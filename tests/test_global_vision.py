import copy
import json

import pytest
from pydantic import ValidationError
from shapely.geometry import box
from shapely.ops import unary_union

from services.understanding import evidence, model_client
from services.understanding.contracts import SemanticProposal
from services.understanding.enrichment import enrich_package
from services.understanding.geometry import InputError
from services.understanding.parser import parse_bytes
from test_enrichment import ROOT, empty_proposal, fixture_package, indoor_proposal


def reviewed_proposal(package):
    packet = evidence.build_evidence(package)
    proposal = empty_proposal(package)
    proposal['schema_version'] = 'cad-semantic-proposal/2.0'
    proposal['indoor_proposals'] = [indoor_proposal(package)]
    proposal['scope_review'] = {
        'extent': 'WHOLE_STORE_CANDIDATE',
        'reviewed_view_ids': [v['id'] for v in packet['views'] if v['image'] in evidence.MODEL_IMAGE_NAMES],
        'summary': 'Synthetic explicit planning scope; not real-store acceptance.',
        'anchor_checks': [{'element_id': anchor.id, 'position_relation': 'INSIDE',
                          'reason': 'Synthetic text anchor inside scope.'} for anchor in package.annotations],
    }
    return proposal


def test_compact_input_keeps_every_source_reference_without_candidate_answer_leakage():
    package = parse_bytes((ROOT / '选定规划只留墙体.dxf').read_bytes())
    full = evidence.build_evidence(package)
    compact = evidence.build_model_input(full)
    assert {e['id'] for e in compact['elements']} == {e.id for e in package.components()}
    assert compact['vertices'] == full['vertices']
    assert len(compact['text_anchors']) == 4
    assert len(compact['unrendered_element_ids']) == 14
    assert len(evidence.json_bytes(compact)) < len(evidence.json_bytes(full)) * .8
    wire = evidence.json_bytes(compact).decode('utf-8')
    assert '紧凑矩形填充可能为柱' not in wire
    vertices = {v['id'] for v in compact['vertices']}
    owners = {e['id'] for e in compact['elements']}
    assert all(s['from'] in vertices and s['to'] in vertices and s['element_id'] in owners
               for s in compact['source_segments'])
    assert {a['element_id'] for a in compact['text_anchors']} <= owners


def test_overlapping_detail_views_cover_whole_drawing_and_keep_vertex_transform(tmp_path, monkeypatch):
    monkeypatch.setattr(evidence, 'EVIDENCE_ROOT', tmp_path)
    package = parse_bytes((ROOT / '选定规划只留墙体.dxf').read_bytes())
    packet, manifest = evidence.prepare_evidence(package)
    details = [v for v in packet['views'] if v['image'] in evidence.FOCUS_IMAGE_NAMES]
    assert unary_union([box(*v['bounds_mm']) for v in details]).equals(box(*packet['coordinate_system']['bounds_mm']))
    points = {v['id']: v['point_mm'] for v in packet['vertices']}
    labelled = set()
    for view in details:
        transform = manifest['rendering']['detail_views'][view['image']]
        for label in transform['labels']['vertices.png']:
            labelled.add(label['id'])
            x, y = points[label['id']]
            assert label['anchor_px'] == pytest.approx([
                transform['left_px'] + (x - transform['bounds_mm'][0]) * transform['scale_px_per_mm'],
                transform['height_px'] - transform['top_px'] - (y - transform['bounds_mm'][1]) * transform['scale_px_per_mm']])
        assert (tmp_path / packet['evidence_sha256'] / view['image']).is_file()
    assert labelled == set(points)
    sent = json.loads((tmp_path / packet['evidence_sha256'] / 'model-input.json').read_text(encoding='utf-8'))
    assert sent == evidence.build_model_input(packet)
    assert len(sent['views']) == 6


def test_v2_requires_scope_review():
    proposal = empty_proposal(fixture_package())
    proposal['schema_version'] = 'cad-semantic-proposal/2.0'
    with pytest.raises(ValidationError):
        SemanticProposal.model_validate(proposal)


def test_global_review_is_candidate_only_even_when_consistent():
    package = fixture_package()
    result = enrich_package(package, reviewed_proposal(package))
    assert result.enrichment.reference_validation == 'PASS'
    quality = result.enrichment.provenance['scope_quality']
    assert quality['model_extent_claim'] == 'WHOLE_STORE_CANDIDATE'
    assert quality['review_consistency'] == 'PASS'
    assert quality['semantic_confirmation'] == 'PENDING'
    assert result.enrichment.usable_area_preview.ready_for_placement is False
    assert result.spaces == package.spaces


@pytest.mark.parametrize('mutation,code', [
    ('missing_view', 'MODEL_VIEW_REVIEW_INCOMPLETE'),
    ('unknown_view', 'MODEL_VIEW_UNKNOWN'),
    ('missing_anchor', 'MODEL_ANCHOR_REVIEW_INCOMPLETE'),
    ('unknown_anchor', 'MODEL_ANCHOR_UNKNOWN'),
    ('duplicate_anchor', 'MODEL_ANCHOR_CONFLICT'),
    ('wrong_position', 'MODEL_ANCHOR_POSITION_CONTRADICTION'),
    ('missing_scope', 'MODEL_WHOLE_SCOPE_MISSING'),
])
def test_false_or_incomplete_global_claim_is_rejected(mutation, code):
    package = fixture_package()
    proposal = reviewed_proposal(package)
    review = proposal['scope_review']
    if mutation == 'missing_view':
        review['reviewed_view_ids'].pop()
    elif mutation == 'unknown_view':
        review['reviewed_view_ids'].append('not-provided')
    elif mutation == 'missing_anchor':
        review['anchor_checks'] = []
    elif mutation == 'unknown_anchor':
        review['anchor_checks'][0]['element_id'] = 'not-in-cad'
    elif mutation == 'duplicate_anchor':
        review['anchor_checks'].append(copy.deepcopy(review['anchor_checks'][0]))
    elif mutation == 'wrong_position':
        review['anchor_checks'][0]['position_relation'] = 'OUTSIDE'
    else:
        proposal['indoor_proposals'] = []
    result = enrich_package(package, proposal)
    assert result.enrichment.reference_validation == 'FAIL'
    assert code in {i.code for i in result.enrichment.issues}
    assert result.enrichment.usable_area_preview is None


def test_uncertain_text_position_does_not_fake_a_geometric_claim():
    package = fixture_package()
    proposal = reviewed_proposal(package)
    proposal['scope_review']['anchor_checks'][0]['position_relation'] = 'UNCERTAIN'
    result = enrich_package(package, proposal)
    quality = result.enrichment.provenance['scope_quality']
    assert quality['anchor_positions'][0]['position_relation'] == 'INSIDE'
    assert quality['anchor_positions'][0]['model_position_relation'] == 'UNCERTAIN'
    assert not result.enrichment.usable_area_preview.ready_for_placement


def test_glm_uses_native_thinking_switch_other_providers_are_unchanged():
    config = model_client.LLMConfig(base_url='https://open.bigmodel.cn/api/paas/v4', model='glm-4.6v')
    _, body = model_client.request_body(config, {}, {})
    assert body['thinking'] == {'type': 'enabled'} and 'enable_thinking' not in body
    config.enable_thinking = False
    assert model_client.request_body(config, {}, {})[1]['thinking']['type'] == 'disabled'
    config.base_url = 'https://api.example.invalid/v1'
    assert 'thinking' not in model_client.request_body(config, {}, {})[1]


def test_input_or_image_changes_are_blocked_before_model_http(tmp_path, monkeypatch):
    monkeypatch.setattr(evidence, 'EVIDENCE_ROOT', tmp_path)
    monkeypatch.setattr(model_client, 'EVIDENCE_ROOT', tmp_path)
    monkeypatch.setenv('CAD_LLM_API_KEY', 'synthetic-only-key')
    packet, manifest = evidence.prepare_evidence(fixture_package())
    (tmp_path / packet['evidence_sha256'] / 'detail-se.png').write_bytes(b'tampered')
    config = model_client.LLMConfig(base_url='https://api.example.invalid/v1', model='synthetic')
    with pytest.raises(InputError) as exc:
        model_client.call_model(packet, manifest, config=config)
    assert exc.value.code == 'EVIDENCE_CACHE_CHANGED'
