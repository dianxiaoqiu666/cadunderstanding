import json
from pathlib import Path
import sys

import pytest

from scripts import evaluate_model
from services.understanding import evidence
from services.understanding.contracts import CadPackage, SemanticProposal
from services.understanding.model_client import LLMConfig
from services.understanding.parser import parse_bytes
from test_global_vision import reviewed_proposal
from test_model_settings import cad_bytes


@pytest.fixture
def evaluation_workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(evaluate_model, 'ROOT', tmp_path)
    monkeypatch.setattr(evaluate_model, 'EVIDENCE_ROOT', tmp_path / 'evidence-cache')
    monkeypatch.setattr(evidence, 'EVIDENCE_ROOT', tmp_path / 'evidence-cache')
    source = tmp_path / 'synthetic.dxf'
    source.write_bytes(cad_bytes())
    return source


def test_preparation_never_loads_credentials_or_calls_model(evaluation_workspace, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('prepare-only must not use credentials or a model')
    monkeypatch.setattr(evaluate_model, 'call_model', forbidden)
    monkeypatch.setattr(evaluate_model, 'load_config', forbidden)
    monkeypatch.setattr(sys, 'argv', ['evaluate_model.py', str(evaluation_workspace), '--run-name', 'prepare'])
    assert evaluate_model.main() == 0
    output = evaluation_workspace.parent / 'outputs/model-evaluations/prepare'
    report = json.loads((output / 'report.json').read_text(encoding='utf-8'))
    assert report['status'] == 'PREPARED' and report['api_calls_attempted'] == 0
    assert (output / 'index.html').is_file() and (output / 'evidence/model-input.json').is_file()
    with pytest.raises(SystemExit):
        evaluate_model.main()


def test_different_source_baseline_blocks_before_model_call(evaluation_workspace, monkeypatch):
    package = parse_bytes(evaluation_workspace.read_bytes())
    package.source.sha256 = '0' * 64
    baseline = evaluation_workspace.with_suffix('.json')
    baseline.write_text(package.model_dump_json(), encoding='utf-8')
    monkeypatch.setattr(sys, 'argv', ['evaluate_model.py', str(evaluation_workspace), '--call-model',
                                    '--baseline', str(baseline), '--run-name', 'different'])
    with pytest.raises(SystemExit) as error:
        evaluate_model.main()
    assert error.value.code == 2
    assert not (evaluation_workspace.parent / 'outputs/model-evaluations/different').exists()


def test_single_call_evaluation_freezes_inputs_and_candidate_output(evaluation_workspace, monkeypatch):
    package = parse_bytes(evaluation_workspace.read_bytes(), evaluation_workspace.name)
    calls = []
    config = LLMConfig(base_url='https://api.test.invalid/v1', model='fixture')
    monkeypatch.setattr(evaluate_model, 'load_config', lambda: config)

    def invoke(packet, manifest, *, config, on_response, on_transport):
        calls.append(config.model)
        reply = reviewed_proposal(package)
        metadata = {'requested_model': config.model, 'usage': {'total_tokens': 12}}
        on_transport({'http_request_started': True, 'transport_kind': 'INJECTED', 'http_status': 200})
        on_response({'metadata': metadata, 'content': json.dumps(reply)})
        return SemanticProposal.model_validate(reply), metadata

    monkeypatch.setattr(evaluate_model, 'call_model', invoke)
    monkeypatch.setattr(sys, 'argv', ['evaluate_model.py', str(evaluation_workspace), '--call-model',
                                    '--model', 'single-test-model', '--run-name', 'call'])
    assert evaluate_model.main() == 0
    output = evaluation_workspace.parent / 'outputs/model-evaluations/call'
    report = json.loads((output / 'report.json').read_text(encoding='utf-8'))
    result = CadPackage.model_validate_json((output / 'package.json').read_text(encoding='utf-8'))
    assert calls == ['single-test-model'] and report['api_calls_attempted'] == 1
    assert report['human_acceptance'] == 'PENDING'
    assert report['semantic_accuracy'] == 'NOT_MEASURED_WITHOUT_APPROVED_REFERENCE'
    assert not result.enrichment.usable_area_preview.ready_for_placement
    assert result.source.sha256 == package.source.sha256
    assert (output / 'model-final-response.json').is_file()
    assert (output / 'evidence/detail-se.png').is_file()
    assert (output / 'transport.json').is_file()


def test_config_failure_records_zero_http_attempts(evaluation_workspace, monkeypatch):
    from services.understanding.geometry import InputError
    def invalid():
        raise InputError('LLM_CONFIG_INVALID', 'Invalid fixture config')
    monkeypatch.setattr(evaluate_model, 'load_config', invalid)
    monkeypatch.setattr(sys, 'argv', ['evaluate_model.py', str(evaluation_workspace), '--call-model',
                                    '--run-name', 'config-failure'])
    assert evaluate_model.main() == 2
    output = evaluation_workspace.parent / 'outputs/model-evaluations/config-failure'
    report = json.loads((output / 'report.json').read_text(encoding='utf-8'))
    assert report['status'] == 'FAILED' and report['api_calls_attempted'] == 0


def test_report_escapes_model_text(evaluation_workspace):
    package = parse_bytes(evaluation_workspace.read_bytes())
    from services.understanding.enrichment import enrich_package
    proposal = reviewed_proposal(package)
    proposal['scope_review']['summary'] = '<script>not_allowed()</script>'
    result = enrich_package(package, proposal)
    packet = evidence.build_evidence(package)
    _, page = evaluate_model.render_report(result, packet,
        {'run_name': 'synthetic', 'prompt_version': 'test', 'status': 'REVIEW_REQUIRED'})
    assert '<script>not_allowed()' not in page
    assert '&lt;script&gt;not_allowed()&lt;/script&gt;' in page
