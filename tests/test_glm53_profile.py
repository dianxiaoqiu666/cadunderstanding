"""Current official GLM request contracts, with no external calls or real credentials."""
import base64
import json

import httpx
import pytest

from services.understanding import evidence, model_client
from services.understanding.geometry import InputError
from test_enrichment import fixture_package
from test_global_vision import reviewed_proposal


@pytest.mark.parametrize('provider,base', [
    ('anthropic', 'https://open.bigmodel.cn/api/anthropic'),
    ('openai_compatible', 'https://open.bigmodel.cn/api/paas/v4'),
])
def test_glm53_flash_sends_all_six_images_and_validates_final_json(tmp_path, monkeypatch, provider, base):
    monkeypatch.setattr(evidence, 'EVIDENCE_ROOT', tmp_path)
    monkeypatch.setattr(model_client, 'EVIDENCE_ROOT', tmp_path)
    monkeypatch.setenv('CAD_LLM_API_KEY', 'synthetic-glm53-key')
    package = fixture_package()
    packet, manifest = evidence.prepare_evidence(package)
    proposal = reviewed_proposal(package)
    calls = []
    config = model_client.LLMConfig(provider=provider, base_url=base, model='glm-5.3-flash',
                                   response_format='json_object', token_parameter='max_tokens')

    def respond(request):
        calls.append(request)
        body = json.loads(request.content)
        assert body['model'] == 'glm-5.3-flash' and body['max_tokens'] == 8192
        assert 'enable_thinking' not in body
        content = body['messages'][-1]['content']
        assert json.loads(content[0]['text'])['source_sha256'] == package.source.sha256
        expected = [(tmp_path / packet['evidence_sha256'] / name).read_bytes() for name in evidence.MODEL_IMAGE_NAMES]
        if provider == 'anthropic':
            assert str(request.url) == base + '/v1/messages'
            assert request.headers['x-api-key'] == 'synthetic-glm53-key'
            assert body['output_config'] == {'effort': 'max'}
            assert 'reasoning_effort' not in body and 'thinking' not in body
            assert 'JSON Schema' in body['system']
            actual = [base64.b64decode(p['source']['data']) for p in content if p['type'] == 'image']
            reply = {'id': 'synthetic-msg-53', 'model': 'glm-5.3-flash', 'stop_reason': 'end_turn',
                     'usage': {'input_tokens': 100, 'output_tokens': 80},
                     'content': [{'type': 'text', 'text': json.dumps(proposal)}]}
        else:
            assert str(request.url) == base + '/chat/completions'
            assert request.headers['authorization'] == 'Bearer synthetic-glm53-key'
            assert body['reasoning_effort'] == 'max' and body['thinking'] == {'type': 'enabled'}
            assert body['response_format'] == {'type': 'json_object'} and 'output_config' not in body
            actual = [base64.b64decode(p['image_url']['url'].split(',', 1)[1]) for p in content if p['type'] == 'image_url']
            reply = {'id': 'synthetic-msg-53', 'model': 'glm-5.3-flash',
                     'usage': {'prompt_tokens': 100, 'completion_tokens': 80, 'total_tokens': 180},
                     'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(proposal)}}]}
        assert len(actual) == 6 and actual == expected
        return httpx.Response(200, json=reply)

    result, metadata = model_client.call_model(packet, manifest, config=config, transport=httpx.MockTransport(respond))
    assert len(calls) == 1 and result.scope_review.extent == 'WHOLE_STORE_CANDIDATE'
    assert metadata['returned_model'] == 'glm-5.3-flash'
    assert metadata['transport']['transport_kind'] == 'INJECTED'
    assert metadata['transport']['model_response_validated'] is True


@pytest.mark.parametrize('options', [{'enable_thinking': False}, {'reasoning_effort': 'medium'}])
def test_native_glm53_rejects_stale_options_before_any_http(options):
    config = model_client.LLMConfig(base_url='https://open.bigmodel.cn/api/paas/v4',
                                   model='glm-5.3-flash', **options)
    with pytest.raises(InputError, match='GLM-5.3'):
        model_client.request_body(config, {}, {})


def test_anthropic_glm53_effort_does_not_replace_structured_output_or_affect_other_hosts():
    config = model_client.LLMConfig(provider='anthropic', base_url='https://open.bigmodel.cn/api/anthropic',
                                   model='GLM-5.3-Flash[1m]', reasoning_effort='high')
    body = model_client.request_body(config, {}, {})[1]
    assert body['output_config']['effort'] == 'high'
    assert body['output_config']['format']['type'] == 'json_schema'
    config.base_url = 'https://api.test.invalid/v1'
    assert 'effort' not in model_client.request_body(config, {}, {})[1]['output_config']
