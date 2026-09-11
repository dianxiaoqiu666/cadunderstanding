"""Configurable paid API-key clients; secrets never enter exported CAD packages."""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import time
from urllib.parse import urlsplit

import httpx
from pydantic import Field, ValidationError, model_validator

from services.understanding.contracts import Model, SemanticProposal
from services.understanding.evidence import (
    EVIDENCE_ROOT, MODEL_IMAGE_NAMES, PROMPT_PATH, build_model_input, digest, json_bytes,
)
from services.understanding.geometry import InputError
from services.understanding.model_transport import TransportAudit, safe_identifier

ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT / 'runtime/llm.local.json'


class LLMConfig(Model):
    name: str = '自定义模型'
    provider: str = 'openai_compatible'
    base_url: str = ''
    model: str = ''
    api_key_env: str = 'CAD_LLM_API_KEY'
    response_format: str = 'json_schema'
    max_output_tokens: int = Field(default=8192, ge=512, le=32768)
    token_parameter: str = 'max_completion_tokens'
    timeout_seconds: int = Field(default=120, ge=10, le=600)
    reasoning_effort: str | None = None
    enable_thinking: bool | None = None
    settings_managed: bool = False
    api_key_protected: str | None = Field(default=None, repr=False)

    @model_validator(mode='after')
    def check_config(self):
        if self.provider not in {'openai_compatible', 'anthropic'}:
            raise ValueError('unsupported provider')
        if self.response_format not in {'json_schema', 'json_object'}:
            raise ValueError('unsupported response format')
        if self.token_parameter not in {'max_tokens', 'max_completion_tokens'}:
            raise ValueError('unsupported token parameter')
        if self.base_url:
            url = urlsplit(self.base_url)
            if (url.scheme != 'https' and not (url.scheme == 'http' and url.hostname in {'localhost', '127.0.0.1', '::1'})) or not url.hostname or url.username or url.password or url.query or url.fragment:
                raise ValueError('base_url must be an HTTPS API base without credentials/query')
            if url.hostname == 'open.bigmodel.cn' and url.path.startswith('/api/anthropic') and self.provider != 'anthropic':
                raise ValueError('The Zhipu Anthropic endpoint requires the Anthropic protocol')
        return self


def load_config():
    try:
        values = json.loads(CONFIG_PATH.read_text(encoding='utf-8-sig')) if CONFIG_PATH.is_file() else {}
        mapping = {'CAD_LLM_PROVIDER': 'provider', 'CAD_LLM_BASE_URL': 'base_url', 'CAD_LLM_MODEL': 'model',
                   'CAD_LLM_API_KEY_ENV': 'api_key_env'}
        if not values.get('settings_managed'):
            values.update({field: os.environ[name] for name, field in mapping.items() if os.environ.get(name)})
        return LLMConfig.model_validate(values)
    except (OSError, ValueError, ValidationError) as exc:
        raise InputError('LLM_CONFIG_INVALID', '本机 runtime/llm.local.json 或模型环境配置无效。') from exc


def resolve_api_key(config):
    if config.api_key_protected:
        from services.understanding.local_secrets import unprotect
        try:
            value = json.loads(unprotect(base64.b64decode(config.api_key_protected, validate=True)))
            if value['base_url'] != config.base_url or value['provider'] != config.provider:
                raise ValueError('key destination changed')
            return value['api_key']
        except (ValueError, KeyError, TypeError) as exc:
            if isinstance(exc, InputError):
                raise
            raise InputError('MODEL_KEY_STORAGE_ERROR', '本机密钥与接口不匹配，请在模型设置中重新填写 API Key。') from exc
    return None if config.settings_managed else os.environ.get(config.api_key_env)


def config_status():
    try:
        config = load_config()
    except InputError as exc:
        return {'configured': False, 'missing': ['valid_config'], 'message': exc.message}
    missing = [name for name in ('base_url', 'model') if not getattr(config, name)]
    try:
        key_set = bool(resolve_api_key(config))
        key_error = None
    except InputError as exc:
        key_set, key_error = False, exc.message
    if not key_set:
        missing.append('api_key')
    return {'configured': not missing, 'name': config.name, 'provider': config.provider, 'model': config.model,
            'base_url': config.base_url, 'api_key_set': key_set,
            'api_host': urlsplit(config.base_url).hostname, 'missing': missing,
            'message': key_error or ('已保存，解析 CAD 时将自动使用此模型。' if not missing else '尚未配置模型，可先解析 CAD，或在模型设置中填写接口。')}


def api_schema():
    # Providers support JSON Schema subsets. Local Pydantic remains the strict
    # validator, including bounds omitted from the provider's decoding grammar.
    unsupported = {'default', 'minItems', 'maxItems', 'minLength', 'maxLength', 'pattern',
                   'minimum', 'maximum', 'exclusiveMinimum', 'exclusiveMaximum'}

    def strip(value):
        if isinstance(value, list):
            return [strip(v) for v in value]
        if not isinstance(value, dict):
            return value
        value = {k: strip(v) for k, v in value.items() if k not in unsupported}
        if value.get('type') == 'object':
            value['additionalProperties'] = False
            value['required'] = list(value.get('properties', {}))
        return value
    return strip(SemanticProposal.model_json_schema())


def request_path(config):
    if config.provider == 'anthropic':
        return '/messages' if urlsplit(config.base_url).path.rstrip('/').endswith('/v1') else '/v1/messages'
    return '/chat/completions'


def is_zhipu_53(config):
    return (urlsplit(config.base_url).hostname == 'open.bigmodel.cn' and
            config.model.lower().removesuffix('[1m]') in {'glm-5.3', 'glm-5.3-flash'})


def request_body(config, evidence, images):
    prompt = PROMPT_PATH.read_text(encoding='utf-8-sig')
    text = json_bytes(build_model_input(evidence)).decode('utf-8')
    if config.provider == 'anthropic':
        content = [{'type': 'text', 'text': text}]
        for name, image in images.items():
            content += [{'type': 'text', 'text': name}, {'type': 'image', 'source': {
                'type': 'base64', 'media_type': 'image/png', 'data': base64.b64encode(image).decode('ascii')}}]
        body = {'model': config.model, 'system': prompt, 'messages': [{'role': 'user', 'content': content}],
                'max_tokens': config.max_output_tokens}
        if config.response_format == 'json_schema':
            body['output_config'] = {'format': {'type': 'json_schema', 'schema': api_schema()}}
        else:
            # Compatible endpoints may only implement core Messages fields.
            # Carry the contract in the prompt; local validation remains strict.
            body['system'] += '\n只输出一个 JSON 对象，不要 Markdown 代码块。输出 JSON Schema：' + json.dumps(api_schema(), ensure_ascii=False)
        if is_zhipu_53(config):
            # Zhipu's Messages compatibility guide uses output_config.effort;
            # native GLM reasoning_effort/thinking fields belong to Chat Completions.
            body.setdefault('output_config', {})['effort'] = (
                config.reasoning_effort or ('low' if config.enable_thinking is False else 'max'))
        return request_path(config), body
    content = [{'type': 'text', 'text': text}]
    for name, image in images.items():
        content += [{'type': 'text', 'text': name}, {'type': 'image_url', 'image_url': {
            'url': 'data:image/png;base64,' + base64.b64encode(image).decode('ascii')}}]
    body = {'model': config.model, 'messages': [{'role': 'system', 'content': prompt},
            {'role': 'user', 'content': content}], config.token_parameter: config.max_output_tokens}
    if config.response_format == 'json_schema':
        body['response_format'] = {'type': 'json_schema', 'json_schema': {
            'name': 'cad_semantic_proposal', 'strict': True, 'schema': api_schema()}}
    else:
        body['response_format'] = {'type': 'json_object'}
        body['messages'][0]['content'] += '\n输出 JSON Schema：' + json.dumps(api_schema(), ensure_ascii=False)
    if config.reasoning_effort is not None:
        body['reasoning_effort'] = config.reasoning_effort
    if is_zhipu_53(config):
        # Current native GLM-5.3 APIs require thinking and only accept these efforts.
        # Refuse stale advanced options before a paid request instead of silently changing them.
        if config.enable_thinking is False or config.reasoning_effort not in {None, 'low', 'high', 'max'}:
            raise InputError('LLM_CONFIG_INVALID', 'GLM-5.3 系列标准 API 需要开启思考，思考强度仅支持 low、high、max。')
        body['thinking'] = {'type': 'enabled'}
        body['reasoning_effort'] = config.reasoning_effort or 'max'
    elif urlsplit(config.base_url).hostname == 'open.bigmodel.cn' and config.model.lower() in {
            'glm-4.6v', 'glm-4.6v-flash', 'glm-4.6v-flashx', 'glm-5v-turbo'}:
        body['thinking'] = {'type': 'disabled' if config.enable_thinking is False else 'enabled'}
    elif config.enable_thinking is not None:
        body['enable_thinking'] = config.enable_thinking
    return request_path(config), body


def _safe_final_text(text, key):
    # Redact a credential echo even when the provider JSON-escapes characters.
    def scrub(value):
        if isinstance(value, str):
            return value.replace(key, '[REDACTED]')
        if isinstance(value, list):
            return [scrub(item) for item in value]
        if isinstance(value, dict):
            return {scrub(k): scrub(v) for k, v in value.items()}
        return value
    try:
        original = json.loads(text)
        cleaned = scrub(original)
        if original != cleaned:
            return json.dumps(cleaned, ensure_ascii=False)
    except (ValueError, RecursionError):
        pass
    return text.replace(key, '[REDACTED]')


def call_model(evidence, manifest, *, config=None, transport=None, on_response=None, on_transport=None):
    audit = TransportAudit(on_transport, injected=transport is not None)
    config = config or load_config()
    key = resolve_api_key(config)
    if not config.base_url or not config.model or not key:
        raise InputError('LLM_NOT_CONFIGURED', '请先配置模型名称、API 地址和本机 API Key；模型输入已可离线导出。')
    folder = EVIDENCE_ROOT / evidence['evidence_sha256']
    images = {name: (folder / name).read_bytes() for name in MODEL_IMAGE_NAMES}
    if (digest(PROMPT_PATH.read_bytes()) != evidence['prompt_sha256'] or
            any(digest(data) != manifest['artifact_sha256'].get(name) for name, data in images.items()) or
            digest(json_bytes(build_model_input(evidence))) != manifest['artifact_sha256'].get('model-input.json')):
        raise InputError('EVIDENCE_CACHE_CHANGED', '模型实际输入与保存的证据不一致，没有发起请求。')
    path, body = request_body(config, evidence, images)
    headers = {'Content-Type': 'application/json'}
    if config.provider == 'anthropic':
        headers.update({'x-api-key': key, 'anthropic-version': '2023-06-01'})
    else:
        headers['Authorization'] = 'Bearer ' + key
    started = time.monotonic()
    try:
        # No automatic retries or provider failover: one user action, one paid call.
        with httpx.Client(timeout=config.timeout_seconds, follow_redirects=False, transport=transport) as client:
            audit.update('HTTP_REQUEST_STARTED', http_request_started=True)
            with client.stream('POST', config.base_url.rstrip('/') + path, headers=headers, json=body,
                               extensions={'trace': audit.trace}) as response:
                audit.response(response, key)
                if response.status_code != 200:
                    raise InputError('LLM_HTTP_ERROR', f'模型接口返回 HTTP {response.status_code}；请检查模型权限、配置或额度。')
                chunks, length = [], 0
                for chunk in response.iter_bytes():
                    length += len(chunk)
                    if length > 2_000_000:
                        raise InputError('LLM_RESPONSE_TOO_LARGE', '模型响应超过 2 MB，未作为补全使用。')
                    chunks.append(chunk)
                audit.update('RESPONSE_BODY_COMPLETE', response_body_complete=True)
                value = json.loads(b''.join(chunks))
    except InputError as exc:
        raise audit.error(exc.code, exc.message) from exc
    except httpx.TimeoutException as exc:
        phase = {httpx.ConnectTimeout: '连接接口', httpx.WriteTimeout: '发送请求',
                 httpx.ReadTimeout: '等待接口响应', httpx.PoolTimeout: '等待本机连接池'}.get(type(exc), '请求')
        raise audit.error('LLM_TIMEOUT', f'模型{phase}超时，没有取得有效补全，也没有自动重试；原 CAD 解析结果保留。',
                          error_type=type(exc).__name__) from exc
    except httpx.HTTPError as exc:
        raise audit.error('LLM_CONNECTION_ERROR', '模型接口通信失败；原 CAD 解析结果保留。',
                          error_type=type(exc).__name__) from exc
    except (ValueError, UnicodeError) as exc:
        raise audit.error('LLM_RESPONSE_INVALID', '模型接口没有返回有效 JSON 响应。') from exc
    metrics = {'prompt_tokens', 'completion_tokens', 'total_tokens', 'input_tokens', 'output_tokens',
               'cache_read_input_tokens', 'cache_creation_input_tokens'}
    usage = (value.get('usage') or {}) if isinstance(value, dict) else {}
    usage = {k: v for k, v in usage.items() if k in metrics and type(v) is int and v >= 0} if isinstance(usage, dict) else {}
    envelope = value if isinstance(value, dict) else {}
    model = envelope.get('model', '')
    returned_model = model.replace(key, '[REDACTED]')[:200] if isinstance(model, str) else ''
    blocks, choices = envelope.get('content'), envelope.get('choices')
    choice = choices[0] if isinstance(choices, list) and choices and isinstance(choices[0], dict) else {}
    message = choice.get('message') if isinstance(choice.get('message'), dict) else {}
    stop_reason, finish_reason = envelope.get('stop_reason'), choice.get('finish_reason')
    if config.provider == 'anthropic':
        text = ''.join(item['text'] for item in blocks
                       if isinstance(item, dict) and item.get('type') == 'text' and isinstance(item.get('text'), str)) if isinstance(blocks, list) else ''
    else:
        text = message.get('content') if isinstance(message.get('content'), str) else ''
    text = _safe_final_text(text, key)
    error = envelope.get('error') if isinstance(envelope.get('error'), dict) else {}
    error_code = error.get('code')
    error_code = str(error_code) if type(error_code) is int else error_code
    metadata = {'provider': config.provider, 'requested_model': config.model,
                'returned_model': returned_model, 'usage': usage,
                'stop_reason': safe_identifier(stop_reason, key),
                'finish_reason': safe_identifier(finish_reason, key),
                'provider_error_code': safe_identifier(error_code, key),
                'provider_error_type': safe_identifier(error.get('type'), key),
                'response_format': ('ambiguous' if isinstance(blocks, list) and isinstance(choices, list) else
                                    'anthropic_messages' if isinstance(blocks, list) else
                                    'chat_completions' if isinstance(choices, list) else 'unknown'),
                'final_content_present': bool(text), 'refusal_present': bool(message.get('refusal')),
                'elapsed_seconds': round(time.monotonic() - started, 3),
                'transport': audit.snapshot(),
                'model_input_sha256': manifest['artifact_sha256']['model-input.json'],
                'image_sha256': {name: manifest['artifact_sha256'][name] for name in MODEL_IMAGE_NAMES}}
    if isinstance(value, dict):
        response_id = safe_identifier(value.get('id'), key)
        if response_id:
            metadata['response_id'] = response_id
    # Persist the receipt and final-text blocks BEFORE completion/schema checks.
    # Otherwise a truncated or incompatible reply loses its model, usage and reason.
    # Never capture thinking blocks, reasoning_content, raw error messages or headers.
    audit.update('RESPONSE_ENVELOPE_CAPTURED', response_metadata={
        k: v for k, v in metadata.items() if k != 'transport'})
    metadata['transport'] = audit.snapshot()
    if on_response is not None:
        on_response({'metadata': metadata, 'content': text})
    try:
        if not isinstance(value, dict):
            raise InputError('LLM_OUTPUT_INVALID', '模型接口返回的响应不是 JSON 对象，未作为补全使用。')
        if error:
            code = metadata['provider_error_code'] or metadata['provider_error_type'] or '未提供错误码'
            raise InputError('LLM_PROVIDER_ERROR', f'接口返回 HTTP 200，但响应正文包含接口错误（{code}）；没有有效补全。')
        if config.provider == 'anthropic':
            if not isinstance(blocks, list) or not isinstance(stop_reason, str):
                raise InputError('LLM_RESPONSE_PROTOCOL_INVALID', '接口未返回预期的 Anthropic Messages 响应结构，请核对地址与 API 格式。')
            if stop_reason != 'end_turn':
                detail = {'max_tokens': '模型达到输出上限（max_tokens）',
                          'refusal': '模型拒绝完成此请求', 'tool_use': '模型返回工具调用请求',
                          'pause_turn': '模型暂停了本轮输出'}.get(stop_reason, '模型未以 end_turn 正常结束')
                raise InputError('LLM_OUTPUT_INCOMPLETE', detail + '；补全未完成，已保留回执，未自动重试。')
        else:
            if not choice or not isinstance(finish_reason, str):
                raise InputError('LLM_RESPONSE_PROTOCOL_INVALID', '接口未返回预期的 Chat Completions 响应结构，请核对地址与 API 格式。')
            if finish_reason != 'stop' or message.get('refusal'):
                detail = ('模型达到输出上限（length）' if finish_reason == 'length' else
                          '模型拒绝完成此请求' if message.get('refusal') else '模型未以 stop 正常结束')
                raise InputError('LLM_OUTPUT_INCOMPLETE', detail + '；补全未完成，已保留回执，未自动重试。')
        proposal = SemanticProposal.model_validate_json(text)
    except InputError as exc:
        raise audit.error(exc.code, exc.message) from exc
    except ValidationError as exc:
        # Locations/types identify the broken contract without logging raw rejected values.
        fields = [{'path': [str(p).replace(key, '[REDACTED]')[:160] for p in item['loc']],
                   'type': item['type'], 'message': item['msg'].replace(key, '[REDACTED]')[:400]}
                  for item in exc.errors(include_input=False, include_context=False, include_url=False)[:50]]
        audit.update('SCHEMA_VALIDATION_FAILED', schema_errors=fields)
        raise audit.error('LLM_OUTPUT_INVALID', '模型补全不符合约定 Schema；日志已记录错误字段，源构件未改写。') from exc
    except (AttributeError, KeyError, IndexError, TypeError, ValueError) as exc:
        raise audit.error('LLM_OUTPUT_INVALID', '模型补全不符合约定 Schema；没有改写源构件。') from exc
    audit.update('MODEL_RESPONSE_VALIDATED', model_response_validated=True)
    metadata['transport'] = audit.snapshot()
    return proposal, metadata
