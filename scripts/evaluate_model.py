"""Freeze one CAD/model experiment with inputs, final reply, checks and a visual report.

Default: prepare only. --call-model attempts at most one request using the saved
local provider/key; changing --model does not change the application's settings.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
import html
import json
from pathlib import Path
import re
import shutil
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.understanding.contracts import CadPackage
from services.understanding.enrichment import enrich_package
from services.understanding.evidence import (
    EVIDENCE_ROOT, MODEL_IMAGE_NAMES, build_model_input, geometry_paths, json_bytes, prepare_evidence,
)
from services.understanding.geometry import InputError
from services.understanding.interpretation import interpreted_area, interpreted_components
from services.understanding.model_client import call_model, load_config
from services.understanding.model_runs import ModelRun
from services.understanding.parser import parse_bytes


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + '\n', encoding='utf-8')


def outcome(package):
    addition = package.enrichment
    area = interpreted_area(package)
    return {
        'counts': dict(Counter(e.type for e in interpreted_components(package))),
        'indoor_area_m2': area.indoor_area_m2, 'usable_area_m2': area.usable_area_m2,
        'excluded_area_m2': area.excluded_area_m2, 'obstacle_count': len(area.obstacles),
        'ready_for_placement': area.ready_for_placement,
        'enrichment_status': addition.status if addition else 'NONE',
        'reference_validation': addition.reference_validation if addition else 'NOT_RUN',
        'scope_quality': addition.provenance.get('scope_quality') if addition else None,
        'usage': addition.provenance.get('usage') if addition else None,
    }


def render_report(package, evidence, report):
    x0, y0, x1, y1 = evidence['coordinate_system']['bounds_mm']
    span = max(x1 - x0, y1 - y0, 1)
    pad = span * .04
    font = span / 95
    shapes = []

    def path(points, close=False):
        return ('M ' + ' L '.join(f'{x:.9f},{-y:.9f}' for x, y in points) + (' Z' if close else '')) if points else ''

    for element in package.components():
        geom = element.geometry
        color = '#a27823' if element.type == 'UNKNOWN' else '#445d61'
        if geom.polygons:
            d = ' '.join(path(ring, True) for ring in geometry_paths(geom))
            shapes.append(f'<path d="{d}" fill="#b5c6c9" fill-rule="evenodd" stroke="{color}"/>')
        else:
            for ring in geometry_paths(geom):
                shapes.append(f'<path d="{path(ring)}" fill="none" stroke="{color}"/>')
        if geom.type == 'TEXT' and geom.points_mm:
            x, y = geom.points_mm[0]
            shapes.append(f'<text x="{x}" y="{-y}" font-size="{font}">{html.escape(str(geom.parameters.get("text", "")))}</text>')
    vertices = {v['id']: v['point_mm'] for v in evidence['vertices']}
    if package.enrichment:
        # Render raw proposed rings even when validation rejected them, visibly red.
        accepted = package.enrichment.reference_validation == 'PASS'
        color, fill = ('#187b6f', '#27a99120') if accepted else ('#b33c30', '#b33c3015')
        for region in package.enrichment.proposal.indoor_proposals:
            ids = set(region.boundary_vertex_ids) | {v for ring in region.hole_vertex_ids for v in ring}
            if not ids <= vertices.keys():
                continue
            rings = [region.boundary_vertex_ids, *region.hole_vertex_ids]
            d = ' '.join(path([vertices[v] for v in ring], True) for ring in rings)
            shapes.append(f'<path d="{d}" fill="{fill}" fill-rule="evenodd" stroke="{color}" stroke-width="3"/>')
            for identifier in set(region.boundary_vertex_ids):
                x, y = vertices[identifier]
                shapes.append(f'<text x="{x+font*.4}" y="{-y-font*.4}" font-size="{font*.7}" fill="{color}">{identifier}</text>')
            for gap in region.gap_proposals:
                if gap.from_vertex_id in vertices and gap.to_vertex_id in vertices:
                    d = path([vertices[gap.from_vertex_id], vertices[gap.to_vertex_id]])
                    shapes.append(f'<path d="{d}" fill="none" stroke="#e17c18" stroke-width="4" stroke-dasharray="8 5"/>')
    svg = f'<svg viewBox="{x0-pad} {-y1-pad} {x1-x0+2*pad} {y1-y0+2*pad}" xmlns="http://www.w3.org/2000/svg"><style>path{{vector-effect:non-scaling-stroke;stroke-width:1.3}}text{{font-family:Microsoft YaHei,sans-serif}}</style>{"".join(shapes)}</svg>'
    model = html.escape(report.get('requested_model', '未调用'))
    quality = ((report.get('result') or {}).get('scope_quality') or {})
    baseline = report.get('baseline')
    comparison = ''
    if baseline:
        current = report.get('result', {})
        rows = ''.join(f'<tr><td>{label}</td><td>{html.escape(str(baseline.get(key)))}</td><td>{html.escape(str(current.get(key)))}</td></tr>'
                       for key, label in [('indoor_area_m2', '室内候选面积㎡'), ('usable_area_m2', '净空候选面积㎡'),
                                          ('obstacle_count', '范围内障碍数'), ('reference_validation', '引用/几何校验')])
        comparison = f'<table><tr><th>项目</th><th>此前结果</th><th>本次结果</th></tr>{rows}</table>'
    issues = package.enrichment.issues if package.enrichment else []
    details = ''.join(f'<li>{html.escape(i.code)}：{html.escape(i.message)}</li>' for i in issues)
    summary = html.escape(package.enrichment.proposal.scope_review.summary) if package.enrichment and package.enrichment.proposal.scope_review else '没有整店核对结论。'
    links = ''.join(f'<a href="evidence/{name}" target="_blank">{name}</a> ' for name in MODEL_IMAGE_NAMES)
    page = f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>门店外围模型评测</title><style>body{{margin:0;background:#f2f6f5;color:#183d40;font:15px/1.65 "Microsoft YaHei",sans-serif}}main{{max-width:1100px;margin:30px auto;padding:0 22px}}section{{background:white;border:1px solid #d5e2dd;border-radius:12px;padding:22px;margin:18px 0}}svg{{width:100%;max-height:850px}}h1{{font-size:25px}}h2{{font-size:18px}}table{{border-collapse:collapse;width:100%}}td,th{{border-bottom:1px solid #dce7e2;padding:9px;text-align:left}}a{{color:#176f64;overflow-wrap:anywhere}}.notice{{background:#fff3d8;padding:12px;border-radius:7px}}pre{{white-space:pre-wrap;overflow-wrap:anywhere}}</style>
<main><h1>门店外围模型评测 · {model}</h1><p>{html.escape(report['run_name'])} · {html.escape(report['prompt_version'])}</p>
<p class="notice">显示的是模型候选；绿色表示引用与几何检查通过，红色表示被拒绝，橙色虚线表示模型声明的缺口。面积变大不等于语义正确，最终范围仍待核实。</p>
<section><h2>本次模型结论</h2><p>{summary}</p><p>模型范围声明：{html.escape(str(quality.get('model_extent_claim', '未评估')))}；接口结果：{html.escape(report['status'])}</p>{comparison}<ul>{details}</ul></section>
<section><h2>源图与模型外围候选</h2>{svg}</section><section><h2>本次实际输入</h2><p>{links}</p><p><a href="evidence/model-input.json">模型读取的 JSON</a> · <a href="evidence/prompt.txt">提示词</a> · <a href="report.json">评测记录</a> · <a href="package.json">完整结果</a></p></section></main></html>'''
    return svg, page


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', type=Path)
    parser.add_argument('--options', type=Path)
    parser.add_argument('--call-model', action='store_true')
    parser.add_argument('--model', help='Use a different model at the same configured endpoint for this run only.')
    parser.add_argument('--timeout-seconds', type=int, help='Override request timeout for this experiment only (10-600).')
    parser.add_argument('--baseline', type=Path, help='Earlier CadPackage from the same source CAD.')
    parser.add_argument('--run-name', default=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
    args = parser.parse_args()
    if not re.fullmatch(r'[a-zA-Z0-9_-]{1,80}', args.run_name):
        parser.error('run-name must contain only letters, numbers, underscore or hyphen')
    if args.timeout_seconds is not None and not 10 <= args.timeout_seconds <= 600:
        parser.error('timeout-seconds must be between 10 and 600')
    destination = ROOT / 'outputs/model-evaluations' / args.run_name
    if destination.exists():
        parser.error('run-name already exists; an experiment is immutable')
    options = json.loads(args.options.read_text(encoding='utf-8-sig')) if args.options else {}
    package = parse_bytes(args.input.read_bytes(), args.input.name, options)
    baseline = CadPackage.model_validate_json(args.baseline.read_text(encoding='utf-8-sig')) if args.baseline else None
    if baseline and baseline.source.sha256 != package.source.sha256:
        parser.error('baseline source hash differs from input; no model request was sent')
    packet, manifest = prepare_evidence(package)
    destination.mkdir(parents=True)
    shutil.copytree(EVIDENCE_ROOT / packet['evidence_sha256'], destination / 'evidence')
    report = {'run_name': args.run_name, 'created_at_utc': datetime.now(timezone.utc).isoformat(),
              'source_sha256': package.source.sha256, 'evidence_sha256': packet['evidence_sha256'],
              'prompt_version': packet['prompt_version'], 'prompt_sha256': packet['prompt_sha256'],
              'status': 'PREPARED', 'api_calls_attempted': 0, 'human_acceptance': 'PENDING',
              'semantic_accuracy': 'NOT_MEASURED_WITHOUT_APPROVED_REFERENCE',
              'input_bytes': {'source_evidence': len(json_bytes(packet)),
                              'model_json': len(json_bytes(build_model_input(packet)))},
              'model_image_names': list(MODEL_IMAGE_NAMES), 'baseline': outcome(baseline) if baseline else None}
    exit_code = 0
    if args.call_model:
        started = time.monotonic()
        diagnostic_run = None

        def record_transport(value):
            if diagnostic_run:
                diagnostic_run.on_transport(value)
            # A started HTTP operation does not prove provider receipt or billing.
            report['transport'] = value
            report['api_calls_attempted'] = int(value['http_request_started'])
            write_json(destination / 'transport.json', value)
            write_json(destination / 'report.json', report)

        def record_response(value):
            if diagnostic_run:
                diagnostic_run.on_response(value)
            write_json(destination / 'model-final-response.json', value)
            report['request_metadata'] = value['metadata']
            write_json(destination / 'report.json', report)

        try:
            diagnostic_run = ModelRun(package)
            diagnostic_run.attach_evidence(packet, manifest)
            config = load_config()
            if args.model:
                config.model = args.model
            if args.timeout_seconds is not None:
                config.timeout_seconds = args.timeout_seconds
            diagnostic_run.configure(config)
            report['requested_model'] = config.model
            report['provider'] = config.provider
            report['status'] = 'IN_PROGRESS'
            report['request_limits'] = {'timeout_seconds': config.timeout_seconds,
                                        'max_output_tokens': config.max_output_tokens}
            proposal, metadata = call_model(packet, manifest, config=config,
                on_response=record_response,
                on_transport=record_transport)
            write_json(destination / 'proposal.json', proposal.model_dump(mode='json'))
            package = enrich_package(package, proposal, origin='MODEL_API', provenance=metadata)
            diagnostic_run.finish(package)
            report['diagnostics'] = diagnostic_run.links()
            report['status'] = diagnostic_run.record['status']
            report['result'] = outcome(package)
            report['request_metadata'] = metadata
            exit_code = 3 if report['status'] in {'REJECTED', 'UNRESOLVED'} else 0
        except Exception as exc:
            if diagnostic_run and not diagnostic_run.closed:
                report['diagnostics'] = diagnostic_run.links()
                try:
                    diagnostic_run.fail(exc)
                except Exception:
                    report['diagnostics_error'] = '诊断保存未完成，请检查本机目录权限或磁盘空间。'
            report['status'] = 'FAILED'
            report['error'] = {'code': exc.code, 'message': exc.message} if isinstance(exc, InputError) else {
                'code': 'MODEL_PIPELINE_ERROR', 'message': '理解评测出现程序错误，请查看诊断记录。'}
            if getattr(exc, 'diagnostics', None):
                report['transport'] = exc.diagnostics
                if exc.diagnostics.get('response_metadata'):
                    report['request_metadata'] = exc.diagnostics['response_metadata']
            exit_code = 2
        report['elapsed_seconds'] = round(time.monotonic() - started, 3)
    write_json(destination / 'package.json', package.model_dump(mode='json'))
    write_json(destination / 'report.json', report)
    svg, page = render_report(package, packet, report)
    (destination / 'overlay.svg').write_text(svg, encoding='utf-8')
    (destination / 'index.html').write_text(page, encoding='utf-8')
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    print(json.dumps({'output': str(destination), 'status': report['status'],
                      'api_calls_attempted': report['api_calls_attempted'],
                      'error': report.get('error'), 'result': report.get('result')}, ensure_ascii=False))
    return exit_code


if __name__ == '__main__':
    raise SystemExit(main())
