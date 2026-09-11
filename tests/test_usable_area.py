import hashlib
import io
import json
import math
from pathlib import Path

import ezdxf
from fastapi.testclient import TestClient
from pydantic import ValidationError
import pytest
from shapely.geometry import Point, Polygon, box
from shapely.ops import unary_union

from services.understanding.api import app
from services.understanding.contracts import CadPackage, ParseOptions
from services.understanding.geometry import InputError
from services.understanding.parser import parse_bytes
from services.understanding.usable_area import check_placement, polygon_of, union_regions

ROOT = Path(__file__).resolve().parents[1]


def drawing(units=4):
    doc = ezdxf.new('R2018')
    doc.units = units
    for name in ['WALL', 'COLUMN', 'PLANNING_SCOPE', 'PLANNING_HOLE', 'ROOM', 'EXCLUSION']:
        doc.layers.new(name)
    return doc


def data_of(doc):
    text = io.StringIO()
    doc.write(text)
    return text.getvalue().encode('utf-8')


def add_area(doc, geometry, layer):
    return doc.modelspace().add_lwpolyline(list(geometry.exterior.coords)[:-1], close=True,
                                          dxfattribs={'layer': layer})


def footprint(geometry):
    return {'boundary_mm': list(geometry.exterior.coords),
            'holes_mm': [list(r.coords) for r in geometry.interiors]}


def manual_options(data, regions, **policy):
    return {'source_sha256': hashlib.sha256(data).hexdigest(),
            'usable_area': {'indoor_regions': [footprint(p) for p in regions], **policy}}


def test_concave_indoor_minus_overlapping_obstacles_does_not_include_outdoors():
    doc = drawing()
    scope = Polygon([(0, 0), (10000, 0), (10000, 4000), (6000, 4000), (6000, 8000), (0, 8000)])
    add_area(doc, scope, 'PLANNING_SCOPE')
    column = box(2000, 2000, 3000, 3000)
    exclusion = box(2500, 2500, 3500, 3500)
    add_area(doc, column, 'COLUMN')
    add_area(doc, exclusion, 'EXCLUSION')
    add_area(doc, box(7000, 5000, 8000, 6000), 'COLUMN')  # outdoors inside the bounding box
    result = parse_bytes(data_of(doc))
    area = result.usable_area
    free = union_regions(area.regions)
    expected = scope.difference(unary_union([column, exclusion]))
    assert area.status == 'READY' and area.ready_for_placement
    assert free.symmetric_difference(expected).area < 1e-6
    assert not free.covers(Point(7500, 5500))
    assert len(area.obstacles) == 2
    assert area.indoor_area_m2 == 64
    assert area.excluded_area_m2 == 1.75
    assert area.usable_area_m2 == 62.25
    assert result.reconstruction['status'] == 'NEEDS_INPUT'  # heights are independent


def test_scope_holes_hatch_holes_and_disconnected_free_islands_roundtrip():
    doc = drawing()
    add_area(doc, box(0, 0, 10000, 8000), 'PLANNING_SCOPE')
    add_area(doc, box(1000, 1000, 2000, 2000), 'PLANNING_HOLE')
    hatch = doc.modelspace().add_hatch(dxfattribs={'layer': 'WALL', 'hatch_style': 0})
    for geom in [box(3000, 3000, 5000, 5000), box(3500, 3500, 4500, 4500)]:
        hatch.paths.add_polyline_path(list(geom.exterior.coords)[:-1], is_closed=True)
    result = parse_bytes(data_of(doc))
    area = result.usable_area
    assert area.status == 'READY'
    assert area.indoor_area_m2 == 79
    assert area.usable_area_m2 == 76
    assert area.excluded_area_m2 == 3
    assert len(area.regions) == 2
    free = union_regions(area.regions)
    assert not free.covers(Point(1500, 1500))
    assert free.covers(Point(4000, 4000))
    assert not free.covers(Point(3200, 3200))
    restored = CadPackage.model_validate_json(result.model_dump_json())
    assert union_regions(restored.usable_area.regions).symmetric_difference(free).area == 0


def test_round_column_footprint_covers_exact_circle_and_retains_source():
    doc = drawing()
    add_area(doc, box(0, 0, 10000, 8000), 'PLANNING_SCOPE')
    col = doc.modelspace().add_circle((5000, 4000), 500, dxfattribs={'layer': 'COLUMN'})
    area = parse_bytes(data_of(doc)).usable_area
    obstacle = area.obstacles[0]
    shape = union_regions(obstacle.polygons)
    assert obstacle.source_handles == [col.dxf.handle]
    assert obstacle.basis == 'CONSERVATIVE_CIRCLE_FOOTPRINT'
    for i in range(360):
        angle = math.radians(i)
        # Analytical boundary checks detect inscribed polygon wedges.
        assert shape.distance(Point(5000 + 500 * math.cos(angle), 4000 + 500 * math.sin(angle))) < 1e-8
    assert math.pi * 500**2 <= shape.area <= math.pi * 500.5**2


@pytest.mark.parametrize('reference,expected', [
    ('CENTERLINE', box(4900, 0, 5100, 8000)),
    ('LEFT_FACE', box(5000, 0, 5200, 8000)),
    ('RIGHT_FACE', box(4800, 0, 5000, 8000)),
])
def test_known_wall_width_partitions_regions_and_uses_directed_face(reference, expected):
    doc = drawing()
    add_area(doc, box(0, 0, 10000, 8000), 'PLANNING_SCOPE')
    doc.modelspace().add_line((5000, 0), (5000, 8000), dxfattribs={'layer': 'WALL'})
    result = parse_bytes(data_of(doc), options={'dimensions': {'WALL': {
        'thickness_mm': 200, 'line_reference': reference}}})
    area = result.usable_area
    assert area.status == 'READY'
    assert len(area.regions) == 2
    assert union_regions(area.obstacles[0].polygons).symmetric_difference(expected).area < 1e-6
    assert area.usable_area_m2 == pytest.approx(78.4)
    assert check_placement(result, footprint(box(4000, 3000, 6000, 4000))).decision == 'INVALID'
    assert check_placement(result, footprint(box(1000, 1000, 2000, 2000))).allowed


def test_clearance_erodes_scope_and_buffers_column_without_erasing_holes():
    doc = drawing()
    scope = box(0, 0, 10000, 8000)
    column = box(4000, 3000, 5000, 4000)
    add_area(doc, scope, 'PLANNING_SCOPE')
    add_area(doc, column, 'COLUMN')
    result = parse_bytes(data_of(doc), options={'usable_area': {
        'boundary_clearance_mm': 300, 'obstacle_clearance_mm': 200}})
    area = result.usable_area
    free = union_regions(area.regions)
    assert free.distance(scope.boundary) >= 300 - 1e-7
    assert free.distance(column) >= 200 - 1e-7
    assert area.usable_area_m2 + area.excluded_area_m2 == pytest.approx(area.indoor_area_m2)
    assert check_placement(result, footprint(box(100, 100, 500, 500))).decision == 'INVALID'
    assert check_placement(result, footprint(box(4200, 2700, 4600, 2900))).decision == 'INVALID'
    assert check_placement(result, footprint(box(1000, 1000, 2000, 2000))).allowed


def test_zero_width_wall_is_a_barrier_and_cannot_silently_become_placeable():
    doc = drawing()
    add_area(doc, box(0, 0, 10000, 8000), 'PLANNING_SCOPE')
    wall = doc.modelspace().add_line((5000, 0), (5000, 8000), dxfattribs={'layer': 'WALL'})
    result = parse_bytes(data_of(doc))
    assert result.usable_area.status == 'REVIEW_REQUIRED'
    assert result.usable_area.usable_area_m2 == 80
    assert result.usable_area.barriers[0].footprint_known is False
    crossing = check_placement(result, footprint(box(4000, 1000, 6000, 2000)))
    assert crossing.decision == 'INVALID'
    assert crossing.colliding_element_ids == ['entity-' + wall.dxf.handle]
    clear = check_placement(result, footprint(box(1000, 1000, 2000, 2000)))
    assert clear.geometric_fit and clear.decision == 'REVIEW_REQUIRED' and not clear.allowed


def test_entire_equipment_footprint_must_fit_not_just_center_or_corners():
    doc = drawing()
    add_area(doc, box(0, 0, 10000, 8000), 'PLANNING_SCOPE')
    add_area(doc, box(4000, 4000, 4500, 4500), 'COLUMN')
    result = parse_bytes(data_of(doc))
    # Center is inside; right edge is outdoors.
    outside = box(9000, 1000, 10500, 2000)
    assert union_regions(result.usable_area.regions).covers(outside.centroid)
    assert 'OUTSIDE_INDOOR_SCOPE' in check_placement(result, footprint(outside)).reasons
    # All four corners and center avoid the column, but the rectangle encloses it.
    encloses_column = box(3000, 3000, 7000, 6000)
    assert union_regions(result.usable_area.regions).covers(encloses_column.centroid)
    assert check_placement(result, footprint(encloses_column)).decision == 'INVALID'


def test_manual_indoor_scope_is_source_bound_and_subtracts_explicit_holes():
    doc = drawing()
    add_area(doc, box(2000, 2000, 3000, 3000), 'COLUMN')
    add_area(doc, box(5000, 5000, 6000, 6000), 'PLANNING_HOLE')
    data = data_of(doc)
    options = manual_options(data, [box(0, 0, 10000, 8000)])
    result = parse_bytes(data, options=options)
    assert result.usable_area.status == 'READY'
    assert result.usable_area.scope_source == 'SOURCE_BOUND_USER'
    assert result.usable_area.usable_area_m2 == 78
    assert result.reconstruction['building_boundary'] is None
    with pytest.raises(ValidationError):
        ParseOptions.model_validate({'usable_area': options['usable_area']})
    options['source_sha256'] = '0' * 64
    with pytest.raises(InputError, match='SHA256'):
        parse_bytes(data, options=options)


@pytest.mark.parametrize('region', [
    {'boundary_mm': [(0, 0), (1000, 1000), (0, 1000), (1000, 0)]},
    {'boundary_mm': [(0, 0), (1000, 0), (2000, 0)]},
    {'boundary_mm': [(0, 0), (1000, 0), (1000, 1000), (0, 1000)],
     'holes_mm': [[(2000, 2000), (3000, 2000), (3000, 3000)]]},
    {'boundary_mm': [(0, 0), (1000, 0), (1000, 1000), (0, 1000)], 'holes_mm': [[(1, 1)]]},
])
def test_invalid_indoor_or_equipment_rings_are_rejected_without_repair(region):
    with pytest.raises(InputError) as exc:
        polygon_of(region)
    assert exc.value.code == 'REGION_INVALID'


def test_multiple_whole_building_scopes_do_not_choose_largest_but_explicit_rooms_work():
    doc = drawing()
    add_area(doc, box(0, 0, 10000, 8000), 'PLANNING_SCOPE')
    second = add_area(doc, box(12000, 0, 14000, 2000), 'PLANNING_SCOPE')
    result = parse_bytes(data_of(doc))
    assert result.usable_area.status == 'NOT_READY' and not result.usable_area.regions
    for e in doc.modelspace():
        e.dxf.layer = 'ROOM'
    result = parse_bytes(data_of(doc))
    assert result.usable_area.scope_source == 'EXPLICIT_CAD_ROOMS'
    assert result.usable_area.status == 'READY' and len(result.usable_area.regions) == 2
    assert result.usable_area.usable_area_m2 == 84
    second.translate(-10000, 0, 0)
    assert parse_bytes(data_of(doc)).usable_area.issues[0].code == 'INDOOR_SCOPE_OVERLAP'


def test_millimeter_output_when_source_is_meters_and_empty_is_distinct_from_missing():
    doc = drawing(units=6)
    add_area(doc, box(0, 0, 10, 8), 'PLANNING_SCOPE')
    add_area(doc, box(4, 4, 5, 5), 'COLUMN')
    result = parse_bytes(data_of(doc))
    assert result.usable_area.usable_area_m2 == 79
    assert max(p[0] for p in result.usable_area.regions[0].boundary_mm) == 10000
    result = parse_bytes(data_of(doc), options={'usable_area': {'boundary_clearance_mm': 5000}})
    assert result.usable_area.status == 'EMPTY'
    assert result.usable_area.usable_area_m2 == 0 and not result.usable_area.ready_for_placement
    assert not check_placement(result, footprint(box(1000, 1000, 2000, 2000))).allowed


def test_unsafe_source_geometry_never_publishes_partial_usable_area():
    doc = drawing()
    add_area(doc, box(0, 0, 10000, 8000), 'PLANNING_SCOPE')
    hatch = doc.modelspace().add_hatch(dxfattribs={'layer': 'WALL', 'hatch_style': 1})
    hatch.paths.add_polyline_path([(3000, 3000), (4000, 3000), (4000, 4000), (3000, 4000)], is_closed=True)
    result = parse_bytes(data_of(doc))
    assert result.usable_area.status == 'NOT_READY'
    assert result.usable_area.regions == [] and result.usable_area.usable_area_m2 is None
    assert result.usable_area.issues[0].code == 'INCOMPLETE_OBSTACLE_GEOMETRY'


def test_real_cad_keeps_accepted_components_and_does_not_adopt_pillars_as_rooms():
    data = (ROOT / '选定规划只留墙体.dxf').read_bytes()
    result = parse_bytes(data)
    baseline = json.loads((ROOT / '_archive/usable-area-baseline-2026-09-09/outputs/current-cad.json').read_text(encoding='utf-8-sig'))
    for group in ['walls', 'columns', 'doors', 'windows', 'spaces', 'unknown_objects', 'inventory', 'topology']:
        assert result.model_dump(mode='json')[group] == baseline[group]
    assert result.usable_area.status == 'NOT_READY' and result.usable_area.regions == []
    assert result.usable_area.issues[0].code == 'INDOOR_SCOPE_REQUIRED'
    assert len(result.topology['closed_regions']) == 9
    # A synthetic test scope exercises real HATCH/LINE geometry. It is never
    # saved or represented as an approved room boundary for this real store.
    options = manual_options(data, [box(30000, -36000, 54000, -11000)])
    scoped = parse_bytes(data, options=options)
    area = scoped.usable_area
    assert area.status == 'REVIEW_REQUIRED'
    assert len(area.obstacles) == 8
    free = union_regions(area.regions)
    for wall in result.walls:
        for poly in wall.geometry.polygons:
            assert free.intersection(polygon_of(poly)).area < 1e-6
    assert area.indoor_area_m2 == 600
    assert area.excluded_area_m2 == pytest.approx(3.03)
    assert area.usable_area_m2 == pytest.approx(596.97)


def test_api_area_download_and_placement_uses_immutable_saved_result(tmp_path, monkeypatch):
    from services.understanding import exports, api
    monkeypatch.setattr(exports, 'RESULTS', tmp_path)
    monkeypatch.setattr(api, 'RESULTS', tmp_path)
    doc = drawing()
    add_area(doc, box(0, 0, 10000, 8000), 'PLANNING_SCOPE')
    add_area(doc, box(4000, 4000, 5000, 5000), 'COLUMN')
    client = TestClient(app)
    data = data_of(doc)
    package = client.post('/v1/cad/parse', files={'file': ('floor.dxf', data)}).json()
    links = package['provenance']['http_exports']
    downloaded = client.get(links['usable-area'])
    assert downloaded.json() == package['usable_area']
    assert 'attachment;' in downloaded.headers['content-disposition']
    assert client.get(links['options']).json() == package['provenance']['options']
    area_only = client.post('/v1/cad/usable-area', files={'file': ('floor.dxf', data)})
    assert area_only.json() == package['usable_area']
    request = {'result_id': links['result_id'], 'footprint': footprint(box(1000, 1000, 2000, 2000))}
    assert client.post('/v1/cad/placement-check', json=request).json()['allowed'] is True
    request['footprint'] = footprint(box(3000, 3000, 6000, 6000))
    assert client.post('/v1/cad/placement-check', json=request).json()['decision'] == 'INVALID'
    request['footprint'] = {'boundary_mm': [(0, 0), (1, 1), (0, 1), (1, 0)]}
    assert client.post('/v1/cad/placement-check', json=request).status_code == 422
    request['result_id'] = '../outside'
    assert client.post('/v1/cad/placement-check', json=request).status_code == 422
    request['result_id'] = '0' * 64
    assert client.post('/v1/cad/placement-check', json=request).status_code == 404
    invalid_options = {'source_sha256': '0' * 64, 'usable_area': {'indoor_regions': [footprint(box(0, 0, 1, 1))]}}
    assert client.post('/v1/cad/parse', files={'file': ('floor.dxf', data)},
                       data={'options_json': json.dumps(invalid_options)}).status_code == 422
