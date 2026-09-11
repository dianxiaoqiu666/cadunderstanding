"""Check global-scope claims against evidence without inferring an indoor envelope."""
from shapely.geometry import Point
from shapely.ops import unary_union

from services.understanding.contracts import Issue
from services.understanding.evidence import MODEL_IMAGE_NAMES
from services.understanding.usable_area import polygon_of


def assess_scope(evidence, proposal, candidate_polys):
    review = proposal.scope_review
    scope = unary_union(candidate_polys)
    anchors = {e['id']: e for e in evidence['elements']
               if e['geometry_type'] == 'TEXT' and e['geometry']['points_mm']}
    view_ids = {v['id'] for v in evidence['views'] if v['image'] in MODEL_IMAGE_NAMES}
    issues = []

    def fail(code, message, ids=()):
        issues.append(Issue(code=code, message=message, severity='ERROR', element_ids=list(ids)))

    if review is None:
        issues.append(Issue(code='MODEL_GLOBAL_REVIEW_MISSING',
                            message='此模型结果没有整店范围核对记录，不能判断是否只覆盖局部。'))
    else:
        if set(review.reviewed_view_ids) - view_ids:
            fail('MODEL_VIEW_UNKNOWN', '模型引用了没有提供的图纸视图。')
        checked = [a.element_id for a in review.anchor_checks]
        if len(checked) != len(set(checked)):
            fail('MODEL_ANCHOR_CONFLICT', '同一文字插入点出现重复的范围核对。')
        if set(checked) - anchors.keys():
            fail('MODEL_ANCHOR_UNKNOWN', '范围核对必须引用本次图纸实际可定位的文字实体。',
                 sorted(set(checked) - anchors.keys()))
        if review.extent == 'WHOLE_STORE_CANDIDATE':
            if scope.is_empty:
                fail('MODEL_WHOLE_SCOPE_MISSING', '声称提出整店范围，但没有有效的范围多边形。')
            if view_ids - set(review.reviewed_view_ids):
                fail('MODEL_VIEW_REVIEW_INCOMPLETE', '整店范围候选缺少部分全图或局部视图的核对。')
            if anchors.keys() - set(checked):
                fail('MODEL_ANCHOR_REVIEW_INCOMPLETE', '整店范围候选尚未解释全部可定位文字的位置。',
                     sorted(anchors.keys() - set(checked)))

    anchor_positions = []
    declared = {a.element_id: a for a in review.anchor_checks} if review else {}
    for identifier, e in anchors.items():
        point = Point(e['geometry']['points_mm'][0])
        actual = ('NO_SCOPE' if scope.is_empty else
                  'BOUNDARY' if scope.boundary.distance(point) <= 1e-6 else
                  'INSIDE' if scope.contains(point) else 'OUTSIDE')
        claim = declared.get(identifier)
        if claim and claim.position_relation != 'UNCERTAIN' and claim.position_relation != actual:
            fail('MODEL_ANCHOR_POSITION_CONTRADICTION',
                 f'文字 {identifier} 的插入点实际为 {actual}，与模型声明的 {claim.position_relation} 不一致。',
                 [identifier])
        anchor_positions.append({'element_id': identifier,
                                 'text': e['geometry']['parameters'].get('text', ''),
                                 'position_relation': actual,
                                 'model_position_relation': claim.position_relation if claim else None})
    face_matches = [r['id'] for r in evidence['closed_regions']
                    if not scope.is_empty and scope.equals(polygon_of({
                        'boundary_mm': [next(v['point_mm'] for v in evidence['vertices'] if v['id'] == i)
                                        for i in r['boundary_vertex_ids']],
                        'holes_mm': [[next(v['point_mm'] for v in evidence['vertices'] if v['id'] == i)
                                      for i in ring] for ring in r['hole_vertex_ids']]}))]
    copied = [r.element_id for r in proposal.role_proposals
              if any(r.element_id in c.get('element_ids', []) and r.reason == c.get('reason')
                     for c in evidence['candidates'])]
    if copied:
        issues.append(Issue(code='MODEL_ROLE_REASON_REPEATED', element_ids=copied,
                            message='部分构件理由仅复述输入候选，缺少新增的语义判断依据。'))
    return {'model_extent_claim': review.extent if review else 'NOT_REVIEWED',
            'semantic_confirmation': 'PENDING',
            'review_consistency': 'FAIL' if any(i.severity == 'ERROR' for i in issues) else 'PASS',
            'reviewed_view_ids': review.reviewed_view_ids if review else [],
            'anchor_positions': anchor_positions, 'matching_geometric_face_ids': face_matches,
            'repeated_candidate_reason_ids': copied,
            'gap_count': sum(len(r.gap_proposals) for r in proposal.indoor_proposals),
            'candidate_area_m2': scope.area / 1e6,
            'meaning': 'Checks verify references and stated spatial relations, not whole-store semantic truth.'}, issues
