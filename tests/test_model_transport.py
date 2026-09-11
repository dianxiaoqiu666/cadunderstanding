import json
from types import SimpleNamespace

import httpx
import pytest

from services.understanding import evidence, model_client
from services.understanding.geometry import InputError
from services.understanding.model_transport import TransportAudit
from test_enrichment import fixture_package, empty_proposal, isolated


@pytest.mark.parametrize('exception,expected_stage,sent', [
    (httpx.ConnectTimeout, 'CONNECTING', False), (httpx.WriteTimeout, 'SENDING_BODY', False),
    (httpx.ReadTimeout, 'WAITING_RESPONSE_HEADERS', True), (httpx.PoolTimeout, 'HTTP_REQUEST_STARTED', False)])
def test_timeouts_record_stage_without_secrets_or_retry(isolated, monkeypatch, exception, expected_stage, sent):
    packet, manifest = evidence.prepare_evidence(fixture_package())
    monkeypatch.setenv('CAD_LLM_API_KEY', 'unit-test-secret')
    config = model_client.LLMConfig(base_url='https://api.test.invalid/v1', model='fixture')
    progress, calls = [], []

    def fail(request):
        calls.append(1)
        trace = request.extensions['trace']
        if exception is httpx.ConnectTimeout:
            trace('connection.connect_tcp.started', {'host': 'unit-test-secret'})
        elif exception in {httpx.WriteTimeout, httpx.ReadTimeout}:
            trace('http11.send_request_headers.started', {'request': SimpleNamespace(method=b'POST')})
            trace('http11.send_request_body.started', {})
            if sent:
                trace('http11.send_request_body.complete', {'return_value': 'unit-test-secret'})
                trace('http11.receive_response_headers.started', {'request': 'unit-test-secret'})
        raise exception('unit-test-secret')

    with pytest.raises(InputError) as caught:
        model_client.call_model(packet, manifest, config=config, transport=httpx.MockTransport(fail), on_transport=progress.append)
    detail = caught.value.diagnostics
    assert detail['error_type'] == exception.__name__
    assert detail['failed_stage'] == expected_stage
    assert detail['http_request_started'] and detail['request_body_sent'] is sent
    assert not detail['response_headers_received'] and not detail['model_response_validated']
    assert len(calls) == 1 and progress[-1] == detail
    assert 'unit-test-secret' not in json.dumps(progress) + str(caught.value)


def test_proxy_connect_does_not_count_as_model_body_sent():
    audit = TransportAudit()
    audit.trace('http11.send_request_headers.started', {'request': SimpleNamespace(method=b'CONNECT')})
    audit.trace('http11.send_request_body.complete', {})
    assert not audit.snapshot()['request_body_sent']
    audit.trace('http11.send_request_headers.started', {'request': SimpleNamespace(method=b'POST')})
    audit.trace('http11.send_request_body.complete', {})
    assert audit.snapshot()['request_body_sent']


def test_http_error_keeps_receipt_but_never_claims_completion(isolated, monkeypatch):
    packet, manifest = evidence.prepare_evidence(fixture_package())
    monkeypatch.setenv('CAD_LLM_API_KEY', 'unit-test-secret')
    config = model_client.LLMConfig(base_url='https://api.test.invalid/v1', model='fixture')
    with pytest.raises(InputError) as caught:
        model_client.call_model(packet, manifest, config=config, transport=httpx.MockTransport(
            lambda request: httpx.Response(429, headers={'x-request-id': 'fixture-429'}, text='unit-test-secret')))
    detail = caught.value.diagnostics
    assert detail['http_status'] == 429 and detail['response_headers_received']
    assert detail['response_request_id'] == 'fixture-429' and not detail['model_response_validated']
    assert 'unit-test-secret' not in json.dumps(detail)


def test_valid_response_records_usage_and_sanitized_receipts(isolated, monkeypatch):
    package = fixture_package()
    packet, manifest = evidence.prepare_evidence(package)
    monkeypatch.setenv('CAD_LLM_API_KEY', 'unit-test-secret')
    config = model_client.LLMConfig(base_url='https://api.test.invalid/v1', model='fixture')
    value = {'id': 'fixture-response-1', 'usage': {'total_tokens': 7},
             'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(empty_proposal(package))}}]}
    _, metadata = model_client.call_model(packet, manifest, config=config, transport=httpx.MockTransport(
        lambda request: httpx.Response(200, headers={'request-id': 'unit-test-secret'}, json=value)))
    assert metadata['response_id'] == 'fixture-response-1' and metadata['usage']['total_tokens'] == 7
    assert metadata['transport']['model_response_validated']
    assert metadata['transport']['transport_kind'] == 'INJECTED'
    assert 'unit-test-secret' not in json.dumps(metadata)


def test_actual_http_trace_on_local_synthetic_server(isolated, monkeypatch):
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from threading import Thread
    package = fixture_package()
    packet, manifest = evidence.prepare_evidence(package)
    monkeypatch.setenv('CAD_LLM_API_KEY', 'synthetic-loopback-key')
    monkeypatch.setenv('NO_PROXY', 'localhost,127.0.0.1')
    calls = []
    reply = json.dumps({'choices': [{'finish_reason': 'stop',
                        'message': {'content': json.dumps(empty_proposal(package))}}]}).encode()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers['Content-Length']))
            calls.append(self.path)
            self.send_response(200)
            self.send_header('Content-Length', str(len(reply)))
            self.send_header('x-request-id', 'local-synthetic-receipt')
            self.end_headers()
            self.wfile.write(reply)

        def log_message(self, *args):
            pass

    server = HTTPServer(('127.0.0.1', 0), Handler)
    worker = Thread(target=lambda: server.serve_forever(poll_interval=.01), daemon=True)
    worker.start()
    try:
        config = model_client.LLMConfig(base_url=f'http://127.0.0.1:{server.server_port}', model='synthetic', timeout_seconds=10)
        _, metadata = model_client.call_model(packet, manifest, config=config)
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)
    audit = metadata['transport']
    assert calls == ['/chat/completions']
    assert audit['request_body_sent'] and audit['response_body_complete'] and audit['model_response_validated']
    assert audit['http_status'] == 200 and audit['response_request_id'] == 'local-synthetic-receipt'
    assert 'synthetic-loopback-key' not in json.dumps(metadata)
