"""First-stage model evidence, invocation, and validated proposal import."""
import json
import re
from pathlib import Path
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from services.understanding import evidence, exports
from services.understanding.contracts import CadPackage, EnrichmentImportRequest, EnrichmentRequest
from services.understanding.enrichment import enrich_package
from services.understanding.geometry import InputError
from services.understanding.model_client import call_model, config_status, load_config
from services.understanding.model_runs import ModelRun

router = APIRouter(prefix='/v1/cad/enrichment', tags=['CAD 理解补全'])


def load_package(identifier):
    path = exports.RESULTS / f'{identifier}.package.json'
    if not path.is_file():
        raise HTTPException(404, detail={'code': 'RESULT_NOT_FOUND', 'message': '请先解析 CAD，再使用返回的 result_id。'})
    return CadPackage.model_validate_json(path.read_text(encoding='utf-8-sig'))


def error(exc):
    status = 503 if exc.code in {'LLM_NOT_CONFIGURED', 'LLM_CONFIG_INVALID'} else 502 if exc.code.startswith('LLM_') else 422
    detail = {'code': exc.code, 'message': exc.message}
    if getattr(exc, 'diagnostics', None):
        detail['transport'] = exc.diagnostics
    return HTTPException(status, detail=detail)


def recorded_error(exc, record):
    if isinstance(exc, HTTPException):
        return exc
    failure = error(exc) if isinstance(exc, InputError) else HTTPException(500, detail={
        'code': 'MODEL_PIPELINE_ERROR', 'message': '理解流程出现程序错误，请查看诊断记录。'})
    if record:
        failure.detail['diagnostics'] = record.links()
        if not record.closed:
            try:
                record.fail(exc)
            except Exception:
                failure.detail['diagnostics_error'] = '诊断保存未完成，请检查本机目录权限或磁盘空间。'
    return failure


@router.get('/config')
def configuration():
    return config_status()


@router.get('/providers')
def providers():
    return json.loads((Path(__file__).parent / 'model_providers.json').read_text(encoding='utf-8-sig'))


@router.post('/prepare')
def prepare(request: EnrichmentRequest):
    try:
        _, manifest = evidence.prepare_evidence(load_package(request.result_id))
        return manifest
    except InputError as exc:
        raise error(exc) from exc


@router.post('/run', response_model=CadPackage)
def run(request: EnrichmentRequest):
    record = None
    try:
        package = load_package(request.result_id)
        record = ModelRun(package)
        config = load_config()
        record.configure(config)
        packet, manifest = evidence.prepare_evidence(package)
        record.attach_evidence(packet, manifest)
        proposal, metadata = call_model(packet, manifest, config=config,
                                       on_response=record.on_response, on_transport=record.on_transport)
        result = enrich_package(package, proposal, origin='MODEL_API', provenance=metadata)
        record.finish(result)
        result.enrichment.provenance['diagnostics'] = record.links()
        return exports.save_package(result)
    except Exception as exc:
        raise recorded_error(exc, record) from exc


@router.post('/import', response_model=CadPackage)
def import_proposal(request: EnrichmentImportRequest):
    record = None
    try:
        package = load_package(request.result_id)
        record = ModelRun(package, origin='IMPORTED_PROPOSAL')
        packet, manifest = evidence.prepare_evidence(package)
        record.attach_evidence(packet, manifest)
        result = enrich_package(package, request.proposal)
        record.finish(result)
        result.enrichment.provenance['diagnostics'] = record.links()
        return exports.save_package(result)
    except Exception as exc:
        raise recorded_error(exc, record) from exc


@router.get('/evidence/{evidence_id}/{name}')
def download_evidence(evidence_id: str, name: str):
    if re.fullmatch(r'[0-9a-f]{64}', evidence_id) is None or name not in evidence.ARTIFACT_NAMES:
        raise HTTPException(404, detail='Evidence not found')
    path = evidence.EVIDENCE_ROOT / evidence_id / name
    if not path.is_file():
        raise HTTPException(404, detail='Evidence not found')
    if name.endswith('.png'):
        return FileResponse(path, media_type='image/png')
    return FileResponse(path, media_type='application/json' if name.endswith('.json') else 'text/plain', filename=name)
