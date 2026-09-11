import base64
import copy
import io
import json
from pathlib import Path

import ezdxf
from fastapi.testclient import TestClient
import httpx
from PIL import Image
from pydantic import ValidationError
import pytest

from services.understanding import api, evidence, exports, model_client
from services.understanding.api import app
from services.understanding.contracts import SemanticProposal
from services.understanding.enrichment import enrich_package
from services.understanding.geometry import InputError
from services.understanding.parser import parse_bytes

ROOT = Path(__file__).resolve().parents[1]


def fixture_package(*, closed=True):
    doc = ezdxf.new('R2018')
    doc.units = 4
    for layer in ('WALL', 'PLANNING_SCOPE'):
        doc.layers.new(layer)
    msp = doc.modelspace()
    corners = [(0, 0), (10000, 0), (10000, 8000), (0, 8000)]
    if closed:
        msp.add_lwpolyline(corners, close=True, dxfattribs={'layer': 'PLANNING_SCOPE'})
    else:
        for a, b in zip(corners, corners[1:]):
            msp.add_line(a, b, dxfattribs={'layer': 'WALL'})
    msp.add_lwpolyline([(4000, 4000), (4500, 4000), (4500, 4500), (4000, 4500)],
                       close=True, dxfattribs={'layer': 'WALL'})
    msp.add_text('柱', dxfattribs={'insert': (4100, 4100)})
    stream = io.StringIO()
    doc.write(stream)
    return parse_bytes(stream.getvalue().encode('utf-8'))


def empty_proposal(package):
    data = evidence.build_evidence(package)
    return {'schema_version': 'cad-semantic-proposal/1.0', 'source_sha256': package.source.sha256,
            'evidence_sha256': data['evidence_sha256'], 'role_proposals': [], 'indoor_proposals': [],
            'relation_proposals': [], 'unresolved': [], 'scope_review': None}


def indoor_proposal(package, *, gap=False):
    data = evidence.build_evidence(package)
    ids = {tuple(v['point_mm']): v['id'] for v in data['vertices']}
    boundary = [ids[p] for p in [(0, 0), (10000, 0), (10000, 8000), (0, 8000)]]
    return {'id': 'room-1', 'boundary_vertex_ids': boundary, 'hole_vertex_ids': [],
            'gap_proposals': [{'from_vertex_id': boundary[-1], 'to_vertex_id': boundary[0],
                              'reason': '合成测试中的待确认缺边'}] if gap else [],
            'evidence_element_ids': [package.components()[0].id], 'reason': '合成测试范围，非真实门店判断'}


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(evidence, 'EVIDENCE_ROOT', tmp_path / 'evidence')
    monkeypatch.setattr(model_client, 'EVIDENCE_ROOT', tmp_path / 'evidence')
    monkeypatch.setattr(exports, 'RESULTS', tmp_path / 'results')
    monkeypatch.setattr(api, 'RESULTS', tmp_path / 'results')
    monkeypatch.setattr(model_client, 'CONFIG_PATH', tmp_path / 'llm.local.json')
    for key in ['CAD_LLM_PROVIDER', 'CAD_LLM_BASE_URL', 'CAD_LLM_MODEL', 'CAD_LLM_API_KEY_ENV', 'CAD_LLM_API_KEY']:
        monkeypatch.delenv(key, raising=False)
    return tmp_path


def test_actual_cad_json_image_ids_and_pixel_transform_match(isolated):
    package = parse_bytes((ROOT / '选定规划只留墙体.dxf').read_bytes())
    data, manifest = evidence.prepare_evidence(package)
    assert len(data['elements']) == 104
    assert len({e['id'] for e in data['elements']}) == 104
    labels = manifest['rendering']['labels']['entities.png']
    mapping = {e['display_id']: e['id'] for e in data['elements'] if e['rendered']}
    assert {label['label']: label['id'] for label in labels} == mapping
    assert len(manifest['unrendered_element_ids']) == 14
    vlabels = {label['id']: label for label in manifest['rendering']['labels']['vertices.png']}
    render = manifest['rendering']
    for vertex in data['vertices']:
        x, y = vertex['point_mm']
        expected = [render['left_px'] + (x - render['bounds_mm'][0]) * render['scale_px_per_mm'],
                    render['height_px'] - render['top_px'] - (y - render['bounds_mm'][1]) * render['scale_px_per_mm']]
        assert vlabels[vertex['id']]['anchor_px'] == pytest.approx(expected)
    for name in evidence.IMAGE_NAMES:
        path = evidence.EVIDENCE_ROOT / data['evidence_sha256'] / name
        with Image.open(path) as picture:
            assert picture.format == 'PNG' and picture.size == (1800, 1500)
        assert evidence.digest(path.read_bytes()) == manifest['artifact_sha256'][name]
    again, cached = evidence.prepare_evidence(package)
    assert again == data
    assert json.loads(json.dumps(cached)) == json.loads(json.dumps(manifest))


def test_modified_evidence_artifact_is_not_sent(isolated):
    package = fixture_package()
    data, _ = evidence.prepare_evidence(package)
    (evidence.EVIDENCE_ROOT / data['evidence_sha256'] / 'plan.png').write_bytes(b'changed')
    with pytest.raises(InputError) as error:
        evidence.prepare_evidence(package)
    assert error.value.code == 'EVIDENCE_CACHE_CHANGED'


def test_semantic_additions_keep_original_geometry_and_human_state(isolated):
    package = fixture_package()
    before = package.model_dump(mode='json')
    proposal = empty_proposal(package)
    wall = package.walls[0]
    proposal['role_proposals'] = [{'element_id': wall.id, 'proposed_role': 'COLUMN',
        'evidence_element_ids': [wall.id, package.annotations[0].id], 'reason': '测试：闭合轮廓与柱文字对应'}]
    proposal['indoor_proposals'] = [indoor_proposal(package)]
    output = enrich_package(package, proposal)
    assert package.model_dump(mode='json') == before
    assert output.walls == package.walls and output.columns == package.columns
    assert output.enrichment.status == 'REVIEW_REQUIRED'
    assert output.enrichment.reference_validation == 'PASS'
    assert output.enrichment.source_geometry_changed is False
    assert output.enrichment.semantic_confirmation == 'PENDING'
    assert output.enrichment.usable_area_preview.scope_source == 'MODEL_PROPOSAL'
    assert output.enrichment.usable_area_preview.usable_area_m2 == 79.75
    assert not output.enrichment.usable_area_preview.ready_for_placement
    assert output.usable_area == package.usable_area  # official scope/area retained
    assert output.enrichment.provenance['evidence_sha256'] == proposal['evidence_sha256']


@pytest.mark.parametrize('change,code', [('hash', 'MODEL_SOURCE_MISMATCH'), ('configuration', 'MODEL_EVIDENCE_MISMATCH'),
                                      ('element', 'MODEL_ELEMENT_ID_UNKNOWN'), ('vertex', 'MODEL_VERTEX_ID_UNKNOWN')])
def test_wrong_source_interpretation_or_references_are_rejected_atomically(change, code):
    package = fixture_package()
    proposal = empty_proposal(package)
    proposal['indoor_proposals'] = [indoor_proposal(package)]
    if change == 'hash':
        proposal['source_sha256'] = '0' * 64
    elif change == 'configuration':
        package.provenance['options']['usable_area']['obstacle_clearance_mm'] = 100
    elif change == 'element':
        proposal['indoor_proposals'][0]['evidence_element_ids'] = ['entity-missing']
    else:
        proposal['indoor_proposals'][0]['boundary_vertex_ids'][0] = 'V-missing'
    result = enrich_package(package, proposal).enrichment
    assert result.status == 'REJECTED' and result.reference_validation == 'FAIL'
    assert result.indoor_candidates == [] and result.usable_area_preview is None
    assert code in {issue.code for issue in result.issues}


def test_open_boundary_requires_explicit_gap_proposal_and_never_becomes_confirmed():
    package = fixture_package(closed=False)
    proposal = empty_proposal(package)
    proposal['indoor_proposals'] = [indoor_proposal(package)]
    output = enrich_package(package, proposal)
    assert output.enrichment.status == 'REJECTED'
    assert 'MODEL_UNDECLARED_GAP' in {i.code for i in output.enrichment.issues}
    proposal['indoor_proposals'] = [indoor_proposal(package, gap=True)]
    output = enrich_package(package, proposal)
    assert output.enrichment.reference_validation == 'PASS'
    assert output.enrichment.status == 'REVIEW_REQUIRED'
    assert output.usable_area.status == 'NOT_READY'
    assert output.enrichment.usable_area_preview.status == 'REVIEW_REQUIRED'


def test_model_cannot_rewrite_coordinates_or_dimensions():
    proposal = empty_proposal(fixture_package())
    proposal['walls'] = [{'geometry': {'points_mm': [[1, 2], [3, 4]]}}]
    with pytest.raises(ValidationError):
        SemanticProposal.model_validate(proposal)


def test_bad_rings_conflicting_roles_and_self_relations_rejected():
    package = fixture_package()
    proposal = empty_proposal(package)
    region = indoor_proposal(package)
    a, b, c, d = region['boundary_vertex_ids']
    region['boundary_vertex_ids'] = [a, c, b, d]
    proposal['indoor_proposals'] = [region]
    wall = package.walls[0].id
    role = {'element_id': wall, 'proposed_role': 'COLUMN', 'evidence_element_ids': [wall], 'reason': 'test'}
    proposal['role_proposals'] = [role, copy.deepcopy(role)]
    proposal['relation_proposals'] = [{'from_element_id': wall, 'to_element_id': wall, 'relation': 'HOSTED_BY',
                                     'evidence_element_ids': [wall], 'reason': 'test'}]
    result = enrich_package(package, proposal).enrichment
    assert result.status == 'REJECTED'
    assert {'MODEL_POLYGON_INVALID', 'MODEL_ROLE_CONFLICT', 'MODEL_SELF_RELATION'} <= {i.code for i in result.issues}


def test_empty_model_reply_preserves_uncertainty():
    package = fixture_package(closed=False)
    proposal = empty_proposal(package)
    proposal['unresolved'] = [{'element_ids': [], 'question': '缺少边界，无法确定室内。'}]
    result = enrich_package(package, proposal)
    assert result.enrichment.status == 'UNRESOLVED' and not result.enrichment.indoor_candidates
    assert result.usable_area.status == 'NOT_READY'


def test_model_cannot_fill_existing_declared_scope_hole():
    package = fixture_package()
    package.provenance['options']['source_sha256'] = package.source.sha256
    scope = {'boundary_mm': [[0, 0], [10000, 0], [10000, 8000], [0, 8000]],
             'holes_mm': [[[1000, 1000], [2000, 1000], [2000, 2000], [1000, 2000]]]}
    from services.understanding.contracts import ParseOptions
    from services.understanding.usable_area import compute_usable_area
    package.provenance['options']['usable_area']['indoor_regions'] = [scope]
    package.usable_area = compute_usable_area(package.components(),
        ParseOptions.model_validate(package.provenance['options']), package.source.sha256, package.issues)
    proposal = empty_proposal(package)
    proposal['indoor_proposals'] = [indoor_proposal(package)]
    output = enrich_package(package, proposal)
    assert output.enrichment.status == 'REJECTED'
    assert 'MODEL_OUTSIDE_DECLARED_SCOPE' in {i.code for i in output.enrichment.issues}
    assert output.usable_area == package.usable_area


@pytest.mark.parametrize('provider', ['openai_compatible', 'anthropic'])
def test_http_adapter_sends_matching_json_and_png_and_validates_reply(isolated, monkeypatch, provider):
    package = fixture_package()
    packet, manifest = evidence.prepare_evidence(package)
    reply = empty_proposal(package)
    monkeypatch.setenv('CAD_LLM_API_KEY', 'unit-test-paid-api-key')
    config = model_client.LLMConfig(provider=provider, base_url='https://api.test.invalid/v1', model='fixture-model')
    requests = []

    def handler(request):
        requests.append(request)
        body = json.loads(request.content)
        assert body['model'] == 'fixture-model'
        items = body['messages'][-1]['content']
        assert json.loads(items[0]['text']) == evidence.build_model_input(packet)
        pictures = [part for part in items if part['type'] in {'image', 'image_url'}]
        assert len(pictures) == len(evidence.MODEL_IMAGE_NAMES)
        for part in pictures:
            data = part['source']['data'] if provider == 'anthropic' else part['image_url']['url'].split(',', 1)[1]
            assert base64.b64decode(data).startswith(b'\x89PNG\r\n\x1a\n')
        if provider == 'anthropic':
            assert request.headers['x-api-key'] == 'unit-test-paid-api-key'
            assert body['output_config']['format']['type'] == 'json_schema'
            value = {'stop_reason': 'end_turn', 'content': [{'type': 'text', 'text': json.dumps(reply)}]}
        else:
            assert request.headers['authorization'] == 'Bearer unit-test-paid-api-key'
            assert body['response_format']['json_schema']['strict']
            value = {'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(reply)}}]}
        value.update(model='fixture-model', usage={'input_tokens': 10, 'api_key': 'unit-test-paid-api-key'})
        return httpx.Response(200, json=value)

    result, metadata = model_client.call_model(packet, manifest, config=config, transport=httpx.MockTransport(handler))
    assert result.model_dump(mode='json') == reply and len(requests) == 1
    assert metadata['image_sha256'] == {name: manifest['artifact_sha256'][name] for name in evidence.MODEL_IMAGE_NAMES}
    assert 'unit-test-paid-api-key' not in json.dumps(metadata)


@pytest.mark.parametrize('failure,code', [('http', 'LLM_HTTP_ERROR'), ('length', 'LLM_OUTPUT_INCOMPLETE'),
                                        ('json', 'LLM_OUTPUT_INVALID'), ('timeout', 'LLM_TIMEOUT')])
def test_provider_failures_never_fabricate_success_or_retry(isolated, monkeypatch, failure, code):
    packet, manifest = evidence.prepare_evidence(fixture_package())
    monkeypatch.setenv('CAD_LLM_API_KEY', 'unit-test-secret')
    config = model_client.LLMConfig(base_url='https://api.test.invalid/v1', model='fixture-model')
    calls = []

    def handler(request):
        calls.append(request)
        if failure == 'http':
            return httpx.Response(401, text='unit-test-secret')
        if failure == 'timeout':
            raise httpx.ReadTimeout('unit-test-secret')
        return httpx.Response(200, json={'choices': [{'finish_reason': 'length' if failure == 'length' else 'stop',
                                                    'message': {'content': 'not JSON'}}]})
    with pytest.raises(InputError) as error:
        model_client.call_model(packet, manifest, config=config, transport=httpx.MockTransport(handler))
    assert error.value.code == code and len(calls) == 1
    assert 'unit-test-secret' not in str(error.value)


def test_no_model_config_reports_missing_without_calling_network(isolated):
    assert not model_client.config_status()['configured']
    packet, manifest = evidence.prepare_evidence(fixture_package())
    with pytest.raises(InputError) as error:
        model_client.call_model(packet, manifest)
    assert error.value.code == 'LLM_NOT_CONFIGURED'


@pytest.mark.parametrize('provider', ['openai_compatible', 'anthropic'])
def test_malformed_api_envelope_is_reported_without_server_crash(isolated, monkeypatch, provider):
    packet, manifest = evidence.prepare_evidence(fixture_package())
    monkeypatch.setenv('CAD_LLM_API_KEY', 'unit-test-secret')
    config = model_client.LLMConfig(provider=provider, base_url='https://api.test.invalid/v1', model='fixture-model')
    with pytest.raises(InputError) as error:
        model_client.call_model(packet, manifest, config=config,
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json=[None])))
    assert error.value.code == 'LLM_OUTPUT_INVALID'


def test_api_prepare_import_download_and_offline_config(isolated):
    package = exports.save_package(fixture_package())
    identifier = package.provenance['http_exports']['result_id']
    client = TestClient(app)
    assert client.get('/v1/cad/enrichment/config').json()['configured'] is False
    assert len(client.get('/v1/cad/enrichment/providers').json()['providers']) == 4
    prepared = client.post('/v1/cad/enrichment/prepare', json={'result_id': identifier})
    assert prepared.status_code == 200
    links = prepared.json()['links']
    assert client.get(links['entities.png']).headers['content-type'] == 'image/png'
    assert client.get(links['evidence.json']).json()['source_sha256'] == package.source.sha256
    assert client.post('/v1/cad/enrichment/run', json={'result_id': identifier}).status_code == 503
    proposal = empty_proposal(package)
    proposal['indoor_proposals'] = [indoor_proposal(package)]
    response = client.post('/v1/cad/enrichment/import', json={'result_id': identifier, 'proposal': proposal})
    assert response.status_code == 200
    enriched = response.json()
    assert enriched['enrichment']['origin'] == 'IMPORTED_PROPOSAL'
    assert enriched['enrichment']['reference_validation'] == 'PASS'
    assert client.get(enriched['provenance']['http_exports']['enrichment']).json() == enriched['enrichment']
    assert client.get('/v1/cad/enrichment/evidence/invalid/evidence.json').status_code == 404
    assert client.post('/v1/cad/enrichment/prepare', json={'result_id': '0' * 64}).status_code == 404
