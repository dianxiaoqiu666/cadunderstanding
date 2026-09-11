"""Render source-coordinate CAD review plots; never invent or repair a boundary."""
import io
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


def render_review(packet, proposal=None, *, status='FAILED', geometry_checks=()):
    bounds = packet['coordinate_system'].get('bounds_mm')
    if not bounds or len(bounds) != 4 or not all(math.isfinite(v) for v in bounds):
        return None
    width, height, pad = 1800, 1500, 90
    scale = min((width - pad * 2) / max(bounds[2] - bounds[0], 1),
                (height - pad * 2) / max(bounds[3] - bounds[1], 1))
    left, top = (width - (bounds[2] - bounds[0]) * scale) / 2, (height - (bounds[3] - bounds[1]) * scale) / 2

    def point(p):
        return left + (p[0] - bounds[0]) * scale, height - top - (p[1] - bounds[1]) * scale

    image = Image.new('RGB', (width, height), '#fff')
    draw = ImageDraw.Draw(image)
    font_path = next((p for p in [Path('C:/Windows/Fonts/msyh.ttc'),
                                 Path('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf')] if p.is_file()), None)
    font = ImageFont.truetype(str(font_path), 19) if font_path else ImageFont.load_default(size=19)
    for element in packet['elements']:
        geom = element['geometry']
        for poly in geom['polygons']:
            if element['role'] != 'SPACE':
                draw.polygon([point(p) for p in poly['boundary_mm']], fill='#d9e1de')
                for hole in poly['holes_mm']:
                    draw.polygon([point(p) for p in hole], fill='white')
            for ring in [poly['boundary_mm'], *poly['holes_mm']]:
                if len(ring) >= 2:
                    draw.line([point(p) for p in ring + [ring[0]]], fill='#697b79', width=2)
        pts = geom['points_mm']
        if geom['type'] == 'TEXT' and pts:
            draw.text(point(pts[0]), str(geom['parameters'].get('text', ''))[:100], font=font, fill='#245e58')
        elif len(pts) >= 2:
            draw.line([point(p) for p in pts], fill='#697b79', width=2)
    vertices = {v['id']: v['point_mm'] for v in packet['vertices']}
    ids_to_label = set()
    for region in (proposal or {}).get('indoor_proposals', []):
        for ring in [region['boundary_vertex_ids'], *region['hole_vertex_ids']]:
            if len(ring) >= 3 and all(v in vertices for v in ring):
                draw.line([point(vertices[v]) for v in ring + [ring[0]]],
                          fill='#c13b39' if status == 'REJECTED' else '#7651a2', width=5)
                ids_to_label.update(ring)
        for gap in region['gap_proposals']:
            a, b = gap['from_vertex_id'], gap['to_vertex_id']
            if a in vertices and b in vertices:
                draw.line([point(vertices[a]), point(vertices[b])], fill='#db8a12', width=6)
    for check in geometry_checks:
        ids = check.get('vertex_ids', [])
        if len(ids) == 2 and all(v in vertices for v in ids):
            draw.line([point(vertices[v]) for v in ids], fill='#e52c34', width=9)
        ids_to_label.update(v for v in ids if v in vertices)
    for identifier in sorted(ids_to_label):
        x, y = point(vertices[identifier])
        draw.ellipse((x - 4, y - 4, x + 4, y + 4), fill='#c13b39')
        draw.text((x + 6, y - 23), identifier, font=font, fill='#a32028', stroke_width=2, stroke_fill='white')
    draw.rectangle((0, 0, width, 58), fill='white')
    draw.text((45, 17), f'CAD 审核图 | {status} | source {packet["source_sha256"][:16]}', font=font, fill='#234347')
    draw.rectangle((0, height - 50, width, height), fill='white')
    note = ('灰：源 CAD；紫/红：模型候选；橙：声明补边；粗红：失败边。范围未经人工确认。'
            if (proposal or {}).get('indoor_proposals') else '本次没有可绘制的室内候选；这里只显示源图，不能视为识别结果。')
    draw.text((45, height - 38), note, font=font, fill='#53636a')
    stream = io.BytesIO()
    image.save(stream, format='PNG')
    return stream.getvalue()
