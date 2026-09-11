import hashlib
import io
import json
from pathlib import Path

import ezdxf
from fastapi.testclient import TestClient
import pytest
from shapely.geometry import Polygon

from services.understanding.api import app
from services.understanding.contracts import CadPackage, ParseOptions
from services.understanding.geometry import InputError
from services.understanding.parser import parse_bytes

ROOT = Path(__file__).resolve().parents[1]


def drawing(units=4):
    doc = ezdxf.new('R2018')
    doc.units = units
    for name in ['WALL', 'COLUMN', 'DOOR', 'WINDOW', 'BEAM', 'STAIR', 'PLANNING_SCOPE', 'PLANNING_HOLE', 'EXCLUSION']:
        doc.layers.new(name)
    return doc


def data_of(doc):
    stream = io.StringIO()
    doc.write(stream)
    return stream.getvalue().encode('utf-8')


def add_poly(doc, pts, layer):
    return doc.modelspace().add_lwpolyline(pts, close=True, dxfattribs={'layer': layer})


def test_real_source_all_entities_and_coordinates_preserved():
    path = ROOT / '选定规划只留墙体.dxf'
    before = path.read_bytes()
    result = parse_bytes(before, path.name)
    assert result.source.sha256 == '0753b00fae27eaa1355b9a3274789079ad9d2b33b28ba343f9afe5516d91468d'
    assert len(result.inventory) == result.summary['source_entity_count'] == 104
    assert result.summary['wall_line_count'] == 68
    assert len(result.walls) == 76
    assert len(result.unknown_objects) == 10
    assert 'entity-165' in {e.id for e in result.unknown_objects}
    # Importing dependency code must not import the other project's saved Human state.
    assert result.reconstruction['building_boundary'] is None
    assert result.reconstruction['status'] == 'NEEDS_INPUT'
    assert all(e.dimensions.height_mm is None for e in result.walls)
    assert all(c['adopted'] is False for c in result.candidates)
    expected = ezdxf.readfile(path).entitydb['118'].dxf.start
    line = next(e for e in result.walls if e.id == 'entity-118')
    assert line.geometry.points_mm[0] == tuple(expected)[:2]
    assert before == path.read_bytes()
    encoded = json.dumps(result.model_dump(mode='json'), allow_nan=False, ensure_ascii=False)
    assert CadPackage.model_validate_json(encoded).summary == result.summary
    assert '墙体' in encoded


def test_explicit_components_units_and_unknown_separation():
    doc = drawing(units=6)
    msp = doc.modelspace()
    wall = msp.add_line((0, 0), (5, 0), dxfattribs={'layer': 'WALL'})
    msp.add_line((1, 0), (2, 0), dxfattribs={'layer': 'DOOR'})
    msp.add_line((3, 0), (4, 0), dxfattribs={'layer': 'WINDOW'})
    msp.add_circle((3, 3), .2, dxfattribs={'layer': 'COLUMN'})
    msp.add_line((0, 5), (4, 5), dxfattribs={'layer': 'BEAM'})
    add_poly(doc, [(1, 1), (2, 1), (2, 3), (1, 3)], 'STAIR')
    msp.add_line((9, 0), (9, 4))
    result = parse_bytes(data_of(doc))
    assert len(result.doors) == len(result.windows) == len(result.columns) == 1
    assert len(result.beams) == len(result.stairs) == 1
    assert result.walls[0].geometry.points_mm[-1] == (5000, 0)
    assert result.columns[0].geometry.parameters['radius_mm'] == 200
    assert len(result.unknown_objects) == 1
    assert result.walls[0].source_handles == [wall.dxf.handle]
    assert result.columns[0].geometry.approximation_tolerance_mm == .5


def test_nested_block_rotation_scale_and_role_are_world_coordinates():
    doc = drawing()
    child = doc.blocks.new('detail')
    child.add_line((0, 0), (100, 0))
    parent = doc.blocks.new('DOOR-900')
    parent.add_blockref('detail', (10, 0))
    insert = doc.modelspace().add_blockref('DOOR-900', (1000, 2000), dxfattribs={
        'layer': 'WALL', 'rotation': 90, 'xscale': 2, 'yscale': 2, 'zscale': 2})
    result = parse_bytes(data_of(doc))
    assert len(result.doors) == 1
    assert not result.walls
    line = result.doors[0]
    assert line.geometry.points_mm[0] == pytest.approx((1000, 2020))
    assert line.geometry.points_mm[1] == pytest.approx((1000, 2220))
    assert line.source_handles == [insert.dxf.handle]
    assert line.block_names == ['DOOR-900', 'detail']


def test_hatch_holes_and_explicit_floor_holes_round_trip():
    doc = drawing()
    add_poly(doc, [(0, 0), (10000, 0), (10000, 8000), (0, 8000)], 'PLANNING_SCOPE')
    add_poly(doc, [(1000, 1000), (2000, 1000), (2000, 2000), (1000, 2000)], 'PLANNING_HOLE')
    hatch = doc.modelspace().add_hatch(dxfattribs={'layer': 'WALL', 'hatch_style': 0})
    hatch.paths.add_polyline_path([(3000, 3000), (4000, 3000), (4000, 4000), (3000, 4000)], is_closed=True)
    hatch.paths.add_polyline_path([(3200, 3200), (3800, 3200), (3800, 3800), (3200, 3800)], is_closed=True)
    result = parse_bytes(data_of(doc))
    floor = result.spaces[0].geometry.polygons[0]
    wall = result.walls[0].geometry.polygons[0]
    assert Polygon(floor['boundary_mm'], floor['holes_mm']).area == 79000000
    assert len(wall['holes_mm']) == 1
    assert Polygon(wall['boundary_mm'], wall['holes_mm']).area == 640000


@pytest.mark.parametrize('style', [1, 2, 99, None])
def test_unsafe_hatch_never_emits_partial_wall_areas(style):
    doc = drawing()
    hatch = doc.modelspace().add_hatch(dxfattribs={'layer': 'WALL'})
    hatch.paths.add_polyline_path([(0, 0), (400, 0), (400, 400), (0, 400)], is_closed=True)
    raw = data_of(doc)
    # Set raw group 75: ezdxf's loader may normalize an invalid style.
    start = raw.index(b'\nHATCH\n')
    tail = raw[start:]
    import re
    tail, count = re.subn(rb' 75\n0\n', b'' if style is None else f' 75\n{style}\n'.encode(), tail, count=1)
    assert count == 1
    result = parse_bytes(raw[:start] + tail)
    assert not result.walls
    assert len(result.unknown_objects) == 1
    assert any(i.code == 'HATCH_STYLE_UNSUPPORTED' for i in result.issues)
    assert result.reconstruction['status'] == 'NEEDS_INPUT'


def test_source_bound_overrides_and_dimensions_enable_known_geometry():
    doc = drawing()
    wall = doc.modelspace().add_line((0, 0), (5000, 0))
    add_poly(doc, [(0, 0), (5000, 0), (5000, 4000), (0, 4000)], 'PLANNING_SCOPE')
    data = data_of(doc)
    options = {'source_sha256': hashlib.sha256(data).hexdigest(), 'entity_overrides': {
        wall.dxf.handle: {'role': 'WALL', 'dimensions': {'height_mm': 2800, 'thickness_mm': 200, 'line_reference': 'CENTERLINE'}}}}
    result = parse_bytes(data, options=options)
    assert result.reconstruction['status'] == 'READY'
    assert result.reconstruction['building_shell_complete'] is None
    assert result.reconstruction['building_shell_status'] == 'NOT_ASSESSED'
    assert result.walls[0].dimension_sources['height_mm'] == 'SOURCE_BOUND_USER_PARAMETER'
    with pytest.raises(InputError, match='SHA256'):
        parse_bytes(data + b'\n', options=options)
    with pytest.raises(ValueError):
        ParseOptions.model_validate({'entity_overrides': {wall.dxf.handle: {'role': 'WALL'}}})


def test_closed_wall_network_does_not_invent_building_floor():
    doc = drawing()
    points = [(0, 0), (4000, 0), (4000, 3000), (0, 3000), (0, 0)]
    for a, b in zip(points, points[1:]):
        doc.modelspace().add_line(a, b, dxfattribs={'layer': 'WALL'})
    result = parse_bytes(data_of(doc))
    assert len(result.topology['closed_regions']) == 1
    assert result.reconstruction['building_boundary'] is None
    assert result.reconstruction['status'] == 'NEEDS_INPUT'


def test_duplicate_overlapping_wall_sources_retained_in_topology():
    doc = drawing()
    a = doc.modelspace().add_line((0, 0), (2000, 0), dxfattribs={'layer': 'WALL'})
    b = doc.modelspace().add_line((1500, 0), (500, 0), dxfattribs={'layer': 'WALL'})
    result = parse_bytes(data_of(doc))
    assert len(result.walls) == 2
    assert len(result.topology['edges']) == 3
    middle = next(e for e in result.topology['edges'] if len(e['sources']) == 2)
    assert {x['source_handle'] for x in middle['sources']} == {a.dxf.handle, b.dxf.handle}


def test_units_missing_and_nonplanar_geometry_are_not_guessed():
    doc = drawing(units=0)
    doc.modelspace().add_line((0, 0), (3, 0), dxfattribs={'layer': 'WALL'})
    with pytest.raises(InputError) as failure:
        parse_bytes(data_of(doc))
    assert failure.value.code == 'UNITS_UNKNOWN'
    result = parse_bytes(data_of(doc), options={'assume_units': 'm'})
    assert result.walls[0].geometry.points_mm[-1] == (3000, 0)
    doc.units = 4
    doc.modelspace().add_line((0, 0, 10), (3, 0, 10), dxfattribs={'layer': 'WALL'})
    result = parse_bytes(data_of(doc))
    assert len(result.unknown_objects) == 1
    assert any(i.code == 'NON_PLANAR_GEOMETRY' for i in result.issues)


def test_unsupported_blocks_keep_inventory_and_do_not_emit_partial_walls():
    doc = drawing()
    block = doc.blocks.new('WALL-BLOCK')
    block.add_line((0, 0), (1000, 0))
    block.add_blockref('WALL-BLOCK', (0, 0))
    doc.modelspace().add_blockref('WALL-BLOCK', (0, 0))
    result = parse_bytes(data_of(doc))
    assert not result.walls
    assert len(result.unknown_objects) == len(result.inventory) == 1
    assert result.inventory[0]['dxf_tags']


def test_curves_bulges_columns_and_old_polyline_are_retained():
    doc = drawing()
    msp = doc.modelspace()
    msp.add_lwpolyline([(0, 0, 1), (1000, 0, 0)], format='xyb', dxfattribs={'layer': 'WALL'})
    msp.add_polyline2d([(100, 100), (500, 100), (500, 500)], dxfattribs={'layer': 'WALL'})
    msp.add_arc((2000, 2000), 500, 0, 90, dxfattribs={'layer': 'DOOR'})
    result = parse_bytes(data_of(doc))
    assert len(result.walls) == 2
    assert result.walls[0].geometry.parameters['bulges'] == [1, 0]
    assert result.walls[0].geometry.approximation_tolerance_mm == .5
    assert result.doors[0].geometry.type == 'ARC'
    assert not result.unknown_objects


def test_http_json_array_schema_and_validation_contract():
    client = TestClient(app)
    doc = drawing()
    doc.modelspace().add_line((0, 0), (1000, 0), dxfattribs={'layer': 'WALL'})
    data = data_of(doc)
    assert client.get('/health').json()['status'] == 'ok'
    assert client.get('/v1/cad/schema').status_code == 200
    assert client.get('/openapi.json').status_code == 200
    response = client.post('/v1/cad/parse', files={'file': ('test.dxf', data)})
    assert response.status_code == 200
    assert response.json()['walls'][0]['geometry']['points_mm'] == [[0, 0], [1000, 0]]
    download_url = response.json()['provenance']['http_exports']['package']
    attachment = client.get(download_url)
    assert attachment.status_code == 200
    assert attachment.headers['content-disposition'].startswith('attachment;')
    assert attachment.json() == response.json()
    assert client.get('/v1/cad/results/not-a-hash/package').status_code == 404
    array = client.post('/v1/cad/components', files={'file': ('test.dxf', data)})
    assert isinstance(array.json(), list) and len(array.json()) == 1
    invalid = client.post('/v1/cad/parse', files={'file': ('test.dxf', data)}, data={'options_json': '{'})
    assert invalid.status_code == 422
    invalid = client.post('/v1/cad/parse', files={'file': ('fake.dwg', data)})
    assert invalid.status_code == 422
    assert invalid.json()['detail']['code'] == 'DWG_HEADER_INVALID'


def test_nonfinite_source_rejected_before_loading_defaults():
    doc = drawing()
    doc.modelspace().add_line((1234, 0), (5678, 0), dxfattribs={'layer': 'WALL'})
    raw = data_of(doc).replace(b'1234.0', b'nan')
    with pytest.raises(InputError) as failure:
        parse_bytes(raw)
    assert failure.value.code == 'NONFINITE_DXF_VALUE'


def test_binary_and_gbk_dxf_are_read_without_losing_chinese_layers():
    doc = drawing()
    doc.layers.new('墙体')
    doc.modelspace().add_line((0, 0), (1000, 0), dxfattribs={'layer': '墙体'})
    binary = io.BytesIO()
    doc.write(binary, fmt='bin')
    result = parse_bytes(binary.getvalue(), 'binary.dxf')
    assert result.walls[0].layer == '墙体'
    old = ezdxf.new('R2000')
    old.units = 4
    old.encoding = 'gbk'
    old.layers.new('墙体')
    old.modelspace().add_line((0, 0), (1000, 0), dxfattribs={'layer': '墙体'})
    text = io.StringIO()
    old.write(text)
    result = parse_bytes(text.getvalue().encode('gbk'), 'older.dxf')
    assert result.walls[0].layer == '墙体'


def test_real_oda_dwg_conversion_matches_real_dxf_geometry(tmp_path):
    from services.understanding.cad_io import convert_file, find_converter
    if find_converter() is None:
        pytest.skip('ODA File Converter is not installed')
    data = (ROOT / '选定规划只留墙体.dxf').read_bytes()
    source = tmp_path / 'input/drawing.dxf'
    source.parent.mkdir()
    source.write_bytes(data)
    dwg = convert_file(source, tmp_path / 'output', 'DWG')
    direct = parse_bytes(data)
    converted = parse_bytes(dwg.read_bytes(), 'drawing.dwg')
    assert converted.source.format == 'DWG'
    assert converted.source.conversion['audit_requested'] is False
    assert converted.summary['counts'] == direct.summary['counts']
    assert converted.summary['source_entity_count'] == direct.summary['source_entity_count']
    direct_lines = [e.geometry.points_mm for e in direct.walls if e.geometry.type == 'LINE']
    dwg_lines = [e.geometry.points_mm for e in converted.walls if e.geometry.type == 'LINE']
    assert len(direct_lines) == len(dwg_lines)
    for a, b in zip(direct_lines, dwg_lines):
        for start, end in zip(a, b):
            assert start == pytest.approx(end, abs=1e-7)


def test_web_page_is_self_contained_and_legacy_app_uses_new_api():
    from services.understanding.app import app as entry, understand_bytes
    assert entry is app
    client = TestClient(app)
    response = client.get('/')
    assert response.status_code == 200
    assert '下载完整 JSON' in response.text
    assert '<script src="https://' not in response.text
    assert understand_bytes((ROOT / '选定规划只留墙体.dxf').read_bytes()).schema_version == 'cad-understanding/1.0'


def test_empty_modelspace_is_not_reported_as_complete():
    with pytest.raises(InputError) as failure:
        parse_bytes(data_of(drawing()))
    assert failure.value.code == 'CAD_EMPTY_MODELSPACE'
