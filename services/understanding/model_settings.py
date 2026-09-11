"""One editable model configuration, with an encrypted key and redacted responses."""
import base64
import json
from pathlib import Path
import tempfile
import threading
from typing import Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import Field, SecretStr, ValidationError, field_validator

from services.understanding import model_client
from services.understanding.contracts import Model
from services.understanding.geometry import InputError
from services.understanding.local_secrets import protect

router = APIRouter(prefix='/v1/cad/model-settings', tags=['模型设置'])
_write_lock = threading.Lock()


class ModelSettings(Model):
    name: str = Field(min_length=1, max_length=80)
    base_url: str = Field(min_length=1, max_length=2048)
    provider: Literal['openai_compatible', 'anthropic']
    model: str = Field(min_length=1, max_length=200)
    api_key: SecretStr = SecretStr('')

    @field_validator('name', 'base_url', 'model', mode='before')
    @classmethod
    def trim(cls, value):
        return value.strip() if isinstance(value, str) else value

    @field_validator('api_key')
    @classmethod
    def validate_key(cls, value):
        raw = value.get_secret_value().strip()
        if len(raw) > 4096 or any(ord(char) < 33 or ord(char) > 126 for char in raw):
            raise ValueError('invalid key')
        return SecretStr(raw)


def save_settings(settings):
    settings = ModelSettings.model_validate(settings)
    with _write_lock:
        base = settings.base_url.rstrip('/')
        suffix = '/messages' if settings.provider == 'anthropic' else '/chat/completions'
        if base.endswith(suffix):
            base = base[:-len(suffix)]
        # Validate URLs before touching the existing file or decrypting its key.
        config = model_client.LLMConfig(name=settings.name, base_url=base, model=settings.model,
                                       provider=settings.provider, settings_managed=True)
        try:
            previous = model_client.load_config()
        except InputError:
            previous = model_client.LLMConfig()
        same_destination = (config.base_url, config.provider) == (previous.base_url, previous.provider)
        key = settings.api_key.get_secret_value()
        if not key and same_destination:
            key = model_client.resolve_api_key(previous)
        if not key:
            raise InputError('MODEL_KEY_REQUIRED', '请填写此接口的 API Key；更换 API 地址或格式时也需重新填写。')
        if same_destination and previous.model == config.model:
            # Preserve deliberate advanced settings when editing the same model.
            for name in ('response_format', 'token_parameter', 'max_output_tokens', 'timeout_seconds',
                         'reasoning_effort', 'enable_thinking'):
                setattr(config, name, getattr(previous, name))
        elif config.provider == 'openai_compatible':
            # Generic compatible endpoints commonly implement JSON mode/max_tokens.
            # The official OpenAI/Gemini paths use their stricter existing profiles.
            if urlsplit(base).hostname not in {'api.openai.com', 'generativelanguage.googleapis.com'}:
                config.response_format, config.token_parameter = 'json_object', 'max_tokens'
            if (urlsplit(base).hostname or '').endswith('aliyuncs.com'):
                config.enable_thinking = False
        elif config.provider == 'anthropic' and urlsplit(base).hostname == 'open.bigmodel.cn':
            config.response_format = 'json_object'
        if not (same_destination and previous.model == config.model) and model_client.is_zhipu_53(config):
            config.reasoning_effort = 'max'
            config.enable_thinking = True
            config.timeout_seconds = 300
        protected = protect(json.dumps({'base_url': base, 'provider': config.provider, 'api_key': key}).encode('utf-8'))
        config.api_key_protected = base64.b64encode(protected).decode('ascii')
        target = model_client.CONFIG_PATH
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            # Endpoint and encrypted key change together, never a half-written pair.
            with tempfile.NamedTemporaryFile(dir=target.parent, suffix='.tmp', delete=False, mode='w', encoding='utf-8') as stream:
                temporary = Path(stream.name)
                json.dump(config.model_dump(mode='json'), stream, ensure_ascii=False, indent=2)
                stream.write('\n')
            temporary.replace(target)
        finally:
            if temporary:
                temporary.unlink(missing_ok=True)
        return model_client.config_status()


def local_request(request):
    if request.url.hostname not in {'127.0.0.1', 'localhost', '::1'}:
        raise HTTPException(403, detail={'message': '模型设置仅允许从本机地址访问。'})
    origin = request.headers.get('origin')
    if origin and origin != f'{request.url.scheme}://{request.url.netloc}':
        raise HTTPException(403, detail={'message': '不允许其他网站修改本机模型设置。'})


@router.get('')
def read_settings(request: Request):
    local_request(request)
    return JSONResponse(model_client.config_status(), headers={'Cache-Control': 'no-store'})


@router.put('')
async def write_settings(request: Request):
    local_request(request)
    if request.headers.get('content-type', '').split(';')[0].strip().lower() != 'application/json':
        raise HTTPException(415, detail={'message': '请以 JSON 提交模型设置。'})
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > 16384:
            raise HTTPException(413, detail={'message': '模型设置内容过大。'})
    try:
        settings = ModelSettings.model_validate(json.loads(body))
        value = save_settings(settings)
    except InputError as exc:
        raise HTTPException(422, detail={'code': exc.code, 'message': exc.message}) from None
    except (ValueError, ValidationError):
        # Validation errors may contain submitted keys: never echo raw inputs.
        raise HTTPException(422, detail={'message': '设置格式有误，请检查名称、API 地址、接口格式、模型名和 API Key。'}) from None
    except OSError:
        raise HTTPException(500, detail={'message': '本机模型设置保存失败，原配置保留。'}) from None
    return JSONResponse(value, headers={'Cache-Control': 'no-store'})
