"""Positive indoor scope minus sourced footprints, with full-footprint validation.

No hull, bounding box, room-size heuristic or automatic opening closure determines
indoors. Line barriers remain separate: subtracting a zero-width line from a
polygon cannot prevent a downstream rectangle from crossing it.
"""
from __future__ import annotations

import math

from shapely.geometry import LineString, Point, Polygon
from shapely.ops import unary_union

from services.understanding.contracts import (
    AreaPolygon, Barrier, Issue, Obstacle, PlacementResult, UsableArea,
)
from services.understanding.geometry import InputError, polygon_data

FLOOR_LAYERS = {'PLANNING_SCOPE', 'HUMAN_PLANNING_SCOPE', 'FLOOR_BOUNDARY', '范围'}


def polygon_of(value):
    value = value.model_dump() if hasattr(value, 'model_dump') else value
    try:
        rings = [value['boundary_mm'], *value.get('holes_mm', [])]
        if any(len(ring) < 3 or len(ring) > 20000 for ring in rings):
            raise ValueError('ring length')
        if any(not math.isfinite(v) for ring in rings for point in ring for v in point):
            raise ValueError('coordinate')
        poly = Polygon(rings[0], rings[1:])
        if poly.is_empty or not poly.is_valid or not math.isfinite(poly.area) or poly.area <= 0:
            raise ValueError('invalid polygon')
        return poly
    except (ValueError, TypeError, KeyError) as exc:
        raise InputError('REGION_INVALID', '区域须为有效的简单多边形；不能自交、退化或含越界/交叠孔洞。') from exc


def polygons(geometry):
    if geometry.is_empty:
        return []
    if geometry.geom_type == 'Polygon':
        return [geometry] if geometry.area > 0 else []
    return [p for part in getattr(geometry, 'geoms', ()) for p in polygons(part)]


def lines(geometry):
    if geometry.is_empty:
        return []
    if geometry.geom_type in {'LineString', 'LinearRing'}:
        return [list(geometry.coords)]
    return [p for part in getattr(geometry, 'geoms', ()) for p in lines(part)]


def area_records(geometry, prefix):
    return [AreaPolygon(id=f'{prefix}-{i}', **polygon_data(poly),
                        area_mm2=poly.area, area_m2=poly.area / 1e6)
            for i, poly in enumerate(sorted(polygons(geometry), key=lambda p: (p.bounds, p.area)))]


def union_regions(regions):
    return unary_union([polygon_of(p) for p in regions])


def margin(geometry, distance):
    # Circumscribe round buffer chords so positive requested clearances are not
    # shaved off by the polygonal approximation. No fabricated wall thickness.
    return geometry if distance == 0 else geometry.buffer(
        distance / math.cos(math.pi / 128), quad_segs=32, join_style='round')


def circle_footprint(geom):
    radius = geom.parameters['radius_mm']
    tolerance = geom.approximation_tolerance_mm or 0.5
    angle = math.acos(radius / (radius + tolerance))
    if angle <= 0:
        raise InputError('AREA_COMPLEXITY_LIMIT', '圆柱占地在当前精度下超出计算上限。')
    quadrant = max(8, math.ceil(math.pi / (4 * angle)))
    if quadrant > 5000:
        raise InputError('AREA_COMPLEXITY_LIMIT', '圆柱占地在当前精度下超出计算上限。')
    return Point(geom.parameters['center_mm']).buffer(
        radius / math.cos(math.pi / (4 * quadrant)), quad_segs=quadrant)


def resolve_scope(elements, options):
    if options.usable_area.indoor_regions:
        parts = [polygon_of(p) for p in options.usable_area.indoor_regions]
        source, ids = 'SOURCE_BOUND_USER', []
    else:
        spaces = [e for e in elements if e.type == 'SPACE']
        scopes = [e for e in spaces if e.layer.upper() in FLOOR_LAYERS]
        if len(scopes) > 1:
            return None, 'NONE', [], 'INDOOR_SCOPE_AMBIGUOUS'
        selected = scopes or spaces
        if not selected:
            return None, 'NONE', [], 'INDOOR_SCOPE_REQUIRED'
        parts = [polygon_of(p) for e in selected for p in e.geometry.polygons]
        source = 'EXPLICIT_CAD_SCOPE' if scopes else 'EXPLICIT_CAD_ROOMS'
        ids = [e.id for e in selected]
    if not parts:
        return None, 'NONE', [], 'INDOOR_SCOPE_REQUIRED'
    scope = unary_union(parts)
    # Touching rooms may join; overlapping positive room claims are ambiguous.
    if sum(p.area for p in parts) - scope.area > max(1e-6, scope.area * 1e-12):
        return None, 'NONE', [], 'INDOOR_SCOPE_OVERLAP'
    return scope, source, ids, None


def compute_usable_area(elements, options, source_hash, source_issues):
    policy = options.usable_area
    out = UsableArea(source_sha256=source_hash,
                     boundary_clearance_mm=policy.boundary_clearance_mm,
                     obstacle_clearance_mm=policy.obstacle_clearance_mm)
    scope, out.scope_source, out.scope_element_ids, missing = resolve_scope(elements, options)
    if missing:
        out.issues.append(Issue(code=missing, message={
            'INDOOR_SCOPE_REQUIRED': '尚未声明室内范围。请在平面图上圈定室内，或在 CAD 中提供闭合的房间/规划范围。',
            'INDOOR_SCOPE_AMBIGUOUS': '存在多个整店范围，无法确定采用哪一个；请明确圈定本次室内范围。',
            'INDOOR_SCOPE_OVERLAP': '声明的室内区域相互重叠；请使用不重叠的房间范围。',
        }[missing], severity='ERROR'))
        return out
    out.indoor_regions = area_records(scope, 'indoor')
    out.indoor_area_m2 = scope.area / 1e6
    if any(i.severity == 'ERROR' for i in source_issues):
        out.issues.append(Issue(code='INCOMPLETE_OBSTACLE_GEOMETRY', severity='ERROR',
            message='源几何含未解决错误，可能漏掉障碍物；本次不发布可用区域。'))
        return out
    if len(elements) > 10000:
        out.issues.append(Issue(code='AREA_COMPLEXITY_LIMIT', severity='ERROR',
            message='区域约束超过 10000 个源构件上限，本次不发布部分计算结果。'))
        return out
    cuts = []
    for item in elements:
        if item.type in {'ANNOTATION', 'SPACE'}:
            continue
        geom = item.geometry
        footprint, path, basis = None, None, 'SOURCE_FOOTPRINT'
        if geom.polygons:
            footprint = union_regions(geom.polygons)
        elif geom.type == 'CIRCLE' and item.type in {'COLUMN', 'WALL', 'EXCLUSION', 'UNKNOWN'}:
            footprint = circle_footprint(geom)
            basis = 'CONSERVATIVE_CIRCLE_FOOTPRINT'
        elif geom.closed and len(geom.points_mm) >= 4:
            footprint = margin(polygon_of({'boundary_mm': geom.points_mm}),
                               geom.approximation_tolerance_mm or 0)
            basis = 'CONSERVATIVE_CURVE_FOOTPRINT'
        elif len(geom.points_mm) >= 2:
            path = LineString(geom.points_mm)
            dim = item.dimensions
            if item.type == 'WALL' and dim.thickness_mm and dim.line_reference != 'UNKNOWN':
                if dim.line_reference == 'CENTERLINE':
                    footprint = path.buffer(dim.thickness_mm / 2, cap_style='flat', join_style='mitre')
                else:
                    # LEFT_FACE means solid lies on the right of directed line.
                    sign = -1 if dim.line_reference == 'LEFT_FACE' else 1
                    footprint = path.buffer(sign * dim.thickness_mm, single_sided=True, join_style='mitre')
                footprint = margin(footprint, geom.approximation_tolerance_mm or 0)
                basis = 'EXPLICIT_WALL_DIMENSIONS'
        elif geom.points_mm and scope.intersects(Point(geom.points_mm[0])):
            out.issues.append(Issue(code='OBSTACLE_FOOTPRINT_UNKNOWN', element_ids=[item.id],
                source_handles=item.source_handles, message='室内点状对象缺少占地轮廓，需要补充后才能批准摆放。'))
        if footprint is not None:
            reserved = margin(footprint, policy.obstacle_clearance_mm).intersection(scope)
            if not reserved.is_empty and reserved.area > 0:
                cuts.append(reserved)
                out.obstacles.append(Obstacle(element_id=item.id, role=item.type,
                    source_handles=item.source_handles, basis=basis,
                    polygons=area_records(reserved, f'obstacle-{item.id}')))
                if item.type in {'DOOR', 'WINDOW', 'BEAM'}:
                    out.issues.append(Issue(code='SPECIAL_COMPONENT_FOOTPRINT_REVIEW', element_ids=[item.id],
                        message='门窗/梁的平面轮廓已保守预留，仍需核实开合范围或离地高度。'))
        if path is not None:
            clipped = path.intersection(scope)
            nearby = margin(path, policy.obstacle_clearance_mm).intersection(scope)
            if not clipped.is_empty:
                out.barriers.append(Barrier(element_id=item.id, role=item.type,
                    source_handles=item.source_handles, paths_mm=lines(clipped), footprint_known=footprint is not None))
            if footprint is None and (not clipped.is_empty or not nearby.is_empty):
                out.issues.append(Issue(code='LINE_OBSTACLE_EXTENT_UNKNOWN', element_ids=[item.id],
                    source_handles=item.source_handles,
                    message='墙线或其他线状对象的占地宽度未确认；已保留不可跨越线，区域须核实后用于摆放。'))
                if policy.obstacle_clearance_mm > 0 and nearby.area > 0:
                    cuts.append(nearby)
                    out.obstacles.append(Obstacle(element_id=item.id, role=item.type,
                        source_handles=item.source_handles, basis='USER_CLEARANCE_AROUND_LINE',
                        polygons=area_records(nearby, f'line-margin-{item.id}')))
    inner = margin(scope, -policy.boundary_clearance_mm)
    blocked = unary_union(cuts)
    free = inner.difference(blocked)
    # Keep every connected component and all holes; no area-based island filter.
    out.regions = area_records(free, 'usable')
    out.excluded_regions = area_records(scope.difference(free), 'excluded')
    out.usable_area_m2 = sum(p.area_mm2 for p in out.regions) / 1e6
    out.excluded_area_m2 = sum(p.area_mm2 for p in out.excluded_regions) / 1e6
    if not free.is_empty and (not scope.covers(free) or free.intersection(blocked).area > 1e-6):
        raise InputError('AREA_TOPOLOGY_INVALID', '可用区域未通过室内包含与障碍相离校验。')
    if not out.regions:
        out.status = 'EMPTY'
    else:
        out.status = 'REVIEW_REQUIRED' if out.issues else 'READY'
    out.ready_for_placement = out.status == 'READY'
    return out


def check_placement(package, footprint_input):
    from services.understanding.interpretation import interpreted_area
    footprint = polygon_of(footprint_input)
    area = interpreted_area(package)
    reasons, collisions = [], set()
    if area.status == 'NOT_READY':
        return PlacementResult(decision='NOT_READY', allowed=False, geometric_fit=False,
            reasons=['USABLE_AREA_NOT_READY'], colliding_element_ids=[], source_sha256=package.source.sha256)
    scope, free = union_regions(area.indoor_regions), union_regions(area.regions)
    if not scope.covers(footprint):
        reasons.append('OUTSIDE_INDOOR_SCOPE')
    if not free.covers(footprint):
        reasons.append('OUTSIDE_USABLE_REGION')
    for obstacle in area.obstacles:
        if union_regions(obstacle.polygons).intersects(footprint):
            collisions.add(obstacle.element_id)
    for barrier in area.barriers:
        if any(LineString(path).intersects(footprint) for path in barrier.paths_mm):
            collisions.add(barrier.element_id)
    if collisions:
        reasons.append('OBSTACLE_OR_BARRIER_COLLISION')
    geometric_fit = not reasons
    if reasons:
        decision = 'INVALID'
    elif not area.ready_for_placement:
        decision = 'REVIEW_REQUIRED'
        reasons.append('MODEL_SEMANTICS_PENDING' if area.scope_source == 'MODEL_PROPOSAL' else 'UNRESOLVED_OBSTACLE_EXTENTS')
    else:
        decision = 'VALID'
    return PlacementResult(decision=decision, allowed=decision == 'VALID', geometric_fit=geometric_fit,
        reasons=reasons, colliding_element_ids=sorted(collisions), source_sha256=package.source.sha256)
