"""Prepare CAD evidence, invoke a configured model, or validate a saved reply."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pydantic import ValidationError
from services.understanding.cad_io import MAX_CAD_BYTES
from services.understanding.enrichment import enrich_package
from services.understanding.evidence import EVIDENCE_ROOT, prepare_evidence
from services.understanding.geometry import InputError
from services.understanding.model_client import call_model, load_config
from services.understanding.model_runs import ModelRun
from services.understanding.parser import parse_bytes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', type=Path)
    parser.add_argument('--options', type=Path)
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument('--prepare-only', action='store_true', help='默认：仅生成输入 JSON、编号 PNG、提示词和响应 Schema')
    actions.add_argument('--call-model', action='store_true', help='使用本机配置调用一次按量计费模型 API')
    actions.add_argument('--proposal', type=Path, help='离线导入 SemanticProposal JSON')
    parser.add_argument('-o', '--output', type=Path)
    parser.add_argument('--force', action='store_true')
    args = parser.parse_args()
    record = None
    try:
        target = args.output.resolve() if args.output else None
        if target and (not target.is_relative_to(ROOT) or target.suffix.lower() != '.json' or target == args.input.resolve()):
            raise InputError('OUTPUT_PATH_INVALID', '输出须为项目内独立 JSON 文件。')
        if target and target.exists() and not args.force:
            raise InputError('OUTPUT_EXISTS', '输出已存在，请更换文件名或使用 --force。')
        options = json.loads(args.options.read_text(encoding='utf-8-sig')) if args.options else {}
        with args.input.open('rb') as source:
            package = parse_bytes(source.read(MAX_CAD_BYTES + 1), args.input.name, options)
        evidence, manifest = prepare_evidence(package)
        code = 0
        if args.call_model:
            record = ModelRun(package)
            record.attach_evidence(evidence, manifest)
            config = load_config()
            record.configure(config)
            proposal, metadata = call_model(evidence, manifest, config=config,
                                           on_response=record.on_response, on_transport=record.on_transport)
            package = enrich_package(package, proposal, origin='MODEL_API', provenance=metadata)
        elif args.proposal:
            record = ModelRun(package, origin='IMPORTED_PROPOSAL')
            record.attach_evidence(evidence, manifest)
            package = enrich_package(package, json.loads(args.proposal.read_text(encoding='utf-8-sig')))
        if args.call_model or args.proposal:
            record.finish(package)
            package.enrichment.provenance['diagnostics'] = record.links()
            value = package.model_dump(mode='json')
            code = 3 if record.record['status'] in {'REJECTED', 'UNRESOLVED'} else 0
        else:
            value = {**manifest, 'local_directory': str(EVIDENCE_ROOT / evidence['evidence_sha256'])}
        text = json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + '\n'
        if target:
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open('w' if args.force else 'x', encoding='utf-8') as stream:
                stream.write(text)
            print(json.dumps({'output': str(target), 'evidence_id': evidence['evidence_sha256'],
                'status': package.enrichment.status if package.enrichment else 'EVIDENCE_PREPARED'}))
        else:
            sys.stdout.reconfigure(encoding='utf-8')
            sys.stdout.write(text)
        return code
    except Exception as exc:
        detail = {'code': exc.code, 'message': exc.message} if isinstance(exc, InputError) else {
            'code': 'MODEL_PIPELINE_ERROR' if record else 'INPUT_INVALID',
            'message': '处理未完成，请检查输入与本机诊断记录。'}
        if record:
            detail['diagnostics'] = record.links()
        if record and not record.closed:
            try:
                record.fail(exc)
            except Exception:
                detail['diagnostics_error'] = '诊断保存未完成，请检查本机目录权限或磁盘空间。'
        print(json.dumps({'error': detail}, ensure_ascii=True), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
