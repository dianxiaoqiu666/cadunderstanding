import io
import json

import ezdxf
from fastapi.testclient import TestClient
import pytest

from services.understanding import api, evidence, exports, model_client, model_settings
from services.understanding.analysis import complete_analysis
from services.understanding.geometry import InputError
from services.understanding.interpretation import interpreted_components
from services.understanding.local_secrets import protect, unprotect
from services.understanding.parser import parse_bytes


@pytest.fixture
def isolated_settings(tmp_path, monkeypatch):
    monkeypatch.setattr(model_client, 'CONFIG_PATH', tmp_path / 'llm.local.json')
    monkeypatch.setattr(evidence, 'EVIDENCE_ROOT', tmp_path / 'evidence')
    monkeypatch.setattr(model_client, 'EVIDENCE_ROOT', tmp_path / 'evidence')
    monkeypatch.setattr(exports, 'RESULTS', tmp_path / 'results')
    monkeypatch.setattr(api, 'RESULTS', tmp_path / 'results')
    for name in ('CAD_LLM_PROVIDER', 'CAD_LLM_MODEL', 'CAD_LLM_BASE_URL', 'CAD_LLM_API_KEY_ENV', 'CAD_LLM_API_KEY'):
        monkeypatch.delenv(name, raising=False)
    return TestClient(api.app, base_url='http://127.0.0.1')


def settings(**changes):
    return {'name': '本机测试', 'base_url': 'https://api.test.invalid/v1',
            'provider': 'openai_compatible', 'model': 'one-vision-model',
            'api_key': 'synthetic-test-key-123', **changes}


def cad_bytes():
    doc = ezdxf.new('R2018')
    doc.units = 4
    doc.layers.new('PLANNING_SCOPE')
    doc.layers.new('WALL')
    doc.modelspace().add_lwpolyline([(0, 0), (10000, 0), (10000, 8000), (0, 8000)],
        close=True, dxfattribs={'layer': 'PLANNING_SCOPE'})
    doc.modelspace().add_lwpolyline([(4000, 4000), (4500, 4000), (4500, 4500), (4000, 4500)],
        close=True, dxfattribs={'layer': 'WALL'})
    stream = io.StringIO()
    doc.write(stream)
    return stream.getvalue().encode('utf-8')


def proposal_for(packet):
    floor = next(e for e in packet['elements'] if e['role'] == 'SPACE')
    wall = next(e for e in packet['elements'] if e['role'] == 'WALL')
    return {'schema_version': 'cad-semantic-proposal/1.0', 'source_sha256': packet['source_sha256'],
        'evidence_sha256': packet['evidence_sha256'],
        'role_proposals': [{'element_id': wall['id'], 'proposed_role': 'COLUMN',
            'evidence_element_ids': [wall['id']], 'reason': '合成测试中的柱'}],
        'indoor_proposals': [{'id': 'synthetic-room', 'boundary_vertex_ids': floor['path_vertex_ids'][0],
            'hole_vertex_ids': [], 'gap_proposals': [], 'evidence_element_ids': [floor['id']],
            'reason': '合成测试房间'}], 'relation_proposals': [], 'unresolved': []}


def test_windows_key_storage_roundtrip_without_plaintext():
    raw = b'synthetic-dpapi-key'
    encrypted = protect(raw)
    assert raw not in encrypted and unprotect(encrypted) == raw


def test_settings_save_reopen_preserve_key_and_do_not_echo(isolated_settings, monkeypatch):
    client = isolated_settings
    response = client.put('/v1/cad/model-settings', json=settings(), headers={'Origin': 'http://127.0.0.1'})
    assert response.status_code == 200
    assert response.json()['configured'] and response.json()['api_key_set']
    stored = model_client.CONFIG_PATH.read_text(encoding='utf-8-sig')
    assert settings()['api_key'] not in stored + response.text
    assert 'api_key_protected' not in response.text
    assert response.headers['cache-control'] == 'no-store'
    assert model_client.resolve_api_key(model_client.load_config()) == settings()['api_key']
    monkeypatch.setenv('CAD_LLM_BASE_URL', 'https://different.invalid/v1')
    monkeypatch.setenv('CAD_LLM_MODEL', 'ignored-env-model')
    monkeypatch.setenv('CAD_LLM_API_KEY', 'ignored-env-key')
    updated = client.put('/v1/cad/model-settings', json=settings(name='修改名称', model='only-one-new-model', api_key=''))
    assert updated.status_code == 200
    assert model_client.resolve_api_key(model_client.load_config()) == settings()['api_key']
    read = client.get('/v1/cad/model-settings').json()
    assert read['model'] == 'only-one-new-model' and read['name'] == '修改名称'
    assert read['base_url'] == settings()['base_url']


@pytest.mark.parametrize('changes', [{'base_url': 'https://different.invalid/v1'}, {'provider': 'anthropic'}])
def test_new_destination_needs_its_own_key_and_retains_existing_config(isolated_settings, changes):
    client = isolated_settings
    assert client.put('/v1/cad/model-settings', json=settings()).status_code == 200
    before = model_client.CONFIG_PATH.read_bytes()
    response = client.put('/v1/cad/model-settings', json=settings(api_key='', **changes))
    assert response.status_code == 422 and response.json()['detail']['code'] == 'MODEL_KEY_REQUIRED'
    assert model_client.CONFIG_PATH.read_bytes() == before


@pytest.mark.parametrize('changes', [{'base_url': 'http://not-loopback.invalid/v1'}, {'provider': 'unknown'},
                                   {'api_key': 'secret\nline'}, {'extra_field': 'private-key'}])
def test_invalid_settings_never_echo_secrets(isolated_settings, changes):
    value = settings(**changes)
    response = isolated_settings.put('/v1/cad/model-settings', json=value)
    assert response.status_code == 422
    assert value['api_key'] not in response.text and 'private-key' not in response.text
    assert not model_client.CONFIG_PATH.exists()


def test_other_websites_cannot_save_settings(isolated_settings):
    response = isolated_settings.put('/v1/cad/model-settings', json=settings(), headers={'Origin': 'https://outside.invalid'})
    assert response.status_code == 403 and not model_client.CONFIG_PATH.exists()
    assert isolated_settings.get('/v1/cad/model-settings', headers={'Host': 'outside.invalid'}).status_code == 403


@pytest.mark.parametrize('provider,suffix', [('openai_compatible', '/chat/completions'), ('anthropic', '/messages')])
def test_full_endpoint_normalized_once(isolated_settings, provider, suffix):
    response = isolated_settings.put('/v1/cad/model-settings', json=settings(
        provider=provider, base_url='https://api.test.invalid/v1' + suffix))
    assert response.status_code == 200
    config = model_client.load_config()
    assert config.base_url == 'https://api.test.invalid/v1'
    path, _ = model_client.request_body(config, {}, {})
    assert config.base_url + path == 'https://api.test.invalid/v1' + suffix


@pytest.mark.parametrize('base', ['https://open.bigmodel.cn/api/anthropic',
                                 'https://open.bigmodel.cn/api/anthropic/v1',
                                 'https://open.bigmodel.cn/api/anthropic/v1/messages'])
def test_zhipu_anthropic_endpoint_has_exactly_one_v1_and_carries_json_contract(isolated_settings, base):
    saved = isolated_settings.put('/v1/cad/model-settings', json=settings(
        provider='anthropic', base_url=base, model='glm-4.6v'))
    assert saved.status_code == 200
    config = model_client.load_config()
    path, body = model_client.request_body(config, {}, {'plan.png': b'synthetic-png'})
    assert config.base_url + path == 'https://open.bigmodel.cn/api/anthropic/v1/messages'
    assert body['model'] == 'glm-4.6v' and config.response_format == 'json_object'
    assert 'output_config' not in body and 'JSON Schema' in body['system']
    assert body['messages'][0]['content'][-1]['type'] == 'image'


def test_zhipu_anthropic_rejects_wrong_protocol_before_saving(isolated_settings):
    response = isolated_settings.put('/v1/cad/model-settings', json=settings(
        base_url='https://open.bigmodel.cn/api/anthropic', provider='openai_compatible'))
    assert response.status_code == 422 and not model_client.CONFIG_PATH.exists()


@pytest.mark.parametrize('provider,base', [
    ('anthropic', 'https://open.bigmodel.cn/api/anthropic'),
    ('openai_compatible', 'https://open.bigmodel.cn/api/paas/v4'),
])
def test_glm53_flash_profile_switch_keeps_key_and_later_name_edit_keeps_options(isolated_settings, provider, base):
    client = isolated_settings
    assert client.put('/v1/cad/model-settings', json=settings(
        provider=provider, base_url=base, model='glm-4.6v')).status_code == 200
    saved = client.put('/v1/cad/model-settings', json=settings(
        provider=provider, base_url=base, model='glm-5.3-flash', api_key=''))
    assert saved.status_code == 200 and saved.json()['model'] == 'glm-5.3-flash'
    config = model_client.load_config()
    assert model_client.resolve_api_key(config) == settings()['api_key']
    assert config.response_format == 'json_object' and config.reasoning_effort == 'max'
    assert config.enable_thinking is True and config.timeout_seconds == 300
    config.reasoning_effort, config.timeout_seconds = 'high', 240
    model_client.CONFIG_PATH.write_text(config.model_dump_json(), encoding='utf-8')
    assert client.put('/v1/cad/model-settings', json=settings(
        provider=provider, base_url=base, model='glm-5.3-flash', api_key='', name='重命名')).status_code == 200
    edited = model_client.load_config()
    assert edited.reasoning_effort == 'high' and edited.timeout_seconds == 240


def test_automatic_analysis_uses_one_model_and_downloads_match_review_view(isolated_settings, monkeypatch):
    client = isolated_settings
    client.put('/v1/cad/model-settings', json=settings())
    calls = []

    def invoke(packet, manifest, *, config, **callbacks):
        calls.append(config.model)
        assert model_client.resolve_api_key(config) == settings()['api_key']
        return proposal_for(packet), {'requested_model': config.model}

    monkeypatch.setattr(model_client, 'call_model', invoke)
    raw = cad_bytes()
    response = client.post('/v1/cad/analyze', files={'file': ('synthetic.dxf', raw)})
    assert response.status_code == 200
    result = response.json()
    assert calls == ['one-vision-model'] and result['provenance']['analysis']['status'] == 'COMPLETED'
    assert result['enrichment']['origin'] == 'MODEL_API'
    assert result['enrichment']['usable_area_preview']['usable_area_m2'] == 79.75
    assert not result['enrichment']['usable_area_preview']['ready_for_placement']
    assert settings()['api_key'] not in response.text
    baseline = parse_bytes(raw, 'synthetic.dxf').model_dump(mode='json')
    for name in ('walls', 'columns', 'usable_area', 'inventory', 'topology'):
        assert result[name] == baseline[name]
    links = result['provenance']['http_exports']
    display = client.get(links['interpreted-components']).json()
    column = next(e for e in display if e['type'] == 'COLUMN')
    assert column['confidence'] == 'CANDIDATE' and column['evidence'][-1]['semantic_confirmation'] == 'PENDING'
    assert client.get(links['interpreted-area']).json() == result['enrichment']['usable_area_preview']
    placement = client.post('/v1/cad/placement-check', json={'result_id': links['result_id'],
        'footprint': {'boundary_mm': [[1000, 1000], [1500, 1000], [1500, 1500], [1000, 1500]], 'holes_mm': []}}).json()
    assert placement['decision'] == 'REVIEW_REQUIRED' and placement['allowed'] is False
    assert 'MODEL_SEMANTICS_PENDING' in placement['reasons']
    assert client.get(links['components']).json() == [e.model_dump(mode='json') for e in parse_bytes(raw, 'synthetic.dxf').components()]


def test_failed_or_unconfigured_analysis_keeps_parse_without_retry(isolated_settings, monkeypatch):
    client = isolated_settings
    raw = cad_bytes()
    calls = []

    def invoke(*args, **kwargs):
        calls.append(1)
        raise InputError('LLM_TIMEOUT', '请求超时')

    monkeypatch.setattr(model_client, 'call_model', invoke)
    without = client.post('/v1/cad/analyze', files={'file': ('fixture.dxf', raw)}).json()
    assert without['provenance']['analysis']['status'] == 'NOT_CONFIGURED' and calls == []
    client.put('/v1/cad/model-settings', json=settings())
    failed = client.post('/v1/cad/analyze', files={'file': ('fixture.dxf', raw)}).json()
    assert calls == [1] and failed['provenance']['analysis']['status'] == 'FAILED'
    assert failed['enrichment'] is None and failed['status'] == 'PARTIAL'
    assert failed['walls'] == without['walls']


def test_rejected_proposal_never_changes_integrated_export(isolated_settings, monkeypatch):
    isolated_settings.put('/v1/cad/model-settings', json=settings())
    def invoke(packet, manifest, *, config, **callbacks):
        proposal = proposal_for(packet)
        proposal['source_sha256'] = '0' * 64
        return proposal, {}
    monkeypatch.setattr(model_client, 'call_model', invoke)
    package = parse_bytes(cad_bytes())
    result = complete_analysis(package.model_copy(deep=True))
    assert result.provenance['analysis']['status'] == 'REJECTED'
    assert interpreted_components(result) == package.components()


def test_model_panel_is_replaced_by_one_settings_form(isolated_settings):
    page = isolated_settings.get('/').text
    assert 'id="modelSettingsForm"' in page and 'id="modelId"' in page
    for removed in ('id="modelPanel"', 'id="runModel"', 'id="proposalFile"', 'id="providerList"', '/enrichment.js'):
        assert removed not in page
    assert '/v1/cad/analyze' in page
    assert isolated_settings.get('/model-settings.js').status_code == 200


def test_parse_rejection_explicitly_reports_no_model_call(isolated_settings, monkeypatch):
    from fastapi import HTTPException
    def rejected(*args):
        raise HTTPException(422, detail={'code': 'AREA_TOPOLOGY_INVALID', 'message': 'Fixture geometry rejected.'})
    def forbidden(*args, **kwargs):
        raise AssertionError('Rejected geometry must never reach model invocation')
    monkeypatch.setattr(api, 'extract', rejected)
    monkeypatch.setattr(model_client, 'call_model', forbidden)
    response = isolated_settings.post('/v1/cad/analyze', files={'file': ('fixture.dxf', cad_bytes())})
    detail = response.json()['detail']
    assert response.status_code == 422 and detail['model_call_started'] is False
    assert detail['stage'] == 'CAD_PARSE' and '未进入模型请求' in detail['message']


def test_failed_analysis_preserves_transport_diagnostics(isolated_settings, monkeypatch):
    from services.understanding.model_transport import ModelCallError
    isolated_settings.put('/v1/cad/model-settings', json=settings())
    detail = {'http_request_started': True, 'response_headers_received': False, 'failed_stage': 'CONNECTING'}
    def failed(*args, **kwargs):
        raise ModelCallError('LLM_TIMEOUT', 'Fixture connection timeout.', detail)
    monkeypatch.setattr(model_client, 'call_model', failed)
    result = isolated_settings.post('/v1/cad/analyze', files={'file': ('fixture.dxf', cad_bytes())}).json()
    assert result['provenance']['analysis']['transport'] == detail
    assert result['provenance']['analysis']['status'] == 'FAILED' and result['enrichment'] is None
