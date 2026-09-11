"""Failed replies must retain receipts without accepting geometry or exposing thinking."""
import json
import sys

import httpx
import pytest

from scripts import evaluate_model
from services.understanding import evidence, model_client
from services.understanding.geometry import InputError
from test_enrichment import empty_proposal, fixture_package, isolated
from test_model_evaluation import evaluation_workspace


@pytest.mark.parametrize('provider,reason', [('anthropic', 'max_tokens'), ('openai_compatible', 'length')])
def test_truncation_retains_model_usage_and_final_text_but_never_accepts_even_valid_json(isolated, monkeypatch, provider, reason):
    package = fixture_package()
    packet, manifest = evidence.prepare_evidence(package)
    final = json.dumps(empty_proposal(package))
    secret, private = 'synthetic-receipt-key', 'private-thinking-must-not-export'
    monkeypatch.setenv('CAD_LLM_API_KEY', secret)
    value = {'model': 'returned-vision-model', 'id': 'receipt-test-1',
             'usage': {'input_tokens': 100, 'output_tokens': 8192, 'api_key': secret, 'total_tokens': True}}
    if provider == 'anthropic':
        value.update(stop_reason=reason, content=[{'type': 'thinking', 'thinking': private + secret},
                                                 {'type': 'text', 'text': final}])
    else:
        value['choices'] = [{'finish_reason': reason, 'message': {'content': final, 'reasoning_content': private + secret}}]
    config = model_client.LLMConfig(provider=provider, base_url='https://api.test.invalid/v1', model='requested-vision-model')
    captured, progress, calls = [], [], []

    def reply(request):
        calls.append(request.url)
        return httpx.Response(200, json=value)

    with pytest.raises(InputError) as caught:
        model_client.call_model(packet, manifest, config=config, transport=httpx.MockTransport(reply),
                                on_response=captured.append, on_transport=progress.append)
    assert caught.value.code == 'LLM_OUTPUT_INCOMPLETE' and len(calls) == 1 and len(captured) == 1
    saved = captured[0]
    assert saved['content'] == final
    assert saved['metadata']['returned_model'] == 'returned-vision-model'
    assert saved['metadata']['response_id'] == 'receipt-test-1'
    assert saved['metadata']['usage'] == {'input_tokens': 100, 'output_tokens': 8192}
    receipt = caught.value.diagnostics['response_metadata']
    assert receipt['stop_reason' if provider == 'anthropic' else 'finish_reason'] == reason
    assert caught.value.diagnostics['response_body_complete']
    assert not caught.value.diagnostics['model_response_validated']
    assert progress[-1] == caught.value.diagnostics
    exported = json.dumps(captured + progress) + str(caught.value)
    assert secret not in exported and private not in exported


@pytest.mark.parametrize('provider', ['anthropic', 'openai_compatible'])
def test_wrong_protocol_records_observed_shape_without_reinterpreting_it(isolated, monkeypatch, provider):
    packet, manifest = evidence.prepare_evidence(fixture_package())
    monkeypatch.setenv('CAD_LLM_API_KEY', 'synthetic-only-key')
    value = ({'choices': [{'finish_reason': 'stop', 'message': {'content': '{}'}}]} if provider == 'anthropic'
             else {'stop_reason': 'end_turn', 'content': [{'type': 'text', 'text': '{}'}]})
    expected = 'chat_completions' if provider == 'anthropic' else 'anthropic_messages'
    captured = []
    config = model_client.LLMConfig(provider=provider, base_url='https://api.test.invalid/v1', model='fixture')
    with pytest.raises(InputError) as caught:
        model_client.call_model(packet, manifest, config=config, on_response=captured.append,
                                transport=httpx.MockTransport(lambda _: httpx.Response(200, json=value)))
    assert caught.value.code == 'LLM_RESPONSE_PROTOCOL_INVALID'
    assert captured[0]['metadata']['response_format'] == expected
    assert not caught.value.diagnostics['model_response_validated']


@pytest.mark.parametrize('reason', ['refusal', 'tool_use'])
def test_nonfinal_stop_is_distinct_from_truncation(isolated, monkeypatch, reason):
    packet, manifest = evidence.prepare_evidence(fixture_package())
    monkeypatch.setenv('CAD_LLM_API_KEY', 'synthetic-only-key')
    config = model_client.LLMConfig(provider='anthropic', base_url='https://api.test.invalid/v1', model='fixture')
    with pytest.raises(InputError) as caught:
        model_client.call_model(packet, manifest, config=config, transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json={'stop_reason': reason, 'content': []})))
    assert caught.value.code == 'LLM_OUTPUT_INCOMPLETE'
    assert caught.value.diagnostics['response_metadata']['stop_reason'] == reason
    assert '输出上限' not in str(caught.value)


def test_http_200_error_envelope_preserves_code_without_raw_error_message(isolated, monkeypatch):
    packet, manifest = evidence.prepare_evidence(fixture_package())
    monkeypatch.setenv('CAD_LLM_API_KEY', 'synthetic-only-key')
    config = model_client.LLMConfig(provider='anthropic', base_url='https://api.test.invalid/v1', model='fixture')
    captured = []
    value = {'error': {'code': 1210, 'type': 'invalid_request_error',
                       'message': 'private-message synthetic-only-key'}, 'id': 'synthetic-error-receipt'}
    with pytest.raises(InputError) as caught:
        model_client.call_model(packet, manifest, config=config, on_response=captured.append,
                                transport=httpx.MockTransport(lambda _: httpx.Response(200, json=value)))
    assert caught.value.code == 'LLM_PROVIDER_ERROR'
    assert captured[0]['metadata']['provider_error_code'] == '1210'
    assert captured[0]['metadata']['response_id'] == 'synthetic-error-receipt'
    assert 'private-message' not in json.dumps(captured) and 'synthetic-only-key' not in str(caught.value)


def test_failed_evaluation_writes_receipt_and_report_without_a_candidate(evaluation_workspace, monkeypatch):
    monkeypatch.setattr(model_client, 'EVIDENCE_ROOT', evidence.EVIDENCE_ROOT)
    monkeypatch.setenv('CAD_LLM_API_KEY', 'synthetic-only-key')
    config = model_client.LLMConfig(provider='anthropic', base_url='https://api.test.invalid/v1', model='fixture')
    monkeypatch.setattr(evaluate_model, 'load_config', lambda: config)
    calls = []

    def reply(request):
        calls.append(request.url)
        return httpx.Response(200, json={'id': 'receipt-failed-evaluation', 'model': 'fixture',
            'stop_reason': 'max_tokens', 'content': [{'type': 'text', 'text': '{"partial":'}],
            'usage': {'input_tokens': 500, 'output_tokens': 8192}})

    def invoke(*args, **kwargs):
        return model_client.call_model(*args, **kwargs, transport=httpx.MockTransport(reply))

    monkeypatch.setattr(evaluate_model, 'call_model', invoke)
    monkeypatch.setattr(sys, 'argv', ['evaluate_model.py', str(evaluation_workspace), '--call-model',
                                    '--run-name', 'failed-receipt'])
    assert evaluate_model.main() == 2 and len(calls) == 1
    folder = evaluation_workspace.parent / 'outputs/model-evaluations/failed-receipt'
    report = json.loads((folder / 'report.json').read_text(encoding='utf-8-sig'))
    captured = json.loads((folder / 'model-final-response.json').read_text(encoding='utf-8-sig'))
    assert report['status'] == 'FAILED' and report['api_calls_attempted'] == 1
    assert report['request_metadata']['stop_reason'] == 'max_tokens'
    assert report['request_metadata']['usage']['output_tokens'] == 8192
    assert captured['content'] == '{"partial":'
    assert not (folder / 'proposal.json').exists()
    assert json.loads((folder / 'package.json').read_text(encoding='utf-8-sig'))['enrichment'] is None
