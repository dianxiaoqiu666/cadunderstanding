"""Deterministic, source-bound JSON and indexed CAD images for multimodal input."""
from __future__ import annotations

import hashlib
import io
import json
import math
from pathlib import Path

from services.understanding.contracts import SemanticProposal
from services.understanding.geometry import InputError

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE_ROOT = ROOT / 'runtime/evidence'
PROMPT_PATH = Path(__file__).parent / 'prompts/cad_semantics_v2.txt'
PROMPT_VERSION = 'cad-semantic-prompt/2.0'
IMAGE_NAMES = ('plan.png', 'entities.png', 'vertices.png')
FOCUS_IMAGE_NAMES = tuple(f'detail-{part}.png' for part in ('nw', 'ne', 'sw', 'se'))
MODEL_IMAGE_NAMES = ('plan.png', 'vertices.png', *FOCUS_IMAGE_NAMES)
ARTIFACT_NAMES = (*IMAGE_NAMES, *FOCUS_IMAGE_NAMES, 'evidence.json', 'model-input.json',
                  'prompt.txt', 'response-schema.json', 'manifest.json')


def json_bytes(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')


def digest(data):
    return hashlib.sha256(data).hexdigest()


def geometry_paths(geom):
    # Polygon rings precede redundant LWPOLYLINE points and preserve all holes.
    if geom.polygons:
        return [ring for p in geom.polygons for ring in [p['boundary_mm'], *p['holes_mm']]]
    return [geom.points_mm] if geom.points_mm and geom.type not in {'TEXT', 'POINT'} else []


def view_catalog(bounds):
    """Overlapping views cover the drawing extent; they make no indoor assertion."""
    if not bounds or len(bounds) != 4:
        return []
    x0, y0, x1, y1 = bounds
    width, height = x1 - x0, y1 - y0
    parts = {'nw': [x0, y0 + .44 * height, x0 + .56 * width, y1],
             'ne': [x1 - .56 * width, y0 + .44 * height, x1, y1],
             'sw': [x0, y0, x0 + .56 * width, y0 + .56 * height],
             'se': [x1 - .56 * width, y0, x1, y0 + .56 * height]}
    return [{'id': name[:-4], 'image': name, 'bounds_mm': list(bounds),
             'purpose': 'GLOBAL_CONTEXT' if name == 'plan.png' else 'GLOBAL_ID_MAPPING'}
            for name in IMAGE_NAMES] + [
        {'id': f'detail-{part}', 'image': f'detail-{part}.png', 'bounds_mm': box,
         'purpose': 'LOCAL_VERTEX_AND_TEXT_DETAIL'} for part, box in parts.items()]


def build_model_input(evidence):
    """Compact transport view; exact coordinates occur once in the vertex table.

    Full metadata stays in evidence.json. Tentative parser explanations are not
    sent as ready-made semantic answers. Every entity and vertex stays addressable.
    """
    if 'elements' not in evidence:
        return evidence
    elements, anchors, segments = [], [], []
    neighbours = {v['id']: set() for v in evidence['vertices']}
    for e in evidence['elements']:
        g = e['geometry']
        item = {k: e[k] for k in ('id', 'display_id', 'role', 'layer', 'block_names',
                                 'geometry_type', 'path_vertex_ids', 'rendered')}
        item['closed'] = g['closed']
        if g['polygons']:
            item['polygon_ring_counts'] = [1 + len(p['holes_mm']) for p in g['polygons']]
        dimensions = {k: v for k, v in e['dimensions'].items() if v is not None and v != 'UNKNOWN'}
        if dimensions:
            item['known_dimensions'] = dimensions
        if g['approximation_tolerance_mm'] is not None:
            item['approximation_tolerance_mm'] = g['approximation_tolerance_mm']
        if g['type'] in {'TEXT', 'POINT', 'UNSUPPORTED'}:
            item['points_mm'], item['parameters'] = g['points_mm'], g['parameters']
        if g['type'] == 'TEXT' and g['points_mm']:
            anchors.append({'element_id': e['id'], 'text': str(g['parameters'].get('text', '')),
                            'point_mm': g['points_mm'][0], 'meaning': 'TEXT_ANCHOR_NOT_AUTOMATIC_ROOM_TRUTH'})
        for path in e['path_vertex_ids']:
            for a, b in zip(path, path[1:]):
                if a == b:
                    continue
                segments.append({'element_id': e['id'], 'from': a, 'to': b})
                neighbours[a].add(b)
                neighbours[b].add(a)
        elements.append(item)
    hints = []
    for c in evidence['candidates']:
        hints.append({k: c[k] for k in ('id', 'type', 'element_ids', 'source_edge_ids',
                                      'start_mm', 'end_mm', 'span_mm', 'confidence') if k in c})
    return {k: evidence[k] for k in ('source_sha256', 'evidence_sha256', 'units', 'coordinate_system',
                                    'source_format', 'parse_options', 'prompt_version', 'prompt_sha256',
                                    'output_schema_sha256')} | {
        'schema_version': 'cad-vision-input/2.0',
        'task': 'Propose the WHOLE STORE indoor perimeter using the global drawing and all overlapping details.',
        'views': [v for v in evidence['views'] if v['image'] in MODEL_IMAGE_NAMES],
        'elements': elements, 'vertices': evidence['vertices'], 'source_segments': segments,
        'open_endpoint_vertex_ids': [v for v, peers in neighbours.items() if len(peers) == 1],
        'text_anchors': anchors, 'geometric_faces_not_store_boundaries': evidence['closed_regions'],
        'unconfirmed_geometric_hints': hints,
        'unrendered_element_ids': [e['id'] for e in evidence['elements'] if not e['rendered']],
        'issues': evidence['issues'],
        'limits': ['A view rectangle is a camera extent, never an indoor boundary.',
                   'A closed face or text anchor does not establish the whole store scope.',
                   'Every missing perimeter connection must be returned as a gap proposal.'],
    }


def build_evidence(package):
    components = sorted(package.components(), key=lambda e: e.id)
    if len(components) > 1500:
        raise InputError('EVIDENCE_TOO_LARGE', '本次模型输入超过 1500 个构件，请先拆分图纸；不会静默丢弃构件。')
    vertices, indices, elements = [], {}, []

    def vertex_id(point, owner):
        key = tuple(point)
        if key not in indices:
            identifier = f'V{len(vertices) + 1:04d}'
            indices[key] = len(vertices)
            vertices.append({'id': identifier, 'point_mm': list(key), 'element_ids': []})
        entry = vertices[indices[key]]
        if owner and owner not in entry['element_ids']:
            entry['element_ids'].append(owner)
        return entry['id']

    for i, element in enumerate(components):
        g = element.geometry
        paths = [[vertex_id(p, element.id) for p in path] for path in geometry_paths(g)]
        elements.append({'id': element.id, 'display_id': f'E{i + 1:03d}', 'role': element.type,
            'layer': element.layer, 'block_names': element.block_names,
            'source_handles': element.source_handles, 'source_path': element.source_path,
            'geometry_type': g.type, 'geometry': g.model_dump(mode='json'), 'path_vertex_ids': paths,
            'dimensions': element.dimensions.model_dump(mode='json'), 'evidence': element.evidence,
            'rendered': bool(g.points_mm or g.polygons)})
    regions = []
    for region in package.topology.get('closed_regions', []):
        regions.append({'id': region['id'], 'area_mm2': region['area_mm2'],
            'boundary_vertex_ids': [vertex_id(p, None) for p in region['boundary_mm']],
            'hole_vertex_ids': [[vertex_id(p, None) for p in ring] for ring in region['holes_mm']],
            'meaning': 'GEOMETRIC_FACE_ONLY'})
    if len(vertices) > 12000:
        raise InputError('EVIDENCE_TOO_LARGE', '模型证据超过 12000 个顶点，请拆分图纸；不会降低原始精度。')
    # Include interpretation options, so another interpretation of the SAME CAD
    # cannot reuse an earlier proposal merely because source bytes still match.
    payload = {'schema_version': 'cad-model-evidence/2.0', 'source_sha256': package.source.sha256,
        'source_format': package.source.format, 'units': 'mm', 'coordinate_system': package.coordinate_system,
        'elements': elements, 'vertices': vertices, 'closed_regions': regions,
        'candidates': package.candidates, 'issues': [i.model_dump(mode='json') for i in package.issues],
        'parse_options': package.provenance['options'],
        'topology_status': package.topology.get('status'), 'prompt_version': PROMPT_VERSION,
        'prompt_sha256': digest(PROMPT_PATH.read_bytes()), 'render_version': 'indexed-cad/2.0',
        'views': view_catalog(package.coordinate_system.get('bounds_mm')),
        'output_schema_sha256': digest(json_bytes(SemanticProposal.model_json_schema()))}
    encoded = json_bytes(payload)
    if len(encoded) > 1_000_000:
        raise InputError('EVIDENCE_TOO_LARGE', '模型证据超过 1 MB，请拆分图纸；没有发送部分证据。')
    payload['evidence_sha256'] = digest(encoded)
    # The in-memory and transmitted packet use identical JSON representations.
    return json.loads(json_bytes(payload))


def render_evidence(evidence, *, bounds=None, focus_id=None):
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError as exc:
        raise InputError('IMAGE_DEPENDENCY_MISSING', '编号图需要 Pillow，请运行 scripts/setup.ps1 安装项目依赖。') from exc
    width, height, pad = 1800, 1500, 70
    bounds = bounds or evidence['coordinate_system'].get('bounds_mm')
    if not bounds or not all(math.isfinite(v) for v in bounds):
        raise InputError('EVIDENCE_NOT_DRAWABLE', '没有可渲染的平面几何，不能向视觉模型发送空白图。')
    scale = min((width - 2 * pad) / max(bounds[2] - bounds[0], 1),
                (height - 2 * pad) / max(bounds[3] - bounds[1], 1))
    left = (width - (bounds[2] - bounds[0]) * scale) / 2
    top = (height - (bounds[3] - bounds[1]) * scale) / 2

    def project(p):
        return (left + (p[0] - bounds[0]) * scale, height - top - (p[1] - bounds[1]) * scale)

    def visible(p):
        return pad <= p[0] <= width - pad and pad <= p[1] <= height - pad

    font_path = next((p for p in [Path('C:/Windows/Fonts/msyh.ttc'), Path('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf')] if p.is_file()), None)
    font = ImageFont.truetype(str(font_path), 20) if font_path else ImageFont.load_default(size=20)
    small = ImageFont.truetype(str(font_path), 15) if font_path else ImageFont.load_default(size=15)
    base = Image.new('RGB', (width, height), '#ffffff')
    draw = ImageDraw.Draw(base)
    anchors = {}
    for element in evidence['elements']:
        g = element['geometry']
        color = '#ad7618' if element['role'] == 'UNKNOWN' else '#263c3e'
        if element['role'] in {'SPACE', 'DOOR', 'WINDOW'}:
            color = '#246b94'
        points = []
        for poly in g['polygons']:
            mask = Image.new('L', base.size, 0)
            md = ImageDraw.Draw(mask)
            md.polygon([project(p) for p in poly['boundary_mm']], fill=255)
            for ring in poly['holes_mm']:
                md.polygon([project(p) for p in ring], fill=0)
            if element['role'] != 'SPACE':
                base.paste('#b9c4c4', mask=mask)
            for ring in [poly['boundary_mm'], *poly['holes_mm']]:
                draw.line([project(p) for p in ring], fill=color, width=2)
            points.extend(poly['boundary_mm'])
        if g['type'] == 'TEXT' and g['points_mm']:
            draw.text(project(g['points_mm'][0]), str(g['parameters'].get('text', ''))[:160], font=font, fill='#546861')
        elif len(g['points_mm']) >= 2:
            draw.line([project(p) for p in g['points_mm']], fill=color, width=2)
        points += g['points_mm']
        if points:
            anchors[element['id']] = project(((min(p[0] for p in points) + max(p[0] for p in points)) / 2,
                                             (min(p[1] for p in points) + max(p[1] for p in points)) / 2))
    draw.rectangle((0, 0, width, 50), fill='white')
    draw.text((pad, 14), f'{focus_id or "GLOBAL"} | CAD WCS / mm | source ' + evidence['source_sha256'][:16], font=font, fill='#234347')
    images, label_maps = {}, {}
    kinds = [('vertices.png', 'vertices')] if focus_id else [
        ('plan.png', 'plan'), ('entities.png', 'entities'), ('vertices.png', 'vertices')]
    for name, kind in kinds:
        image = base.copy()
        painter = ImageDraw.Draw(image)
        occupied, labels = [], []
        entries = []
        if kind == 'entities':
            entries = [(e['display_id'], anchors[e['id']], e['id']) for e in evidence['elements'] if e['id'] in anchors]
        if kind == 'vertices':
            entries = [(v['id'], project(v['point_mm']), v['id']) for v in evidence['vertices']]
        if focus_id:
            entries = [entry for entry in entries if visible(entry[1])]
        for label, anchor, identifier in entries:
            tw = painter.textbbox((0, 0), label, font=small)[2] + 6
            chosen = None
            for radius in [8, 23, 43, 68, 98, 135]:
                for dx, dy in [(1, -1), (1, 1), (-1, -1), (-1, 1), (0, 1), (0, -1)]:
                    x = min(max(4, anchor[0] + dx * radius), width - tw - 4)
                    y = min(max(42, anchor[1] + dy * radius), height - 25)
                    rect = (x, y, x + tw, y + 21)
                    if not any(rect[0] < b[2] and rect[2] > b[0] and rect[1] < b[3] and rect[3] > b[1] for b in occupied):
                        chosen = rect
                        break
                if chosen:
                    break
            rect = chosen or (max(4, min(anchor[0] + 8, width - tw - 4)),
                              max(42, min(anchor[1] + 8, height - 25)), 0, 0)
            if not chosen:
                rect = (rect[0], rect[1], rect[0] + tw, rect[1] + 21)
            occupied.append(rect)
            painter.line([anchor, (rect[0] + tw / 2, rect[1] + 10)], fill='#b5b5cd', width=1)
            painter.rectangle(rect, fill='#ffffff', outline='#d8d8e7')
            painter.text((rect[0] + 3, rect[1]), label, font=small, fill='#453b94')
            painter.ellipse((anchor[0] - 2, anchor[1] - 2, anchor[0] + 2, anchor[1] + 2), fill='#6956b8')
            labels.append({'label': label, 'id': identifier, 'anchor_px': anchor, 'label_box_px': rect,
                           'overlap_unresolved': chosen is None})
        painter.rectangle((0, height - 40, width, height), fill='white')
        painter.text((pad, height - 30), (focus_id or kind) + ' | V = exact CAD vertex; view frame is NOT an indoor boundary', font=small, fill='#53636a')
        stream = io.BytesIO()
        image.save(stream, format='PNG')
        images[name] = stream.getvalue()
        label_maps[name] = labels
    rendering = {'width_px': width, 'height_px': height, 'bounds_mm': bounds,
                 'scale_px_per_mm': scale, 'left_px': left, 'top_px': top, 'labels': label_maps,
                 'font': font_path.name if font_path else 'Pillow default'}
    if not focus_id:
        rendering['detail_views'] = {}
        for view in evidence['views']:
            if view['image'] not in FOCUS_IMAGE_NAMES:
                continue
            detail_images, detail_render = render_evidence(evidence, bounds=view['bounds_mm'], focus_id=view['id'])
            images[view['image']] = detail_images['vertices.png']
            rendering['detail_views'][view['image']] = detail_render
    return images, rendering


def prepare_evidence(package):
    evidence = build_evidence(package)
    identifier = evidence['evidence_sha256']
    target = EVIDENCE_ROOT / identifier
    # Reuse a complete immutable packet only after every artifact digest matches.
    manifest_path = target / 'manifest.json'
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding='utf-8-sig'))
        if set(manifest.get('artifact_sha256', {})) == set(ARTIFACT_NAMES) - {'manifest.json'} and all((target / name).is_file() and digest((target / name).read_bytes()) == checksum
               for name, checksum in manifest['artifact_sha256'].items()):
            return evidence, manifest
        raise InputError('EVIDENCE_CACHE_CHANGED', '已保存的模型证据文件发生变更，请核实；没有继续调用模型。')
    images, rendering = render_evidence(evidence)
    artifacts = {'evidence.json': json_bytes(evidence), 'model-input.json': json_bytes(build_model_input(evidence)), **images,
                 'prompt.txt': PROMPT_PATH.read_bytes(),
                 'response-schema.json': json_bytes(SemanticProposal.model_json_schema())}
    manifest = {'evidence_id': identifier, 'source_sha256': package.source.sha256,
        'model_image_names': list(MODEL_IMAGE_NAMES),
        'artifact_sha256': {n: digest(b) for n, b in artifacts.items()}, 'rendering': rendering,
        'element_count': len(evidence['elements']), 'vertex_count': len(evidence['vertices']),
        'unrendered_element_ids': [e['id'] for e in evidence['elements'] if not e['rendered']],
        'links': {name: f'/v1/cad/enrichment/evidence/{identifier}/{name}' for name in ARTIFACT_NAMES}}
    target.mkdir(parents=True, exist_ok=True)
    for name, data in artifacts.items():
        (target / name).write_bytes(data)
    manifest_path.write_bytes(json_bytes(manifest))
    return evidence, manifest
