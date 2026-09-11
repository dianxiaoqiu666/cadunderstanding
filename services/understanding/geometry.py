# Strict planar geometry helpers retained from the imported L3 service.
import hashlib
import math
from shapely import normalize
from shapely.geometry import Polygon

class InputError(ValueError):
    def __init__(self, code: str, message: str):
        self.code, self.message = code, message
        super().__init__(message)


def fail(code, message):
    raise InputError(code, message)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def point(value):
    coords = tuple(float(x) for x in value)
    if not all(math.isfinite(x) for x in coords) or (len(coords) > 2 and abs(coords[2]) > 1e-9):
        fail('NON_PLANAR_GEOMETRY', '仅支持有限坐标、Z=0 的二维毫米 CAD。')
    return coords[:2]


def check_plane(entity):
    extrusion = tuple(entity.dxf.get('extrusion', (0, 0, 1)))
    if extrusion != (0, 0, 1):
        fail('NON_PLANAR_GEOMETRY', f'实体 {entity.dxf.handle} 的法向量不是二维正向 Z。')
    # Curves such as ARC encode elevation in their center, not an elevation
    # attribute. Their transformed coordinates are checked by point().
    elevation = entity.dxf.get('elevation', 0) if entity.dxf.is_supported('elevation') else 0
    if isinstance(elevation, (int, float)):
        if not math.isfinite(elevation) or abs(elevation) > 1e-9:
            fail('NON_PLANAR_GEOMETRY', 'CAD elevation 必须为 0。')
    else:
        point(elevation)


def polygon_data(poly):
    poly = normalize(poly)
    return dict(boundary_mm=list(poly.exterior.coords), holes_mm=[list(r.coords) for r in poly.interiors])


def strict_polyline(entity):
    if entity.dxftype() != 'LWPOLYLINE':
        fail('UNSUPPORTED_BOUNDARY', f'实体 {entity.dxf.handle} 必须为闭合 LWPOLYLINE。')
    check_plane(entity)
    vertices = list(entity.get_points())
    if any(v[4] != 0 for v in vertices):
        fail('CURVED_BOUNDARY_UNSUPPORTED', '当前输入契约要求范围、孔洞和禁放区域使用直线段。')
    if not entity.closed:
        fail('SCOPE_NOT_CLOSED', f'实体 {entity.dxf.handle} 未设置闭合标志；请在 CAD 中闭合。')
    coords = [point(v[:2]) for v in vertices]
    if len(coords) < 3:
        fail('INVALID_BOUNDARY', '边界至少需要三个不同顶点。')
    poly = Polygon(coords)
    if not poly.is_valid or not math.isfinite(poly.area) or poly.area <= 0:
        fail('INVALID_BOUNDARY', f'实体 {entity.dxf.handle} 自交、退化或无效。')
    return poly


def hatch_polygons(entity):
    """Interpret NORMAL style using valid, non-crossing loop containment parity."""
    check_plane(entity)
    if entity.dxf.hatch_style != 0:
        fail('HATCH_STYLE_UNSUPPORTED', f'HATCH {entity.dxf.handle} 仅支持 style 0。')
    loops = []
    for path in entity.paths:
        if type(path).__name__ == 'PolylinePath':
            if not path.is_closed or any(v[2] != 0 for v in path.vertices):
                fail('HATCH_PATH_UNSUPPORTED', f'HATCH {entity.dxf.handle} 含开放或曲线路径。')
            coords = [point(v[:2]) for v in path.vertices]
        elif type(path).__name__ == 'EdgePath':
            if not path.edges or any(type(e).__name__ != 'LineEdge' for e in path.edges):
                fail('HATCH_PATH_UNSUPPORTED', f'HATCH {entity.dxf.handle} 仅支持连续直线边。')
            for i, edge in enumerate(path.edges):
                if point(edge.end) != point(path.edges[(i + 1) % len(path.edges)].start):
                    fail('HATCH_PATH_OPEN', f'HATCH {entity.dxf.handle} 边界不连续闭合。')
            coords = [point(e.start) for e in path.edges]
        else:
            fail('HATCH_PATH_UNSUPPORTED', f'HATCH {entity.dxf.handle} 路径不受支持。')
        if len(coords) < 3:
            fail('HATCH_INVALID', 'HATCH 边界顶点不足。')
        poly = Polygon(coords)
        if not poly.is_valid or not math.isfinite(poly.area) or poly.area <= 0:
            fail('HATCH_INVALID', f'HATCH {entity.dxf.handle} 含无效边界，不能部分使用。')
        for previous in loops:
            if previous.boundary.intersects(poly.boundary) or (previous.intersects(poly) and not (previous.contains(poly) or poly.contains(previous))):
                fail('HATCH_LOOPS_AMBIGUOUS', f'HATCH {entity.dxf.handle} 含相交或接触边界。')
        loops.append(poly)
    if not loops:
        fail('HATCH_INVALID', 'HATCH 不含边界。')
    result = loops[0]
    for poly in loops[1:]:
        result = result.symmetric_difference(poly)
    if result.geom_type not in {'Polygon', 'MultiPolygon'} or not result.is_valid:
        fail('HATCH_INVALID', 'HATCH 无法解释为有效排除区域。')
    polys = [result] if result.geom_type == 'Polygon' else list(result.geoms)
    return sorted(polys, key=lambda p: (p.bounds, p.area))


