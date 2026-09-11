"""Evidence-based CAD extraction, independent of products or layout planning."""
from __future__ import annotations

from collections import Counter
import hashlib
import math
import re
from typing import Any

import ezdxf
from ezdxf.lldxf.tagwriter import TagCollector
from ezdxf.path import make_path
from shapely.geometry import LineString, Point as ShapelyPoint, Polygon
from shapely.ops import polygonize_full

from services.understanding.cad_io import read_cad
from services.understanding.contracts import (
    CadPackage, Dimensions, Element, Geometry, GROUPS, Issue, ParseOptions, Source,
)
from services.understanding.geometry import InputError, check_plane, hatch_polygons, polygon_data
from services.understanding.scope_review import node_linework
from services.understanding.door_gaps import find_door_gaps
from services.understanding.usable_area import compute_usable_area

MAX_ENTITIES = 50000
MAX_CURVE_POINTS = 20000
UNIT_SCALES = {1: 25.4, 2: 304.8, 4: 1.0, 5: 10.0, 6: 1000.0, 7: 1000000.0,
               8: 0.0000254, 9: 0.0254, 10: 914.4, 11: 1e-7, 12: 1e-6,
               13: 0.001, 14: 100.0, 15: 10000.0, 16: 100000.0, 21: 1200000 / 3937}
ASSUMED_SCALES = {'mm': 1.0, 'cm': 10.0, 'm': 1000.0, 'in': 25.4, 'ft': 304.8}
ROLE_WORDS = {
    'WALL': ('墙', '墙体', '墙线', '外墙', '内墙', 'WALL', 'WALLS'),
    'DOOR': ('门', '门窗门', '入口', '出口', '出入口', 'DOOR', 'DOORS', 'ENTRANCE', 'EXIT'),
    'WINDOW': ('窗', '窗户', 'WINDOW', 'WINDOWS', 'GLAZING'),
    'COLUMN': ('柱', '柱子', '柱体', '结构柱', 'COLUMN', 'COLUMNS', 'COL', 'PILLAR'),
    'BEAM': ('梁', '梁体', 'BEAM', 'BEAMS'),
    'STAIR': ('楼梯', '楼梯间', 'STAIR', 'STAIRS', 'STAIRCASE'),
    'SPACE': ('范围', '房间', '空间', 'PLANNING_SCOPE', 'HUMAN_PLANNING_SCOPE',
              'FLOOR_BOUNDARY', 'ROOM', 'ROOMS', 'SPACE', 'SPACES'),
    'EXCLUSION': ('禁放区', '障碍物', 'EXCLUSION', 'EXCLUSION_ZONE', 'NO_PLACEMENT'),
    'HOLE': ('孔洞', '洞口', 'PLANNING_HOLE', 'PLANNING_HOLES', 'FLOOR_HOLE'),
}
FLOOR_LAYERS = {'PLANNING_SCOPE', 'HUMAN_PLANNING_SCOPE', 'FLOOR_BOUNDARY', '范围'}
ANNOTATION_TYPES = {'TEXT', 'MTEXT', 'ATTRIB', 'ATTDEF', 'DIMENSION', 'LEADER', 'MLEADER'}


def role_from_name(name: str):
    name = name.strip().upper()
    # Match complete English tokens, Chinese terms and conventional CAD prefixes.
    matches = set()
    for role, words in ROLE_WORDS.items():
        for word in words:
            if name == word or re.search(r'(^|[-_\s$])' + re.escape(word) + r'($|[-_\s$\d])', name):
                matches.add(role)
    return next(iter(matches)) if len(matches) == 1 else 'UNKNOWN'


def raw_value(value: Any):
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise InputError('NONFINITE_DXF_VALUE', '源实体包含非有限数值。')
        return value
    if isinstance(value, bytes):
        return {'hex': value.hex()}
    if isinstance(value, dict):
        return {str(k): raw_value(v) for k, v in value.items()}
    try:
        return [raw_value(v) for v in value]
    except TypeError:
        return str(value)


def raw_tags(entity, version):
    try:
        return [[t.code, raw_value(t.value)] for t in TagCollector.dxftags(entity, dxfversion=version)]
    except InputError:
        raise
    except Exception:
        return []


def xy(value, scale):
    values = [float(v) for v in value]
    if len(values) < 2 or not all(math.isfinite(v) for v in values):
        raise InputError('INVALID_COORDINATE', '坐标缺失或不是有限数值。')
    if len(values) > 2 and abs(values[2] * scale) > 1e-7:
        raise InputError('NON_PLANAR_GEOMETRY', '当前平面理解支持 WCS Z=0；非平面实体已保留为未知对象。')
    return (values[0] * scale, values[1] * scale)


def polygon_scaled(poly, scale):
    data = polygon_data(poly)
    return {'boundary_mm': [[x * scale, y * scale] for x, y in data['boundary_mm']],
            'holes_mm': [[[x * scale, y * scale] for x, y in ring] for ring in data['holes_mm']]}


def geometry_of(entity, scale, tolerance):
    kind = entity.dxftype()
    check_plane(entity)
    if kind == 'LINE':
        points = [xy(entity.dxf.start, scale), xy(entity.dxf.end, scale)]
        if points[0] == points[1]:
            raise InputError('ZERO_LENGTH_LINE', '零长度线不能用于构件或空间拓扑。')
        return Geometry(type='LINE', points_mm=points, parameters={'length_mm': math.dist(*points)})
    if kind in {'TEXT', 'MTEXT', 'ATTRIB', 'ATTDEF'}:
        text = entity.plain_text() if hasattr(entity, 'plain_text') else entity.dxf.text
        return Geometry(type='TEXT', points_mm=[xy(entity.dxf.insert, scale)],
                        parameters={'text': text, 'rotation_deg': entity.dxf.get('rotation', 0)})
    if kind in ANNOTATION_TYPES:
        return Geometry(type='UNSUPPORTED', parameters={'annotation_type': kind,
                        'attributes': raw_value(entity.dxf.all_existing_dxf_attribs())})
    if kind == 'POINT':
        return Geometry(type='POINT', points_mm=[xy(entity.dxf.location, scale)])
    if kind == 'HATCH':
        polys = [polygon_scaled(p, scale) for p in hatch_polygons(entity)]
        return Geometry(type='POLYGON' if len(polys) == 1 else 'MULTIPOLYGON', closed=True,
                        polygons=polys, parameters={'hatch_style': entity.dxf.hatch_style,
                        'pattern_name': entity.dxf.pattern_name, 'solid_fill': bool(entity.dxf.solid_fill)})
    if kind in {'SOLID', 'TRACE', '3DFACE'}:
        pts = [xy(p, scale) for p in entity.wcs_vertices(close=False)]
        poly = Polygon(pts)
        if not poly.is_valid or poly.area <= 0:
            raise InputError('INVALID_POLYGON', '实体不能形成有效面积。')
        return Geometry(type='POLYGON', closed=True, polygons=[polygon_data(poly)])
    if kind in {'LWPOLYLINE', 'POLYLINE'}:
        if kind == 'LWPOLYLINE':
            values = list(entity.get_points())
            bulges = [v[4] for v in values]
            widths = [[v[2] * scale, v[3] * scale] for v in values]
            pts = [xy(p, scale) for p in entity.vertices_in_wcs()]
            closed = bool(entity.closed)
            const_width = entity.dxf.get('const_width', 0) * scale
        else:
            if not entity.is_2d_polyline:
                raise InputError('POLYLINE_3D_UNSUPPORTED', '多面网格/三维 POLYLINE 保留原始数据，暂不解释为平面构件。')
            bulges = [v.dxf.get('bulge', 0) for v in entity.vertices]
            widths = [[v.dxf.get('start_width', 0) * scale, v.dxf.get('end_width', 0) * scale] for v in entity.vertices]
            pts = [xy(p, scale) for p in entity.points_in_wcs()]
            closed = bool(entity.is_closed)
            const_width = 0
        params = {'vertices_mm': pts, 'bulges': bulges, 'vertex_widths_mm': widths,
                  'constant_width_mm': const_width}
        curved = any(bulges)
        if curved:
            pts = sampled(entity, scale, tolerance)
        if closed and pts and pts[0] != pts[-1]:
            pts = [*pts, pts[0]]
        if len(pts) < 2 or LineString(pts).length == 0:
            raise InputError('DEGENERATE_POLYLINE', '多段线没有有效长度。')
        if closed and not curved and not const_width and not any(any(w) for w in widths):
            poly = Polygon(pts)
            if not poly.is_valid or poly.area <= 0:
                raise InputError('INVALID_POLYGON', '闭合多段线自交或退化；没有自动补线或填洞。')
            return Geometry(type='POLYGON', points_mm=pts, closed=True,
                            polygons=[polygon_data(poly)], parameters=params)
        return Geometry(type='POLYLINE', points_mm=pts, closed=closed, parameters=params,
                        approximation_tolerance_mm=tolerance if curved else None)
    if kind in {'ARC', 'CIRCLE', 'ELLIPSE', 'SPLINE'}:
        parameters = raw_value(entity.dxf.all_existing_dxf_attribs())
        parameters['source_units'] = 'CAD_NATIVE'
        if kind in {'ARC', 'CIRCLE'}:
            parameters.update(center_mm=xy(entity.dxf.center, scale), radius_mm=entity.dxf.radius * scale)
            if entity.dxf.radius <= 0:
                raise InputError('INVALID_RADIUS', '圆或圆弧半径必须大于 0。')
        if kind == 'SPLINE':
            parameters.update(control_points_mm=[xy(p, scale) for p in entity.control_points],
                              knots=list(entity.knots), weights=list(entity.weights))
        pts = sampled(entity, scale, tolerance)
        closed = kind == 'CIRCLE' or (len(pts) > 2 and pts[0] == pts[-1])
        return Geometry(type=kind, points_mm=pts, closed=closed, parameters=parameters,
                        approximation_tolerance_mm=tolerance)
    raise InputError('ENTITY_UNSUPPORTED', f'{kind} 暂不支持几何转换，原始属性和 DXF 标签已保留。')


def sampled(entity, scale, tolerance):
    values = []
    for p in make_path(entity).flattening(distance=tolerance / scale, segments=8):
        values.append(xy(p, scale))
        if len(values) > MAX_CURVE_POINTS:
            raise InputError('CURVE_COMPLEXITY_LIMIT', '单条曲线采样超过 20000 点上限。')
    if len(values) < 2:
        raise InputError('CURVE_EMPTY', '无法读取有效曲线。')
    return values


def expand(entity, doc, path, names=(), parent_layer='0', parent_role='UNKNOWN', count=None):
    """Use ezdxf WCS transforms; retain a source path for every block primitive."""
    count = count if count is not None else [0]
    layer = entity.dxf.layer if entity.dxf.layer != '0' else parent_layer
    own_role = role_from_name(layer)
    role = parent_role if entity.dxf.layer == '0' and parent_role != 'UNKNOWN' else (own_role if own_role != 'UNKNOWN' else parent_role)
    if entity.dxftype() != 'INSERT':
        count[0] += 1
        if count[0] > MAX_ENTITIES:
            raise InputError('ENTITY_EXPANSION_LIMIT', '展开实体超过 50000 上限。')
        return [(entity, path, names, layer, role)]
    name = entity.dxf.name
    block = doc.blocks.get(name)
    if name in names or len(names) >= 32 or block is None or block.block.is_xref:
        raise InputError('BLOCK_UNRESOLVED', '块循环、超过 32 层、缺失或为外部参照。')
    if entity.has_extension_dict and 'ACAD_FILTER' in entity.get_extension_dict():
        raise InputError('CLIPPED_BLOCK_UNSUPPORTED', 'XCLIP 块的裁剪语义暂不支持。')
    check_plane(entity)
    named_role = role_from_name(name)
    if named_role != 'UNKNOWN':
        role = named_role
    parts = []
    skipped = []
    if entity.mcount > MAX_ENTITIES:
        raise InputError('ENTITY_EXPANSION_LIMIT', '阵列块超过展开上限。')
    instances = entity.multi_insert() if entity.mcount > 1 else [entity]
    for cell, instance in enumerate(instances):
        for index, child in enumerate(instance.virtual_entities(skipped_entity_callback=lambda e, reason: skipped.append(reason))):
            original = getattr(child, 'source_of_copy', None)
            handle = original.dxf.get('handle', str(index)) if original is not None else str(index)
            child_path = [*path, f'{name}[{cell}]', str(handle)]
            parts.extend(expand(child, doc, child_path, (*names, name), layer, role, count))
        for index, attr in enumerate(instance.attribs):
            parts.extend(expand(attr, doc, [*path, f'ATTRIB[{cell},{index}]'], (*names, name), layer, role, count))
    if skipped or not parts:
        raise InputError('BLOCK_PARTIAL', '块有无法展开的内容：' + '; '.join(skipped or ['空块']))
    return parts


def build_topology(elements, issues):
    edges = []
    for item in elements:
        geom = item.geometry
        if item.type != 'WALL' or geom.approximation_tolerance_mm is not None:
            continue
        # Area rings describe solid footprints; never substitute them for a wall centerline.
        paths = [geom.points_mm] if geom.type in {'LINE', 'POLYLINE'} else []
        for points in paths:
            for index, (a, b) in enumerate(zip(points, points[1:])):
                if a != b:
                    edges.append({'edge_id': f'{item.id}:{index}', 'source_handle': item.source_handles[0],
                                  'element_id': item.id, 'start_mm': list(a), 'end_mm': list(b), 'role': 'WALL'})
    if len(edges) > 3000:
        issues.append(Issue(code='TOPOLOGY_COMPLEXITY_LIMIT', message='精确墙拓扑超过 3000 条边上限；原始构件仍全部输出。'))
        return {'status': 'UNAVAILABLE', 'source_edges': edges, 'nodes': [], 'edges': [], 'closed_regions': []}
    try:
        result = node_linework(edges)
        result.update(status='COMPUTED', source_edges=edges, automatic_gap_closure=False,
                      coordinate_snapping=False, whole_building_boundary=None)
        regions = polygonize_full([LineString([e['start_mm'], e['end_mm']]) for e in result['edges']])[0]
        result['closed_regions'] = [dict(id=f'region-{i}', **polygon_data(p), area_mm2=p.area,
            meaning='GEOMETRIC_FACE_ONLY', adopted_as_floor=False) for i, p in enumerate(sorted(regions.geoms, key=lambda p: (p.bounds, p.area)))]
        return result
    except Exception as exc:
        issues.append(Issue(code='TOPOLOGY_UNRESOLVED', message=f'精确墙拓扑无法完整构建：{type(exc).__name__}；原始坐标保留。'))
        return {'status': 'UNAVAILABLE', 'source_edges': edges, 'nodes': [], 'edges': [], 'closed_regions': []}


def recognition_candidates(elements, topology):
    candidates = []
    texts = [dict(handle=e.source_handles[0], text=e.geometry.parameters.get('text', ''),
                  position_mm=list(e.geometry.points_mm[0])) for e in elements
             if e.geometry.type == 'TEXT' and e.geometry.points_mm]
    edges = [dict(e, handle=e['source_handle']) for e in topology['source_edges']]
    if topology['status'] == 'COMPUTED' and len(edges) <= 500:
        for candidate in find_door_gaps(edges, texts):
            candidate.update(type='DOOR', adopted=False, requires_confirmation=True,
                             meaning='CANDIDATE_ONLY_NO_WALL_OR_FLOOR_CREATED')
            candidates.append(candidate)
    for item in elements:
        if item.type not in {'WALL', 'UNKNOWN'}:
            continue
        for index, data in enumerate(item.geometry.polygons):
            poly = Polygon(data['boundary_mm'], data['holes_mm'])
            rect = poly.minimum_rotated_rectangle
            sides = [math.dist(a, b) for a, b in zip(rect.exterior.coords, list(rect.exterior.coords)[1:])]
            if not poly.interiors and sides and min(sides) >= 100 and max(sides) <= 2000 and max(sides) / min(sides) <= 3 and poly.area / rect.area > .995:
                candidates.append({'id': f'column-candidate-{item.id}-{index}', 'type': 'COLUMN',
                    'element_ids': [item.id], 'source_handles': item.source_handles,
                    'footprint': data, 'confidence': 'CANDIDATE', 'adopted': False,
                    'requires_confirmation': True, 'reason': '紧凑矩形填充可能为柱，也可能为墙垛或设备；形状不能独自确定用途。'})
    return candidates


def reconstruction_of(elements, issues):
    recipes = []
    floors = []
    missing = []
    for item in elements:
        if item.type == 'SPACE' and item.geometry.polygons:
            floors.append({'element_id': item.id, 'polygons': item.geometry.polygons,
                           'source_handles': item.source_handles, 'z_mm': 0,
                           'meaning': 'EXPLICIT_2D_SPACE_SURFACE'})
        if item.type not in {'WALL', 'COLUMN', 'DOOR', 'WINDOW', 'BEAM', 'STAIR'}:
            continue
        geom, dim = item.geometry, item.dimensions
        required = []
        operation = 'UNRESOLVED'
        if item.type in {'DOOR', 'WINDOW'}:
            required.append('verified_opening_span_and_host_wall')
            # A swing arc, symbol line or block bounds is not an opening cut.
        elif item.type in {'BEAM', 'STAIR'}:
            required.append('elevation_and_structural_profile')
        elif geom.polygons:
            operation = 'EXTRUDE_FOOTPRINT'
        elif item.type == 'COLUMN' and geom.type == 'CIRCLE':
            operation = 'EXTRUDE_CIRCLE'
        elif item.type == 'WALL' and geom.type in {'LINE', 'POLYLINE', 'ARC'}:
            operation = 'SWEEP_WALL_LINE'
            if dim.thickness_mm is None:
                required.append('thickness_mm')
            if dim.line_reference == 'UNKNOWN':
                required.append('line_reference')
        else:
            required.append('closed_footprint_or_supported_wall_line')
        if dim.height_mm is None:
            required.append('height_mm')
        if item.type == 'WINDOW' and dim.sill_height_mm is None:
            required.append('sill_height_mm')
        ready = not required
        recipe = {'element_id': item.id, 'type': item.type, 'operation': operation,
                  'status': 'READY' if ready else 'NEEDS_INPUT', 'missing': required,
                  'geometry': geom.model_dump(mode='json'), 'dimensions': dim.model_dump(mode='json'),
                  'dimension_sources': item.dimension_sources}
        recipes.append(recipe)
        if required:
            missing.append({'element_id': item.id, 'fields': required})
    explicit_floor_scopes = [e for e in elements if e.type == 'SPACE' and e.layer.upper() in FLOOR_LAYERS and e.geometry.polygons]
    blockers = []
    if len(explicit_floor_scopes) != 1:
        blockers.append('NO_UNIQUE_EXPLICIT_BUILDING_BOUNDARY')
    if any(e.type == 'UNKNOWN' for e in elements):
        blockers.append('UNKNOWN_SOURCE_ENTITIES')
    if any(i.severity == 'ERROR' for i in issues):
        blockers.append('SOURCE_GEOMETRY_ERRORS')
    if missing:
        blockers.append('MISSING_3D_FACTS')
    return {'status': 'READY' if recipes and not blockers else 'NEEDS_INPUT',
            'supported_element_geometry_ready': bool(recipes) and not blockers,
            'building_shell_complete': None, 'building_shell_status': 'NOT_ASSESSED',
            'building_boundary': explicit_floor_scopes[0].geometry.polygons if len(explicit_floor_scopes) == 1 else None,
            'blockers': blockers, 'missing': missing, 'floor_regions': floors, 'elements': recipes,
            'coordinate_mapping': {'source': 'CAD WCS (x, y, z)', 'threejs_y_up': '[x, z, -y]',
                                   'millimeters_to_meters': 0.001},
            'automatic_thickness_height_or_exterior_inference': False}


def parse_bytes(data: bytes, filename: str = 'drawing.dxf', options: ParseOptions | dict | None = None) -> CadPackage:
    options = ParseOptions.model_validate({} if options is None else options)
    checksum = hashlib.sha256(data).hexdigest()
    if options.source_sha256 is not None and options.source_sha256 != checksum:
        raise InputError('SOURCE_BINDING_MISMATCH', '配置绑定的 SHA256 与上传 CAD 不一致。')
    doc, filename, conversion, unsafe_hatches = read_cad(data, filename)
    if doc.units == 0 and options.assume_units is None:
        raise InputError('UNITS_UNKNOWN', 'INSUNITS=0；请明确提供 assume_units，不能猜测尺寸。')
    if doc.units and options.assume_units is not None:
        raise InputError('UNIT_OVERRIDE_CONFLICT', '图纸已声明单位，assume_units 仅允许用于无单位图纸。')
    scale = ASSUMED_SCALES[options.assume_units] if doc.units == 0 else UNIT_SCALES.get(doc.units)
    if scale is None:
        raise InputError('UNITS_UNSUPPORTED', f'暂不支持 INSUNITS={doc.units} 的单位。')
    roots = list(doc.modelspace())
    if not roots:
        raise InputError('CAD_EMPTY_MODELSPACE', '模型空间没有实体；布局/图纸空间目前不作为建筑平面输入。')
    if len(roots) > MAX_ENTITIES:
        raise InputError('ENTITY_LIMIT', '模型空间实体超过 50000 上限。')
    handles = {e.dxf.handle for e in roots}
    if set(options.entity_overrides) - handles:
        raise InputError('OVERRIDE_HANDLE_UNKNOWN', 'entity_overrides 含当前模型空间中不存在的句柄。')
    layer_roles = {k.casefold(): v for k, v in options.layer_roles.items()}
    elements, issues, inventory = [], [], []
    block_definitions = {}
    expanded_count = [0]
    for root in roots:
        first_element_index = len(elements)
        handle = root.dxf.handle
        record = {'handle': handle, 'type': root.dxftype(), 'layer': root.dxf.layer,
                  'attributes': raw_value(root.dxf.all_existing_dxf_attribs()),
                  'dxf_tags': raw_tags(root, doc.dxfversion), 'element_ids': []}
        try:
            pieces = expand(root, doc, [handle], count=expanded_count)
        except InputError as exc:
            pieces = [(root, [handle], (), root.dxf.layer, 'UNKNOWN')]
            issues.append(Issue(code=exc.code, message=exc.message, severity='ERROR', source_handles=[handle]))
        for entity, path, names, layer, inherited_role in pieces:
            identifier = 'entity-' + '/'.join(path)
            evidence = []
            role = layer_roles.get(layer.casefold(), inherited_role)
            if layer.casefold() in layer_roles:
                evidence.append({'kind': 'USER_LAYER_MAPPING', 'layer': layer, 'role': role})
            elif role != 'UNKNOWN':
                evidence.append({'kind': 'EXPLICIT_BLOCK_NAME' if names and any(role_from_name(n) == role for n in names) else 'EXPLICIT_LAYER_NAME',
                                 'layer': layer, 'block_names': list(names), 'role': role})
            if entity.dxftype() in ANNOTATION_TYPES:
                role = 'ANNOTATION'
                evidence = [{'kind': 'CAD_ANNOTATION_TYPE'}]
            override = options.entity_overrides.get(handle)
            if override and override.role is not None:
                role = override.role
                evidence.append({'kind': 'SOURCE_BOUND_USER_ROLE', 'source_sha256': checksum, 'handle': handle})
            try:
                # Inspect original tags, including block definitions, before ezdxf repairs defaults.
                if entity.dxftype() == 'HATCH' and (handle in unsafe_hatches or path[-1] in unsafe_hatches):
                    raise InputError('HATCH_STYLE_UNSUPPORTED', '原始 HATCH 必须明确且唯一指定 style 0；不能部分使用其他/缺失样式。')
                geom = geometry_of(entity, scale, options.curve_tolerance_mm)
                raw_value(geom.model_dump())  # Also reject overflow in derived, untyped parameter dictionaries.
                if role in {'SPACE', 'HOLE', 'EXCLUSION'} and not geom.polygons:
                    raise InputError('AREA_GEOMETRY_REQUIRED', '空间/孔洞/排除区域必须有有效的精确闭合面。')
            except Exception as exc:
                code = exc.code if isinstance(exc, InputError) else 'GEOMETRY_UNSUPPORTED'
                message = exc.message if isinstance(exc, InputError) else f'无法解释实体几何：{type(exc).__name__}。'
                issues.append(Issue(code=code, message=message, severity='ERROR', element_ids=[identifier], source_handles=[handle]))
                geom = Geometry(type='UNSUPPORTED', parameters={'source_attributes': raw_value(entity.dxf.all_existing_dxf_attribs())})
                role = 'UNKNOWN'
            dims = options.dimensions.get(role, Dimensions()).model_copy(deep=True)
            dim_sources = {k: 'USER_PARAMETER' for k, v in dims.model_dump().items() if v is not None and v != 'UNKNOWN'}
            if override and override.dimensions:
                for key in override.dimensions.model_fields_set:
                    setattr(dims, key, getattr(override.dimensions, key))
                    dim_sources[key] = 'SOURCE_BOUND_USER_PARAMETER'
            element = Element(id=identifier, type=role, source_handles=[handle], source_path=path,
                source_type=entity.dxftype(), layer=layer, block_names=list(names), geometry=geom,
                confidence='UNKNOWN' if role == 'UNKNOWN' else 'EXPLICIT', evidence=evidence,
                dimensions=dims, dimension_sources=dim_sources)
            elements.append(element)
            record['element_ids'].append(identifier)
            if role == 'UNKNOWN':
                issues.append(Issue(code='SEMANTICS_UNKNOWN', message='图层、块名或明确配置不足以确定构件用途；原始数据已保留。',
                                    element_ids=[identifier], source_handles=[handle]))
            for name in names:
                if name not in block_definitions:
                    block_definitions[name] = [{'handle': e.dxf.handle, 'type': e.dxftype(),
                        'dxf_tags': raw_tags(e, doc.dxfversion)} for e in doc.blocks.get(name)]
        record['status'] = 'PARTIAL' if any(e.type == 'UNKNOWN' for e in elements[first_element_index:]) else 'EXTRACTED'
        inventory.append(record)
    if len({e.id for e in elements}) != len(elements):
        raise InputError('SOURCE_ID_COLLISION', '展开实体的来源 ID 冲突。')
    # Audit after extraction so modelspace facts never silently use repaired geometry.
    pre_audit_types = {h: e.dxftype() for h, e in doc.entitydb.items()}
    audit = doc.audit()
    audit_items = []
    for item in [*audit.errors, *audit.fixes]:
        entity = getattr(item, 'entity', None)
        kind = entity.dxftype() if entity is not None else None
        if kind is None:
            source_handle = re.search(r'#([0-9A-F]+)', item.message)
            if source_handle:
                kind = pre_audit_types.get(source_handle[1])
            elif int(item.code) == 207:  # ezdxf AuditError.INVALID_DIMSTYLE
                kind = 'DIMSTYLE_REFERENCE'
        audit_items.append({'code': int(item.code), 'message': item.message, 'entity_type': kind})
    if audit.errors:
        issues.append(Issue(code='DXF_AUDIT_ERRORS', message='DXF 结构审计仍有错误；输出为部分提取，不能当成完整空间。', severity='ERROR'))
    if audit.fixes:
        annotation_only = all(x['entity_type'] in {'PLOTSETTINGS', 'DIMENSION', 'DIMSTYLE', 'DIMSTYLE_REFERENCE', 'STYLE'} for x in audit_items)
        issues.append(Issue(code='DXF_AUDIT_FIXES_RECORDED', message='DXF 审计提出修复，提取使用修复前模型空间；详情见 provenance.dxf_audit。',
                            severity='INFO' if annotation_only else 'ERROR'))
    # Bind explicit floor holes uniquely, preserving existing interior rings.
    spaces = [e for e in elements if e.type == 'SPACE']
    for hole in [e for e in elements if e.type == 'HOLE']:
        for hole_data in hole.geometry.polygons:
            hole_poly = Polygon(hole_data['boundary_mm'], hole_data['holes_mm'])
            owners = [(s, p) for s in spaces for p in s.geometry.polygons
                      if Polygon(p['boundary_mm'], p['holes_mm']).contains(hole_poly)]
            if not owners and options.usable_area.indoor_regions:
                # The separate source-bound indoor scope clips this explicit
                # exclusion; do not demand an unrelated CAD SPACE as its owner.
                continue
            if len(owners) != 1 or hole_poly.interiors:
                issues.append(Issue(code='HOLE_SCOPE_AMBIGUOUS', message='孔洞无法唯一归属到显式空间，未自动填充或裁剪。', severity='ERROR', element_ids=[hole.id]))
                continue
            owner, data_poly = owners[0]
            new = Polygon(data_poly['boundary_mm'], [*data_poly['holes_mm'], hole_data['boundary_mm']])
            if not new.is_valid:
                issues.append(Issue(code='INVALID_SPACE_HOLES', message='孔洞接触/交叠导致空间无效。', severity='ERROR', element_ids=[owner.id, hole.id]))
            else:
                data_poly.update(polygon_data(new))
                owner.evidence.append({'kind': 'EXPLICIT_HOLE', 'element_id': hole.id})
    topology = build_topology(elements, issues)
    candidates = recognition_candidates(elements, topology)
    reconstruction = reconstruction_of(elements, issues)
    usable_area = compute_usable_area(elements, options, checksum, issues)
    points = [p for e in elements for p in e.geometry.points_mm]
    points += [p for e in elements for poly in e.geometry.polygons for p in poly['boundary_mm']]
    bounds = [min(p[0] for p in points), min(p[1] for p in points), max(p[0] for p in points), max(p[1] for p in points)] if points else None
    unknown = [e for e in elements if e.type == 'UNKNOWN']
    counts = {group: sum(e.type == role for e in elements) for role, group in GROUPS.items()}
    complete = not unknown and not any(i.severity == 'ERROR' for i in issues)
    return CadPackage(status='COMPLETE' if complete else 'PARTIAL',
        source=Source(filename=filename, format='DWG' if conversion else 'DXF', sha256=checksum, byte_count=len(data),
                      dxf_version=doc.dxfversion, insunits=doc.units, scale_to_mm=scale, conversion=conversion),
        coordinate_system={'frame': 'CAD_WCS', 'plane': 'XY', 'up_axis': 'Z', 'source_origin_preserved': True,
                           'bounds_mm': bounds, 'suggested_scene_origin_mm': [bounds[0], bounds[1], 0] if bounds else [0, 0, 0]},
        **{group: [e for e in elements if e.type == role] for role, group in GROUPS.items()},
        candidates=candidates, topology=topology, reconstruction=reconstruction, usable_area=usable_area,
        inventory=inventory, issues=issues,
        summary={'source_entity_count': len(roots), 'output_element_count': len(elements), 'counts': counts,
                 'wall_line_count': sum(e.type == 'WALL' and e.geometry.type == 'LINE' for e in elements),
                 'source_entity_types': dict(Counter(e.dxftype() for e in roots)),
                 'candidate_count': len(candidates), 'every_source_entity_accounted_for': len(inventory) == len(roots),
                 'usable_area_status': usable_area.status, 'usable_area_m2': usable_area.usable_area_m2,
                 'status_meaning': '提取与语义状态；摆放条件请查看 usable_area，3D 完整性请查看 reconstruction。'},
        provenance={'parser': f'ezdxf {ezdxf.__version__}', 'geometry_kernel': 'Shapely/GEOS',
                    'rules_version': 'cad-evidence/1.0', 'dxf_audit': audit_items,
                    'options': options.model_dump(mode='json'), 'block_definitions': block_definitions,
                    'raw_tag_units': 'CAD_NATIVE', 'geometry_units': 'mm', 'source_file_modified': False,
                    'tag_capture': 'Loaded entity DXF tags before audit; not a byte-for-byte replacement for the original CAD',
                    'unknown_is_not_outside': True, 'topology_regions_are_not_building_boundaries': True})
