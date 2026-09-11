"""Standalone local HTTP service for both CAD formats."""
import json
from pathlib import Path
import re

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from pydantic import ValidationError

from services.understanding.cad_io import MAX_CAD_BYTES, find_converter
from services.understanding.contracts import CadPackage, Element, ParseOptions, PlacementRequest, PlacementResult, UsableArea
from services.understanding.geometry import InputError
from services.understanding.parser import parse_bytes
from services.understanding.exports import RESULTS, save_package
from services.understanding.usable_area import check_placement
from services.understanding.enrichment_api import router as enrichment_router
from services.understanding.model_settings import router as settings_router
from services.understanding.analysis import complete_analysis

app = FastAPI(title='CAD 理解服务', version='1.5.1',
              description='DXF / DWG → 建筑构件、室内可用区域、占地碰撞校验及 3D 构建待补事实。')
app.include_router(enrichment_router)
app.include_router(settings_router)


@app.get('/health')
def health():
    return {'status': 'ok', 'service': 'cad-understanding', 'schema_version': 'cad-understanding/1.0',
            'dxf_available': True, 'dwg_available': find_converter() is not None}


@app.get('/capabilities')
def capabilities():
    return {'input_formats': ['DXF', 'DWG'], 'dwg_available': find_converter() is not None,
            'max_upload_bytes': MAX_CAD_BYTES, 'output_units': 'mm',
            'supported_geometry': ['LINE', 'LWPOLYLINE', 'POLYLINE(2D)', 'ARC', 'CIRCLE',
                                   'ELLIPSE', 'SPLINE', 'HATCH(style 0, straight loops)',
                                   'SOLID', 'TRACE', '3DFACE(planar)', 'INSERT', 'TEXT', 'MTEXT'],
            'roles': ['WALL', 'DOOR', 'WINDOW', 'COLUMN', 'BEAM', 'STAIR', 'SPACE', 'EXCLUSION', 'HOLE'],
            'usable_area_available': True, 'placement_check_available': True,
            'semantic_enrichment_available': True, 'model_configuration_url': '/v1/cad/model-settings',
            'analysis_url': '/v1/cad/analyze',
            'options_schema': ParseOptions.model_json_schema(),
            'limits': ['仅解释模型空间的二维 XY 图形；不读取外部参照。',
                       'DWG 经本机 ODA 转为 DXF；转换坐标与原文件哈希分别记录。',
                       '门窗符号、紧凑填充和拓扑闭合面不自动成为开洞、柱或整店外边界。',
                       '真实高度、墙厚、墙线位置含义缺失时为 null/UNKNOWN。']}


def extract(file, options_json):
    try:
        value = json.loads(options_json) if options_json else {}
        options = ParseOptions.model_validate(value)
        return parse_bytes(file.file.read(MAX_CAD_BYTES + 1), file.filename or 'drawing.dxf', options)
    except InputError as exc:
        raise HTTPException(422, detail={'code': exc.code, 'message': exc.message}) from exc
    except (ValidationError, json.JSONDecodeError) as exc:
        raise HTTPException(422, detail={'code': 'OPTIONS_INVALID', 'message': str(exc)}) from exc


@app.post('/understand', response_model=CadPackage, include_in_schema=False)
@app.post('/v1/cad/parse', response_model=CadPackage)
def parse(file: UploadFile = File(...), options_json: str = Form('{}')):
    return save_package(extract(file, options_json))


@app.post('/v1/cad/analyze', response_model=CadPackage)
def analyze(file: UploadFile = File(...), options_json: str = Form('{}')):
    try:
        package = extract(file, options_json)
    except HTTPException as exc:
        if isinstance(exc.detail, dict):
            exc.detail.update(stage='CAD_PARSE', model_call_started=False)
            exc.detail['message'] += ' 本次未进入模型请求；页面仍保留上次结果。'
        raise
    return save_package(complete_analysis(package))


@app.post('/v1/cad/components', response_model=list[Element])
def components(file: UploadFile = File(...), options_json: str = Form('{}')):
    """A flat array; the full package additionally contains issues and readiness."""
    return extract(file, options_json).components()


@app.post('/v1/cad/usable-area', response_model=UsableArea)
def usable_area(file: UploadFile = File(...), options_json: str = Form('{}')):
    return save_package(extract(file, options_json)).usable_area


@app.post('/v1/cad/placement-check', response_model=PlacementResult)
def placement_check(request: PlacementRequest):
    path = RESULTS / f'{request.result_id}.package.json'
    if not path.is_file():
        raise HTTPException(404, detail={'code': 'RESULT_NOT_FOUND', 'message': '请先解析 CAD，再使用返回的 result_id 校验占地。'})
    package = CadPackage.model_validate_json(path.read_text(encoding='utf-8-sig'))
    try:
        return check_placement(package, request.footprint)
    except InputError as exc:
        raise HTTPException(422, detail={'code': exc.code, 'message': exc.message}) from exc


@app.get('/v1/cad/schema')
def schema():
    return JSONResponse(CadPackage.model_json_schema())


@app.get('/v1/cad/results/{result_id}/{kind}')
def download(result_id: str, kind: str):
    if re.fullmatch(r'[0-9a-f]{64}', result_id) is None or kind not in {'package', 'components', 'usable-area', 'options', 'enrichment', 'interpreted-components', 'interpreted-area'}:
        raise HTTPException(404, detail={'code': 'RESULT_NOT_FOUND', 'message': '结果不存在。'})
    path = RESULTS / f'{result_id}.{kind}.json'
    if not path.is_file():
        raise HTTPException(404, detail={'code': 'RESULT_NOT_FOUND', 'message': '结果不存在或已清理，请重新解析。'})
    return FileResponse(path, media_type='application/json', filename=f'cad-{result_id[:12]}-{kind}.json')


@app.get('/', include_in_schema=False)
def index():
    return FileResponse(Path(__file__).parent / 'static/index.html', media_type='text/html')


@app.get('/model-settings.js', include_in_schema=False)
def model_settings_script():
    return FileResponse(Path(__file__).parent / 'static/model-settings.js', media_type='application/javascript')


@app.get('/v1/cad/model-evaluations/{run_name}/{name:path}', include_in_schema=False)
def evaluation_report(run_name: str, name: str):
    from services.understanding.evidence import ARTIFACT_NAMES
    allowed = {'index.html', 'overlay.svg', 'report.json', 'package.json', 'proposal.json',
               'model-final-response.json', 'transport.json'} | {'evidence/' + item for item in ARTIFACT_NAMES}
    if re.fullmatch(r'[a-zA-Z0-9_-]{1,80}', run_name) is None or name not in allowed:
        raise HTTPException(404, detail='Evaluation not found')
    path = Path(__file__).resolve().parents[2] / 'outputs/model-evaluations' / run_name / name
    if not path.is_file():
        raise HTTPException(404, detail='Evaluation not found')
    return FileResponse(path, headers={'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'})


@app.get('/v1/cad/model-runs/{run_id}/{name:path}', include_in_schema=False)
def model_run_report(run_id: str, name: str):
    from services.understanding.model_runs import FILES, run_folder
    try:
        if name not in FILES:
            raise InputError('MODEL_RUN_NOT_FOUND', '诊断记录不存在。')
        folder = run_folder(run_id)
        path = folder / name
        if not path.is_file() or not path.resolve().is_relative_to(folder.resolve()):
            raise InputError('MODEL_RUN_NOT_FOUND', '诊断记录不存在。')
    except InputError:
        raise HTTPException(404, detail='Diagnostic not found') from None
    return FileResponse(path, headers={'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
        'Content-Security-Policy': "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; base-uri 'none'; frame-ancestors 'self'"})
