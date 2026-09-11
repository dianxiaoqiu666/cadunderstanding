"""Offline model diagnostics: import a saved trial, validate a revision, or summarize errors.

This tool never loads credentials, calls a model, adopts a boundary or changes the active prompt.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pydantic import ValidationError
from services.understanding import evidence, exports
from services.understanding.contracts import CadPackage, SemanticProposal
from services.understanding.enrichment import enrich_package
from services.understanding.geometry import InputError
from services.understanding.model_runs import ModelRun, run_folder, summarize_runs, write_json


def import_evaluation(name):
    if not re.fullmatch(r'[a-zA-Z0-9_-]{1,80}', name):
        raise InputError('EVALUATION_NAME_INVALID', '评测名称无效。')
    source = ROOT / 'outputs/model-evaluations' / name
    if not source.resolve().is_relative_to((ROOT / 'outputs/model-evaluations').resolve()):
        raise InputError('EVALUATION_NAME_INVALID', '评测位置无效。')
    old = json.loads((source / 'report.json').read_text(encoding='utf-8-sig'))
    package = CadPackage.model_validate_json((source / 'package.json').read_text(encoding='utf-8-sig'))
    packet = json.loads((source / 'evidence/evidence.json').read_text(encoding='utf-8-sig'))
    manifest = json.loads((source / 'evidence/manifest.json').read_text(encoding='utf-8-sig'))
    if (old['source_sha256'] != package.source.sha256 or packet['source_sha256'] != package.source.sha256
            or manifest['source_sha256'] != package.source.sha256
            or old['evidence_sha256'] != packet['evidence_sha256']):
        raise InputError('MODEL_SOURCE_MISMATCH', '旧评测与源图纸不一致，不能合并。')
    for item in evidence.ARTIFACT_NAMES:
        if item == 'manifest.json':
            continue
        if evidence.digest((source / 'evidence' / item).read_bytes()) != manifest['artifact_sha256'].get(item):
            raise InputError('EVIDENCE_CACHE_CHANGED', '旧评测材料哈希发生变化。')
    record = ModelRun(package, origin='HISTORICAL_REVIEW', source_evaluation=name)
    # Copy the frozen historical packet, not a rebuilt packet with today's prompt.
    target = record.folder / 'evidence'
    target.mkdir()
    for item in evidence.ARTIFACT_NAMES:
        (target / item).write_bytes((source / 'evidence' / item).read_bytes())
    record.packet = packet
    record.record.update(evidence_sha256=packet['evidence_sha256'], prompt_version=packet['prompt_version'],
        prompt_sha256=packet['prompt_sha256'], input_hashes=manifest['artifact_sha256'], image_names=list(evidence.MODEL_IMAGE_NAMES),
        source_trial_calls=old.get('api_calls_attempted'), source_trial_status=old['status'],
        source_report_sha256=evidence.digest((source / 'report.json').read_bytes()))
    record.record['model'] = {'provider': old.get('provider'), 'model': old.get('requested_model'),
                              'meaning': 'Historical requested profile; returned model is known only from a saved receipt.'}
    if old.get('transport'):
        record.record['source_transport'] = old['transport']
    response = source / 'model-final-response.json'
    if response.is_file():
        record.on_response(json.loads(response.read_text(encoding='utf-8-sig')))
    else:
        record.record['response_missing'] = True
    if old.get('error'):
        record.fail(InputError(old['error']['code'], old['error']['message']))
    elif package.enrichment:
        record.finish(package)
    else:
        record.fail(InputError('NO_MODEL_RESULT', '历史评测没有可验证的模型提案；不能补造结果。'))
    return record


def validate_revision(identifier, proposal_path):
    parent = run_folder(identifier)
    previous = json.loads((parent / 'run.json').read_text(encoding='utf-8-sig'))
    package = CadPackage.model_validate_json((parent / 'base-package.json').read_text(encoding='utf-8-sig'))
    raw = proposal_path.read_bytes()
    if len(raw) > 2_000_000:
        raise InputError('PROPOSAL_TOO_LARGE', '修订提案超过 2 MB。')
    packet, manifest = evidence.prepare_evidence(package)
    if packet['evidence_sha256'] != previous.get('evidence_sha256'):
        raise InputError('MODEL_EVIDENCE_MISMATCH', '当前提示词或证据版本已变化，不能把新版本检查混写为旧版本修订。')
    record = ModelRun(package, origin='OFFLINE_REVISION', parent_run_id=identifier)
    record.attach_evidence(packet, manifest)
    record.record['revision_input_sha256'] = evidence.digest(raw)
    try:
        proposal = SemanticProposal.model_validate_json(raw)
        record.finish(enrich_package(package, proposal))
    except ValidationError as exc:
        record.record['schema_errors'] = [{'path': list(e['loc']), 'type': e['type']}
            for e in exc.errors(include_input=False, include_context=False, include_url=False)[:50]]
        record.fail(InputError('LLM_OUTPUT_INVALID', '离线修订不符合返回 Schema，错误字段已记录。'))
    except InputError as exc:
        record.fail(exc)
    return record


def import_result(identifier):
    """Review a saved web failure whose receipt identifies the exact model input."""
    if not re.fullmatch(r'[0-9a-f]{64}', identifier):
        raise InputError('RESULT_NOT_FOUND', '结果编号无效。')
    source = exports.RESULTS / (identifier + '.package.json')
    if not source.resolve().is_relative_to(exports.RESULTS.resolve()):
        raise InputError('RESULT_NOT_FOUND', '结果位置无效。')
    package = CadPackage.model_validate_json(source.read_text(encoding='utf-8-sig'))
    previous = package.provenance.get('analysis') or {}
    transport = previous.get('transport') or {}
    metadata = transport.get('response_metadata') or {}
    if previous.get('status') != 'FAILED':
        raise InputError('NO_MODEL_FAILURE', '该结果不是已保存的网页模型失败。')
    # Web failures append one issue after the request. It belongs in the review
    # log, not in the historical request. Only remove that exact message and
    # require the reconstructed input hash to match the saved receipt.
    package.issues = [issue for issue in package.issues
                      if not (issue.code == previous.get('code') and issue.message == previous.get('message'))]
    packet = evidence.build_evidence(package)
    if evidence.digest(evidence.json_bytes(evidence.build_model_input(packet))) != metadata.get('model_input_sha256'):
        raise InputError('MODEL_EVIDENCE_MISMATCH', '旧回执未能绑定当前图像与 JSON，不能补造历史输入。')
    packet, manifest = evidence.prepare_evidence(package)
    if (metadata.get('model_input_sha256') != manifest['artifact_sha256']['model-input.json']
            or metadata.get('image_sha256') != {name: manifest['artifact_sha256'][name] for name in evidence.MODEL_IMAGE_NAMES}):
        raise InputError('MODEL_EVIDENCE_MISMATCH', '旧回执未能绑定当前图像与 JSON，不能补造历史输入。')
    record = ModelRun(package, origin='HISTORICAL_REVIEW')
    record.attach_evidence(packet, manifest)
    record.record.update(source_result_id=identifier, source_result_sha256=evidence.digest(source.read_bytes()),
        source_trial_calls=int(bool(transport.get('http_request_started'))), source_transport=transport,
        response_metadata=metadata, response_missing=True,
        model={'provider': metadata.get('provider'), 'model': metadata.get('requested_model')})
    record.fail(InputError(previous.get('code', 'MODEL_PIPELINE_ERROR'), previous.get('message', '旧网页模型失败。')))
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest='action', required=True)
    actions.add_parser('summary', help='按错误类别汇总，不计算无真实标注支持的识别准确率')
    imported = actions.add_parser('import-evaluation', help='把已有评测导入审核记录，不重新调用模型')
    imported.add_argument('name')
    result = actions.add_parser('import-result', help='离线整理包含输入哈希与回执的旧网页失败结果')
    result.add_argument('result_id')
    revision = actions.add_parser('validate-revision', help='在相同源图与证据上离线检查一个修订提案')
    revision.add_argument('run_id')
    revision.add_argument('proposal', type=Path)
    args = parser.parse_args()
    try:
        if args.action == 'summary':
            summary = summarize_runs()
            destination = ROOT / 'outputs/model-feedback' / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
            write_json(destination / 'summary.json', summary)
            print(json.dumps({'summary': str(destination / 'summary.json'), 'run_count': summary['run_count'], 'model_calls': 0}))
            return 0
        if args.action == 'import-evaluation':
            record = import_evaluation(args.name)
        elif args.action == 'import-result':
            record = import_result(args.result_id)
        else:
            record = validate_revision(args.run_id, args.proposal)
        print(json.dumps({'run_id': record.id, 'status': record.record['status'], 'links': record.links(), 'model_calls': 0}, ensure_ascii=False))
        return 3 if record.record['status'] in {'REJECTED', 'FAILED', 'UNRESOLVED'} else 0
    except (OSError, ValueError, InputError):
        print(json.dumps({'error': '离线审核未完成，请检查输入文件、来源与证据版本；没有调用模型。'}, ensure_ascii=False))
        return 2


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    raise SystemExit(main())
