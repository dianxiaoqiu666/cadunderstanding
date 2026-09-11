"""Offline end-to-end failure/review checks; no external model or real credential."""
import json

import httpx
from fastapi.testclient import TestClient
import pytest

from scripts import evaluate_model, model_review
from services.understanding import analysis, api, evidence, model_client, model_runs
from services.understanding.enrichment import enrich_package
from services.understanding.geometry import InputError
from test_enrichment import fixture_package, isolated
from test_global_vision import reviewed_proposal
from test_model_evaluation import evaluation_workspace


def saved_json(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def hashes(folder):
    return {p.relative_to(folder).as_posix(): evidence.digest(p.read_bytes())
            for p in folder.rglob('*') if p.is_file()}


def invoke_fixture(monkeypatch, package, content, *, stop='end_turn', failure=None):
    config = model_client.LLMConfig(provider='anthropic', base_url='https://api.test.invalid', model='synthetic-vision')
    key = 'synthetic-review-key'
    monkeypatch.setattr(model_client, 'load_config', lambda: config)
    monkeypatch.setattr(model_client, 'resolve_api_key', lambda config: key)
    calls = []

    def reply(request):
        calls.append(request.url.path)
        if failure == 'timeout':
            raise httpx.ReadTimeout('fixture timeout', request=request)
        if failure == 'http':
            return httpx.Response(403, json={'error': {'message': key}})
        return httpx.Response(200, json={'id': 'synthetic-receipt', 'model': 'synthetic-vision',
            'stop_reason': stop, 'usage': {'input_tokens': 8, 'output_tokens': 2}, 'content': [
                {'type': 'thinking', 'thinking': 'private-thinking-must-not-be-saved'},
                {'type': 'text', 'text': content}]})

    original = model_client.call_model
    monkeypatch.setattr(model_client, 'call_model', lambda packet, manifest, **kw:
                        original(packet, manifest, transport=httpx.MockTransport(reply), **kw))
    result = analysis.complete_analysis(package)
    folder = model_runs.run_folder(result.provenance['analysis']['diagnostics']['run_id'])
    return result, folder, calls


@pytest.mark.parametrize('content,stop,failure,code', [
    ('not JSON', 'end_turn', None, 'LLM_OUTPUT_INVALID'),
    ('{}', 'end_turn', None, 'LLM_OUTPUT_INVALID'),
    ('{"unresolved":', 'max_tokens', None, 'LLM_OUTPUT_INCOMPLETE'),
    ('', 'end_turn', 'timeout', 'LLM_TIMEOUT'),
    ('', 'end_turn', 'http', 'LLM_HTTP_ERROR'),
])
def test_failed_model_stages_are_logged_without_retry(isolated, monkeypatch, content, stop, failure, code):
    package = fixture_package()
    before = package.model_dump(mode='json')
    result, folder, calls = invoke_fixture(monkeypatch, package, content, stop=stop, failure=failure)
    record, feedback = saved_json(folder / 'run.json'), saved_json(folder / 'feedback.json')
    assert len(calls) == 1 and record['model_calls_this_run'] == 1
    assert record['transport']['transport_kind'] == 'INJECTED'
    assert result.provenance['analysis']['code'] == code == feedback['error']['code']
    assert record['status'] == 'FAILED' and result.enrichment is None
    assert result.walls == package.walls and result.spaces == package.spaces
    assert saved_json(folder / 'base-package.json') == before
    assert not feedback['auto_retry'] and feedback['semantic_accuracy'] is None
    assert (folder / 'review.png').read_bytes().startswith(b'\x89PNG')
    if not failure:
        receipt = saved_json(folder / 'model-response.json')
        assert receipt['metadata']['stop_reason'] == stop and receipt['content'] == content
        assert receipt['metadata']['usage']['output_tokens'] == 2
        if stop == 'end_turn':
            assert feedback['schema_errors']
    readable = ''.join(p.read_text(encoding='utf-8') for p in folder.rglob('*.json'))
    assert 'synthetic-review-key' not in readable and 'private-thinking-must-not-be-saved' not in readable


def test_rejected_edge_has_source_vertices_measurement_and_review_plot(isolated, monkeypatch):
    package = fixture_package()
    proposal = reviewed_proposal(package)
    proposal['indoor_proposals'][0]['boundary_vertex_ids'].pop(1)
    result, folder, _ = invoke_fixture(monkeypatch, package, json.dumps(proposal))
    feedback = saved_json(folder / 'feedback.json')
    check = next(c for c in feedback['geometry_checks'] if c['code'] == 'MODEL_UNDECLARED_GAP')
    assert result.provenance['analysis']['status'] == 'REJECTED'
    assert check['region_id'] == 'room-1' and len(check['vertex_ids']) == 2
    assert check['uncovered_length_mm'] > 10_000
    assert result.enrichment.usable_area_preview is None
    from PIL import Image
    with Image.open(folder / 'review.png') as picture:
        assert picture.size == (1800, 1500)
        assert (229, 44, 52) in picture.get_flattened_data()  # Unsupported source edge highlighted.
    assert result.walls == package.walls and result.spaces == package.spaces


def test_model_unresolved_stays_visible_and_is_not_completed(isolated, monkeypatch):
    package = fixture_package()
    proposal = reviewed_proposal(package)
    proposal['indoor_proposals'] = []
    proposal['scope_review']['extent'] = 'UNRESOLVED'
    proposal['scope_review']['anchor_checks'][0]['position_relation'] = 'UNCERTAIN'
    proposal['unresolved'] = [{'element_ids': [], 'question': '入口缺边需要原图核对。'}]
    result, folder, _ = invoke_fixture(monkeypatch, package, json.dumps(proposal))
    feedback = saved_json(folder / 'feedback.json')
    assert result.provenance['analysis']['status'] == feedback['status'] == 'UNRESOLVED'
    assert feedback['category'] == 'MODEL_UNRESOLVED' and feedback['unresolved'] == proposal['unresolved']
    assert feedback['proposed_indoor_count'] == 0


def test_valid_candidate_redacts_escaped_key_in_all_artifacts_and_keeps_pending(isolated, monkeypatch):
    package = fixture_package()
    proposal = reviewed_proposal(package)
    proposal['scope_review']['summary'] = 'synthetic-review-key <script>bad()</script>'
    proposal['unresolved'] = [{'element_ids': [], 'question': '<script>bad()</script>'}]
    text = json.dumps(proposal).replace('synthetic-review-key', '\\u0073ynthetic-review-key')
    result, folder, _ = invoke_fixture(monkeypatch, package, text)
    assert result.provenance['analysis']['status'] == 'COMPLETED'
    assert result.enrichment.usable_area_preview.ready_for_placement is False
    record = saved_json(folder / 'run.json')
    assert record['semantic_confirmation'] == 'PENDING' and record['semantic_accuracy'] is None
    assert 'synthetic-review-key' not in result.model_dump_json()
    receipt = saved_json(folder / 'model-response.json')
    assert 'synthetic-review-key' not in json.dumps(json.loads(receipt['content']))
    page = (folder / 'index.html').read_text(encoding='utf-8')
    assert '<script>bad()' not in page and '&lt;script&gt;bad()&lt;/script&gt;' in page
    assert record['review_prompt']['active_base_prompt_changed'] is False
    for name, sha in saved_json(folder / 'files.json')['sha256'].items():
        assert evidence.digest((folder / name).read_bytes()) == sha


def test_cannot_start_model_without_writable_diagnostics(isolated, monkeypatch):
    blocker = isolated / 'read-only-log-root'
    blocker.write_text('not a directory', encoding='utf-8')
    monkeypatch.setattr(model_runs, 'runs_root', lambda: blocker)
    monkeypatch.setattr(model_client, 'load_config', lambda: pytest.fail('No model setup before logging is available'))
    result = analysis.complete_analysis(fixture_package())
    assert result.provenance['analysis']['code'] == 'MODEL_LOG_WRITE_FAILED'
    assert result.enrichment is None


@pytest.mark.parametrize('entry', ['analyze', 'enrichment'])
def test_unexpected_program_error_has_code_location_without_exception_secret(isolated, monkeypatch, entry):
    from services.understanding import enrichment_api
    package = fixture_package()
    config = model_client.LLMConfig(provider='anthropic', base_url='https://api.test.invalid', model='synthetic')
    monkeypatch.setattr(model_client, 'load_config', lambda: config)
    monkeypatch.setattr(model_client, 'resolve_api_key', lambda config: 'synthetic-review-key')
    monkeypatch.setattr(enrichment_api, 'load_config', lambda: config)
    monkeypatch.setattr(enrichment_api, 'load_package', lambda identifier: package)

    def broken(*args, **kwargs):
        raise RuntimeError('synthetic-review-key must not appear in a traceback message')

    monkeypatch.setattr(model_client, 'call_model', broken)
    monkeypatch.setattr(enrichment_api, 'call_model', broken)
    if entry == 'analyze':
        result = analysis.complete_analysis(package)
        failure = result.provenance['analysis']
        assert result.enrichment is None
    else:
        client = TestClient(api.app, base_url='http://127.0.0.1')
        response = client.post('/v1/cad/enrichment/run', json={'result_id': 'a' * 64})
        assert response.status_code == 500
        failure = response.json()['detail']
    assert failure['code'] == 'MODEL_PIPELINE_ERROR'
    folder = model_runs.run_folder(failure['diagnostics']['run_id'])
    record = saved_json(folder / 'run.json')
    assert record['error']['stack'][-1]['function'] == 'broken'
    assert record['model_calls_this_run'] == 0
    assert 'synthetic-review-key' not in json.dumps(record)


def test_offline_revision_preserves_parent_and_cannot_adopt_scope(isolated, monkeypatch):
    package = fixture_package()
    packet, manifest = evidence.prepare_evidence(package)
    parent = model_runs.ModelRun(package, origin='IMPORTED_PROPOSAL')
    parent.attach_evidence(packet, manifest)
    wrong = reviewed_proposal(package)
    wrong['indoor_proposals'][0]['boundary_vertex_ids'].pop(1)
    parent.finish(enrich_package(package, wrong))
    original = hashes(parent.folder)
    path = isolated / 'revision.json'
    path.write_text(json.dumps(reviewed_proposal(package)), encoding='utf-8')
    monkeypatch.setattr(model_client, 'load_config', lambda: pytest.fail('Offline review must not use credentials'))
    monkeypatch.setattr(model_client, 'call_model', lambda *a, **k: pytest.fail('Offline review must not call a model'))
    child = model_review.validate_revision(parent.id, path)
    assert child.id != parent.id and child.record['parent_run_id'] == parent.id
    assert child.record['status'] == 'REVIEW_REQUIRED' and child.record['model_calls_this_run'] == 0
    assert not child.record['area']['ready_for_placement']
    assert hashes(parent.folder) == original
    summary = model_runs.summarize_runs()
    assert summary['run_count'] == 2 and summary['semantic_accuracy'] is None
    assert summary['counts_by_error']['MODEL_UNDECLARED_GAP'] == 1
    assert {r['origin'] for r in summary['runs']} == {'IMPORTED_PROPOSAL', 'OFFLINE_REVISION'}


@pytest.mark.parametrize('mutation', ['schema', 'source', 'evidence_version'])
def test_offline_revision_rejects_wrong_contract_or_source(isolated, mutation):
    package = fixture_package()
    packet, manifest = evidence.prepare_evidence(package)
    parent = model_runs.ModelRun(package)
    parent.attach_evidence(packet, manifest)
    parent.fail(InputError('LLM_OUTPUT_INVALID', 'fixture'))
    original = hashes(parent.folder)
    proposal = reviewed_proposal(package)
    if mutation == 'schema':
        proposal.pop('source_sha256')
    elif mutation == 'source':
        proposal['source_sha256'] = '0' * 64
    else:
        # An earlier prompt/evidence version is a separate comparison, not a same-input revision.
        record = saved_json(parent.folder / 'run.json')
        record['evidence_sha256'] = '0' * 64
        model_runs.write_json(parent.folder / 'run.json', record)
        original = hashes(parent.folder)
    path = isolated / 'bad-revision.json'
    path.write_text(json.dumps(proposal), encoding='utf-8')
    if mutation == 'evidence_version':
        with pytest.raises(InputError, match='当前提示词或证据版本已变化'):
            model_review.validate_revision(parent.id, path)
    else:
        child = model_review.validate_revision(parent.id, path)
        assert child.record['model_calls_this_run'] == 0
        if mutation == 'schema':
            assert child.record['status'] == 'FAILED' and child.record['error']['code'] == 'LLM_OUTPUT_INVALID'
        else:
            assert child.record['status'] == 'REJECTED'
            assert 'MODEL_SOURCE_MISMATCH' in {issue['code'] for issue in child.record['issues']}
    assert hashes(parent.folder) == original


def test_historical_missing_receipt_is_preserved_without_repeating_call(evaluation_workspace, monkeypatch):
    import sys
    monkeypatch.setattr(sys, 'argv', ['evaluate_model.py', str(evaluation_workspace), '--run-name', 'historic'])
    assert evaluate_model.main() == 0
    folder = evaluation_workspace.parent / 'outputs/model-evaluations/historic'
    report = saved_json(folder / 'report.json')
    report.update(status='FAILED', api_calls_attempted=1, requested_model='historical-requested-model', provider='anthropic',
                  error={'code': 'LLM_OUTPUT_INCOMPLETE', 'message': 'fixture incomplete result'},
                  transport={'http_status': 200, 'response_body_complete': True})
    model_runs.write_json(folder / 'report.json', report)
    original = hashes(folder)
    monkeypatch.setattr(model_review, 'ROOT', evaluation_workspace.parent)
    monkeypatch.setattr(model_client, 'load_config', lambda: pytest.fail('Historical import must not load credentials'))
    record = model_review.import_evaluation('historic')
    assert record.record['source_trial_calls'] == 1 and record.record['model_calls_this_run'] == 0
    assert record.record['source_transport']['http_status'] == 200
    assert record.record['response_missing'] and not record.record.get('response_metadata')
    assert record.record['status'] == 'FAILED' and hashes(folder) == original


def test_diagnostic_report_serves_only_allowlisted_files(isolated):
    record = model_runs.ModelRun(fixture_package())
    record.fail(InputError('LLM_TIMEOUT', 'fixture timeout'))
    client = TestClient(api.app, base_url='http://127.0.0.1')
    page = client.get(record.links()['report_url'])
    assert page.status_code == 200 and page.headers['cache-control'] == 'no-store'
    assert "default-src 'none'" in page.headers['content-security-policy']
    assert client.get(f'/v1/cad/model-runs/{record.id}/llm.local.json').status_code == 404
    assert client.get(f'/v1/cad/model-runs/{record.id}/%2e%2e%2fllm.local.json').status_code == 404
    assert client.get('/v1/cad/model-runs/invalid/run.json').status_code == 404


def test_import_web_receipt_retains_token_limit_and_input_provenance(isolated, monkeypatch):
    from services.understanding import exports
    package = fixture_package()
    packet, manifest = evidence.prepare_evidence(package)
    metadata = {'requested_model': 'synthetic-vision', 'returned_model': 'synthetic-vision', 'provider': 'anthropic',
        'stop_reason': 'max_tokens', 'usage': {'output_tokens': 8192}, 'final_content_present': False,
        'model_input_sha256': manifest['artifact_sha256']['model-input.json'],
        'image_sha256': {name: manifest['artifact_sha256'][name] for name in evidence.MODEL_IMAGE_NAMES}}
    package.provenance['analysis'] = {'status': 'FAILED', 'code': 'LLM_OUTPUT_INCOMPLETE', 'message': 'fixture limit',
        'transport': {'http_request_started': True, 'http_status': 200, 'response_metadata': metadata}}
    from services.understanding.contracts import Issue
    package.issues.append(Issue(code='LLM_OUTPUT_INCOMPLETE', message='fixture limit'))
    source = exports.RESULTS / ('b' * 64 + '.package.json')
    source.parent.mkdir(exist_ok=True)
    source.write_text(package.model_dump_json(), encoding='utf-8')
    before = source.read_bytes()
    monkeypatch.setattr(model_client, 'call_model', lambda *a, **k: pytest.fail('Import must not call a model'))
    record = model_review.import_result('b' * 64)
    assert record.record['response_metadata'] == metadata
    assert record.record['model_calls_this_run'] == 0 and record.record['source_trial_calls'] == 1
    assert source.read_bytes() == before and not (record.folder / 'model-response.json').exists()
    package.provenance['analysis']['transport']['response_metadata']['model_input_sha256'] = '0' * 64
    source.write_text(package.model_dump_json(), encoding='utf-8')
    with pytest.raises(InputError, match='旧回执未能绑定'):
        model_review.import_result('b' * 64)
