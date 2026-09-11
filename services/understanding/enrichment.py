"""Validate model references and attach reviewable semantic additions to CAD JSON."""
from __future__ import annotations

from shapely.geometry import LineString
from shapely.ops import unary_union

from services.understanding.contracts import EnrichmentResult, Issue, ParseOptions, SemanticProposal
from services.understanding.evidence import build_evidence, geometry_paths
from services.understanding.geometry import InputError
from services.understanding.scope_quality import assess_scope
from services.understanding.usable_area import area_records, compute_usable_area, polygon_of, union_regions


def enrich_package(package, proposal, *, origin='IMPORTED_PROPOSAL', provenance=None):
    proposal = SemanticProposal.model_validate(proposal)
    evidence = build_evidence(package)
    elements = {e.id: e for e in package.components()}
    vertices = {v['id']: v['point_mm'] for v in evidence['vertices']}
    issues, candidate_polys, check_details = [], [], []

    def fail(code, message, ids=(), **context):
        issues.append(Issue(code=code, message=message, severity='ERROR', element_ids=list(ids)))
        check_details.append({'code': code, 'element_ids': list(ids), **context})

    def refs(ids):
        missing = set(ids) - elements.keys()
        if missing:
            fail('MODEL_ELEMENT_ID_UNKNOWN', '模型引用了本次 CAD 中不存在的实体。', sorted(missing))
        return not missing

    if proposal.source_sha256 != package.source.sha256:
        fail('MODEL_SOURCE_MISMATCH', '模型结果对应另一份 CAD，补全未应用。')
    if proposal.evidence_sha256 != evidence['evidence_sha256']:
        fail('MODEL_EVIDENCE_MISMATCH', '模型结果对应的解析配置或证据版本已变化，补全未应用。')
    role_ids = [p.element_id for p in proposal.role_proposals]
    if len(role_ids) != len(set(role_ids)):
        fail('MODEL_ROLE_CONFLICT', '同一实体出现多条类别补全，无法确定采用哪条。')
    for item in proposal.role_proposals:
        if refs([item.element_id, *item.evidence_element_ids]):
            source = elements[item.element_id]
            if source.type == 'ANNOTATION' and item.proposed_role != 'ANNOTATION':
                fail('MODEL_ANNOTATION_AS_STRUCTURE', '文字/尺寸标注不能直接变成建筑实体。', [source.id])
            if item.proposed_role in {'SPACE', 'HOLE', 'EXCLUSION'} and not source.geometry.polygons:
                fail('MODEL_AREA_WITHOUT_FOOTPRINT', '区域类别必须引用已有有效闭合占地；开口线不能直接改为区域。', [source.id])
    proposed_roles = {p.element_id: p.proposed_role for p in proposal.role_proposals}
    for item in proposal.relation_proposals:
        if not refs([item.from_element_id, item.to_element_id, *item.evidence_element_ids]):
            continue
        if item.from_element_id == item.to_element_id:
            fail('MODEL_SELF_RELATION', '构件关系不能引用自身。', [item.from_element_id])
        src, dst = elements[item.from_element_id], elements[item.to_element_id]
        roles = [proposed_roles.get(e.id, e.type) for e in (src, dst)]
        if item.relation == 'HOSTED_BY' and (roles[0] not in {'DOOR', 'WINDOW'} or roles[1] != 'WALL'):
            fail('MODEL_HOST_ROLE_INVALID', 'HOSTED_BY 必须从门窗引用墙体。', [src.id, dst.id])
        if item.relation == 'LABELS' and src.type != 'ANNOTATION':
            fail('MODEL_LABEL_ROLE_INVALID', 'LABELS 必须从标注引用被说明的构件。', [src.id])
    for item in proposal.unresolved:
        refs(item.element_ids)
    paths = [path for e in elements.values() if e.type != 'ANNOTATION'
             for path in geometry_paths(e.geometry) if len(path) >= 2]
    coverage = unary_union([LineString(p) for p in paths])
    ids = [p.id for p in proposal.indoor_proposals]
    if len(ids) != len(set(ids)):
        fail('MODEL_REGION_ID_CONFLICT', '室内候选区域 ID 重复。')
    authoritative_scope = union_regions(package.usable_area.indoor_regions)
    for item in proposal.indoor_proposals:
        refs(item.evidence_element_ids)
        rings = [item.boundary_vertex_ids, *item.hole_vertex_ids]
        needed = {v for ring in rings for v in ring}
        needed |= {v for g in item.gap_proposals for v in (g.from_vertex_id, g.to_vertex_id)}
        if needed - vertices.keys():
            fail('MODEL_VERTEX_ID_UNKNOWN', f'区域 {item.id} 引用了不存在的顶点编号。',
                 region_id=item.id, vertex_ids=sorted(needed - vertices.keys()))
            continue
        if any(len(ring) < 3 for ring in rings):
            fail('MODEL_RING_INVALID', f'区域 {item.id} 的边界或孔洞不足三个顶点。', region_id=item.id)
            continue
        pairs = []
        for ring in rings:
            clean = ring[:-1] if ring[0] == ring[-1] else ring
            pairs += list(zip(clean, clean[1:] + clean[:1]))
        gaps = {frozenset((g.from_vertex_id, g.to_vertex_id)) for g in item.gap_proposals}
        edge_pairs = {frozenset(p) for p in pairs}
        if gaps - edge_pairs:
            fail('MODEL_GAP_NOT_ON_BOUNDARY', f'区域 {item.id} 的补边声明不属于它的边界。',
                 region_id=item.id, vertex_pairs=[sorted(pair) for pair in gaps - edge_pairs])
        for a, b in pairs:
            edge = LineString([vertices[a], vertices[b]])
            if edge.length == 0:
                fail('MODEL_ZERO_LENGTH_EDGE', f'区域 {item.id} 有重复顶点或零长度边。',
                     region_id=item.id, vertex_ids=[a, b])
            elif edge.difference(coverage).length > 1e-6 and frozenset((a, b)) not in gaps:
                fail('MODEL_UNDECLARED_GAP', f'区域 {item.id} 含源图不存在的连接，必须明确标为待补边：{a} → {b}。',
                     region_id=item.id, vertex_ids=[a, b], uncovered_length_mm=edge.difference(coverage).length)
        try:
            poly = polygon_of({'boundary_mm': [vertices[v] for v in item.boundary_vertex_ids],
                               'holes_mm': [[vertices[v] for v in ring] for ring in item.hole_vertex_ids]})
            if not authoritative_scope.is_empty and not authoritative_scope.covers(poly):
                fail('MODEL_OUTSIDE_DECLARED_SCOPE', f'区域 {item.id} 超出已有明确室内范围或覆盖其孔洞。',
                     region_id=item.id, outside_area_mm2=poly.difference(authoritative_scope).area)
            candidate_polys.append(poly)
        except InputError as exc:
            fail('MODEL_POLYGON_INVALID', f'区域 {item.id} 自交、退化或孔洞无效；没有自动修补。',
                 region_id=item.id, geometry_error_code=exc.code, vertex_ids=item.boundary_vertex_ids)
    if candidate_polys:
        united = unary_union(candidate_polys)
        if sum(p.area for p in candidate_polys) - united.area > max(1e-6, united.area * 1e-12):
            fail('MODEL_REGION_OVERLAP', '模型提出的室内区域相互重叠。')
    scope_quality, scope_issues = assess_scope(evidence, proposal, candidate_polys)
    issues.extend(scope_issues)
    failed = any(i.severity == 'ERROR' for i in issues)
    has_changes = bool(proposal.role_proposals or proposal.indoor_proposals or proposal.relation_proposals)
    output = EnrichmentResult(status='REJECTED' if failed else 'REVIEW_REQUIRED' if has_changes else 'UNRESOLVED',
        origin=origin, proposal=proposal, reference_validation='FAIL' if failed else 'PASS', issues=issues,
        provenance={'evidence_sha256': evidence['evidence_sha256'], 'prompt_version': evidence['prompt_version'],
                    'prompt_sha256': evidence['prompt_sha256'], **(provenance or {}),
                    'scope_quality': scope_quality, 'geometry_checks': check_details})
    if not failed:
        output.indoor_candidates = [record for i, poly in enumerate(candidate_polys)
                                   for record in area_records(poly, f'model-{proposal.indoor_proposals[i].id}')]
        # This copy is used only for the explicitly marked model preview. The
        # source components, official usable area, and Human parameters survive.
        preview_elements = [e.model_copy(deep=True) for e in elements.values()]
        for element in preview_elements:
            if element.id in proposed_roles:
                element.type = proposed_roles[element.id]
                element.confidence = 'CANDIDATE'
                element.evidence.append({'kind': 'MODEL_PROPOSAL'})
        options = ParseOptions.model_validate(package.provenance['options'])
        if candidate_polys:
            options = ParseOptions.model_validate({**options.model_dump(), 'source_sha256': package.source.sha256,
                'usable_area': {**options.usable_area.model_dump(),
                    'indoor_regions': [{'boundary_mm': list(p.exterior.coords),
                                        'holes_mm': [list(r.coords) for r in p.interiors]} for p in candidate_polys]}})
        if candidate_polys or package.usable_area.indoor_regions:
            try:
                preview = compute_usable_area(preview_elements, options, package.source.sha256, package.issues)
                preview.scope_source = 'MODEL_PROPOSAL'
                preview.ready_for_placement = False
                if preview.status == 'READY':
                    preview.status = 'REVIEW_REQUIRED'
                preview.issues.append(Issue(code='MODEL_SEMANTICS_PENDING',
                    message='此区域基于模型推断，几何校验通过不代表室内含义已经确认。'))
                output.usable_area_preview = preview
            except InputError as exc:
                output.issues.append(Issue(code=exc.code, message=exc.message, severity='ERROR'))
                output.status = 'REJECTED'
                output.reference_validation = 'FAIL'
                output.indoor_candidates = []
    result = package.model_copy(deep=True)
    result.enrichment = output
    result.provenance.pop('http_exports', None)
    return result
