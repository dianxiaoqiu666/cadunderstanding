"""Local, source-bound model diagnostics and review material. Never calls a model."""
from collections import Counter
from datetime import datetime, timezone
import html
import json
from pathlib import Path
import re
import tempfile
import traceback
from uuid import uuid4

from services.understanding import evidence, model_client
from services.understanding.geometry import InputError
from services.understanding.review_image import render_review

REVIEW_PROMPT = Path(__file__).parent / 'prompts/cad_review_v1.txt'
REVIEW_PROMPT_VERSION = 'cad-semantic-review/1.0'
FILES = {'run.json', 'events.jsonl', 'base-package.json', 'result.json', 'proposal.json',
         'model-response.json', 'transport.json', 'feedback.json', 'feedback.md', 'review.png',
         'review-prompt.txt', 'index.html', 'files.json'} | {'evidence/' + name for name in evidence.ARTIFACT_NAMES}


def runs_root():
    # Config-path isolation also isolates logs in tests and alternate local instances.
    return model_client.CONFIG_PATH.parent / 'model-runs'


def run_folder(identifier):
    if not re.fullmatch(r'[0-9a-f]{32}', identifier):
        raise InputError('MODEL_RUN_NOT_FOUND', '诊断记录不存在。')
    folder = runs_root() / identifier
    if not folder.resolve().is_relative_to(runs_root().resolve()) or not (folder / 'run.json').is_file():
        raise InputError('MODEL_RUN_NOT_FOUND', '诊断记录不存在。')
    return folder


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, suffix='.tmp', delete=False, mode='w', encoding='utf-8') as stream:
        temporary = Path(stream.name)
        stream.write(json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + '\n')
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def error_category(code):
    if code in {'LLM_NOT_CONFIGURED', 'LLM_CONFIG_INVALID'}:
        return 'LOCAL_CONFIGURATION'
    if code in {'LLM_OUTPUT_INVALID', 'LLM_RESPONSE_INVALID'}:
        return 'OUTPUT_SCHEMA'
    if 'PROTOCOL' in code:
        return 'PROTOCOL'
    if code == 'LLM_OUTPUT_INCOMPLETE':
        return 'INCOMPLETE_RESPONSE'
    if code.startswith('LLM_'):
        return 'MODEL_CALL'
    if code.startswith(('MODEL_KEY', 'MODEL_LOG')):
        return 'LOCAL_CONFIGURATION'
    return 'PIPELINE'


def review_status(package):
    addition = package.enrichment
    if not addition:
        return 'NOT_CONFIGURED'
    if addition.status == 'REJECTED':
        return 'REJECTED'
    claim = (addition.provenance.get('scope_quality') or {}).get('model_extent_claim')
    if claim == 'UNRESOLVED' or not (addition.indoor_candidates or package.usable_area.indoor_regions):
        return 'UNRESOLVED'
    return addition.status


def summarize_runs():
    rows = []
    for path in sorted(runs_root().glob('*/run.json')):
        if (not re.fullmatch(r'[0-9a-f]{32}', path.parent.name)
                or not path.resolve().is_relative_to(runs_root().resolve())):
            continue
        item = json.loads(path.read_text(encoding='utf-8-sig'))
        if item.get('stage') != 'FINISHED':
            continue
        rows.append({'run_id': item['run_id'], 'source_sha256': item['source_sha256'],
            'origin': item['origin'], 'status': item['status'], 'category': item.get('category'),
            'model': (item.get('model') or {}).get('model'), 'prompt_sha256': item.get('prompt_sha256'),
            'transport_kind': (item.get('transport') or {}).get('transport_kind'),
            'error_codes': sorted({issue['code'] for issue in item.get('issues', [])} |
                                  ({item['error']['code']} if item.get('error') else set()))})
    return {'schema_version': 'cad-model-run-summary/1.0', 'run_count': len(rows),
            'counts_by_category': dict(Counter(row['category'] for row in rows)),
            'counts_by_error': dict(Counter(code for row in rows for code in row['error_codes'])),
            'semantic_accuracy': None, 'meaning': 'Error frequencies only; repeated/offline cases are not independent accuracy samples.',
            'runs': rows}


class ModelRun:
    def __init__(self, package, *, origin='MODEL_API', parent_run_id=None, source_evaluation=None):
        self.id = uuid4().hex
        self.folder = runs_root() / self.id
        self.package, self.packet, self.proposal = package, None, None
        self.closed = False
        self.record = {'schema_version': 'cad-model-run/1.0', 'run_id': self.id,
            'created_at_utc': datetime.now(timezone.utc).isoformat(), 'origin': origin,
            'parent_run_id': parent_run_id, 'source_evaluation': source_evaluation,
            'source_sha256': package.source.sha256,
            'options_sha256': evidence.digest(evidence.json_bytes(package.provenance.get('options', {}))),
            'status': 'IN_PROGRESS', 'stage': 'PREPARING', 'model_calls_this_run': 0,
            'model_call_started': False, 'semantic_confirmation': 'PENDING',
            'semantic_accuracy': None, 'issues': [], 'unresolved': [], 'geometry_checks': [],
            'meaning': 'Local consistency and error diagnostics; not human acceptance or measured semantic accuracy.'}
        try:
            self.folder.mkdir(parents=True)
            write_json(self.folder / 'base-package.json', package.model_dump(mode='json'))
            self.event('PREPARING')
        except OSError as exc:
            raise InputError('MODEL_LOG_WRITE_FAILED', '无法创建本机诊断记录，没有继续调用模型。') from exc

    def links(self):
        base = f'/v1/cad/model-runs/{self.id}/'
        return {'run_id': self.id, 'report_url': base + 'index.html', 'log_url': base + 'run.json',
                'feedback_url': base + 'feedback.json', 'review_image_url': base + 'review.png'}

    def event(self, stage, **fields):
        if self.closed:
            raise InputError('MODEL_RUN_CLOSED', '诊断记录已完成，不能覆盖；请创建新的修订记录。')
        self.record.update(stage=stage, updated_at_utc=datetime.now(timezone.utc).isoformat(), **fields)
        entry = {'at_utc': self.record['updated_at_utc'], 'stage': stage, **fields}
        with (self.folder / 'events.jsonl').open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(entry, ensure_ascii=False, allow_nan=False) + '\n')
        write_json(self.folder / 'run.json', self.record)

    def configure(self, config):
        self.record['model'] = {name: getattr(config, name) for name in (
            'provider', 'model', 'base_url', 'timeout_seconds', 'max_output_tokens',
            'response_format', 'reasoning_effort', 'enable_thinking')}
        self.event('CONFIGURED')

    def attach_evidence(self, packet, manifest):
        source = evidence.EVIDENCE_ROOT / packet['evidence_sha256']
        target = self.folder / 'evidence'
        target.mkdir()
        for name in evidence.ARTIFACT_NAMES:
            raw = (source / name).read_bytes()
            expected = manifest.get('artifact_sha256', {}).get(name)
            if name != 'manifest.json' and evidence.digest(raw) != expected:
                raise InputError('EVIDENCE_CACHE_CHANGED', '审核输入与保存的材料哈希不一致，未继续调用。')
            (target / name).write_bytes(raw)
        self.packet = packet
        self.record.update(evidence_sha256=packet['evidence_sha256'], prompt_version=packet['prompt_version'],
                           prompt_sha256=packet['prompt_sha256'], input_hashes=manifest['artifact_sha256'],
                           image_names=list(evidence.MODEL_IMAGE_NAMES))
        self.event('EVIDENCE_FROZEN')

    def on_transport(self, value):
        write_json(self.folder / 'transport.json', value)
        self.record['transport'] = value
        if value.get('http_request_started'):
            self.record['model_call_started'] = True
            self.record['model_calls_this_run'] = 1
        self.event(value.get('stage', 'TRANSPORT_UPDATED'))

    def on_response(self, value):
        # The client supplies final-text blocks and an allowlisted receipt, never its config/key/thinking.
        write_json(self.folder / 'model-response.json', value)
        self.record['response_metadata'] = value['metadata']
        self.event('RESPONSE_SAVED')

    def finish(self, result):
        addition = result.enrichment
        self.proposal = addition.proposal.model_dump(mode='json') if addition else None
        if self.proposal:
            write_json(self.folder / 'proposal.json', self.proposal)
        if addition:
            state = review_status(result)
            self.record.update(status=state, issues=[i.model_dump(mode='json') for i in addition.issues],
                unresolved=[i.model_dump(mode='json') for i in addition.proposal.unresolved],
                geometry_checks=addition.provenance.get('geometry_checks', []),
                scope_quality=addition.provenance.get('scope_quality'),
                reference_validation=addition.reference_validation,
                category='VALIDATION_REJECTED' if state == 'REJECTED' else
                         'MODEL_UNRESOLVED' if state == 'UNRESOLVED' else 'SEMANTIC_REVIEW')
            preview = addition.usable_area_preview
            self.record['area'] = {'indoor_area_m2': preview.indoor_area_m2 if preview else None,
                'usable_area_m2': preview.usable_area_m2 if preview else None,
                'obstacle_count': len(preview.obstacles) if preview else None,
                'ready_for_placement': False}
        else:
            self.record.update(status='NOT_CONFIGURED', category='LOCAL_CONFIGURATION')
        write_json(self.folder / 'result.json', result.model_dump(mode='json'))
        self.event('VALIDATION_RECORDED')
        self._close()

    def fail(self, exc):
        code = exc.code if isinstance(exc, InputError) else 'MODEL_PIPELINE_ERROR'
        message = exc.message if isinstance(exc, InputError) else '理解流程出现程序错误，基础结果保留；诊断记录包含代码位置。'
        stack = [{'file': Path(frame.filename).name, 'line': frame.lineno, 'function': frame.name}
                 for frame in traceback.extract_tb(exc.__traceback__)[-12:]] if exc.__traceback__ else []
        self.record.update(status='FAILED', category=error_category(code),
                           error={'code': code, 'message': message, 'type': type(exc).__name__, 'stack': stack})
        if getattr(exc, 'diagnostics', None):
            self.record['transport'] = exc.diagnostics
            self.record['schema_errors'] = exc.diagnostics.get('schema_errors', [])
            if exc.diagnostics.get('response_metadata'):
                self.record['response_metadata'] = exc.diagnostics['response_metadata']
        self.event('FAILURE_RECORDED')
        self._close()

    def _close(self):
        quality = self.record.get('scope_quality') or {}
        feedback = {'schema_version': 'cad-model-feedback/1.0', 'run_id': self.id,
            'source_sha256': self.record['source_sha256'], 'evidence_sha256': self.record.get('evidence_sha256'),
            'prompt_version': self.record.get('prompt_version'), 'prompt_sha256': self.record.get('prompt_sha256'),
            'status': self.record['status'], 'category': self.record.get('category'),
            'error': self.record.get('error'), 'schema_errors': self.record.get('schema_errors', []),
            'issues': self.record['issues'], 'unresolved': self.record['unresolved'],
            'geometry_checks': self.record['geometry_checks'], 'anchor_positions': quality.get('anchor_positions', []),
            'scope_quality': quality, 'proposed_indoor_count': len((self.proposal or {}).get('indoor_proposals', [])),
            'next_review': self._review_steps(), 'semantic_accuracy': None, 'semantic_confirmation': 'PENDING',
            'auto_retry': False, 'model_generated_text_is_untrusted_data': True}
        write_json(self.folder / 'feedback.json', feedback)
        prompt = REVIEW_PROMPT.read_bytes()
        (self.folder / 'review-prompt.txt').write_bytes(prompt)
        self.record['review_prompt'] = {'version': REVIEW_PROMPT_VERSION, 'sha256': evidence.digest(prompt),
                                       'active_base_prompt_changed': False}
        if self.packet:
            image = render_review(self.packet, self.proposal, status=self.record['status'],
                                  geometry_checks=self.record['geometry_checks'])
            if image:
                (self.folder / 'review.png').write_bytes(image)
        guide = '# 本次审核建议\n\n' + '\n'.join('- ' + line for line in feedback['next_review'])
        guide += '\n\n具体错误、文字位置、构件与顶点编号见 feedback.json；原图和原提示词见 evidence/。\n'
        guide += '\n本文件不是自动重试指令，审核提示词未替换当前运行提示词。\n'
        (self.folder / 'feedback.md').write_text(guide, encoding='utf-8')
        self.event('FINISHED')
        self._html(feedback)
        checksums = {p.relative_to(self.folder).as_posix(): evidence.digest(p.read_bytes())
                     for p in self.folder.rglob('*') if p.is_file()}
        write_json(self.folder / 'files.json', {'schema_version': 'cad-model-run-files/1.0', 'sha256': checksums})
        self.closed = True

    def _review_steps(self):
        category = self.record.get('category')
        steps = []
        if category in {'MODEL_CALL', 'LOCAL_CONFIGURATION', 'PROTOCOL', 'PIPELINE'}:
            steps.append('先核对调用/配置/程序错误；没有有效模型结论时，不从本次失败推断图纸语义或调整边界。')
        if category == 'INCOMPLETE_RESPONSE':
            steps.append('核对已记录的 stop_reason、finish_reason、usage 与最终文本，再决定是否调整输出限制；不要把不完整 JSON 当成候选。')
        if category == 'OUTPUT_SCHEMA':
            steps.append('按 schema_errors 的字段路径核对响应契约与原文；补齐字段和真实引用，不编造缺少的几何。')
        if self.record['geometry_checks'] or category == 'VALIDATION_REJECTED':
            steps.append('依错误中的 region_id、element_ids、vertex_ids 回看审核图与六张源图，检查缺边、越界、自交及孔洞。')
        if self.record['unresolved'] or category == 'MODEL_UNRESOLVED':
            steps.append('逐项保留无法确定的问题和所需证据，结合文字指向与空间位置复查，无法确定时保持 UNRESOLVED。')
        steps.append('结合全图、局部图、源坐标/线段与文字锚点核对整店主空间、入口、内凹和柱体；程序一致性不等于识别准确率。')
        steps.append('提示词修订另存版本，在同源案例及保留案例上对照；同时记录新错误与退化，不覆盖原实验或自动采用模型范围。')
        return steps

    def _html(self, feedback):
        visual = '<img src="review.png" alt="源 CAD 与模型候选的审核图">' if (self.folder / 'review.png').is_file() else '<p>本次未生成可用图像。</p>'
        detail = html.escape(json.dumps(feedback, ensure_ascii=False, indent=2))
        info = html.escape(json.dumps({k: self.record.get(k) for k in (
            'origin', 'model', 'model_calls_this_run', 'response_metadata', 'source_evaluation', 'source_result_id',
            'source_trial_calls', 'source_transport', 'response_missing', 'review_prompt')}, ensure_ascii=False, indent=2))
        page = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>CAD 诊断记录</title>
<link rel="alternate" type="application/json" title="错误与核验结果" href="feedback.json">
<link rel="alternate" type="application/json" title="调用与版本记录" href="run.json">
<style>body{font:15px/1.7 "Microsoft YaHei",sans-serif;background:#f4f6f5;color:#203d3d;margin:0}main{max-width:1100px;margin:28px auto;padding:0 20px}section,details{background:white;padding:22px;margin:18px 0;border:1px solid #dce4df;border-radius:10px}img{max-width:100%;height:auto}pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:13px;max-height:400px;overflow:auto}summary{cursor:pointer;font-size:16px;font-weight:600}summary:focus-visible{outline:2px solid #176a62;outline-offset:6px}a{color:#176a62}h1{font-size:24px}.notice{color:#925010}</style><main>'''
        page += f'<h1>CAD 诊断 · {html.escape(self.record["status"])}</h1><p>{self.id}</p>'
        page += '<p class="notice">仅用于核对失败原因和模型候选；程序校验通过也不代表整店范围已确认。</p>'
        page += '<p><a href="run.json" download>下载完整日志 JSON</a> · <a href="feedback.json" download>下载核验 JSON</a> · <a href="events.jsonl">阶段记录</a> · <a href="review-prompt.txt">审核提示词</a></p>'
        if self.packet:
            page += '<p>' + ' · '.join(f'<a href="evidence/{name}">{name}</a>' for name in evidence.MODEL_IMAGE_NAMES) + '</p>'
        page += f'<section>{visual}</section><details data-json-src="feedback.json"><summary>错误与核验结果</summary>'
        page += f'<p><a href="feedback.json">读取 JSON 接口</a> · <a href="feedback.json" download>下载 JSON</a></p><pre>{detail}</pre></details>'
        page += '<details data-json-src="run.json"><summary>调用与版本记录</summary>'
        page += f'<p><a href="run.json">读取 JSON 接口</a> · <a href="run.json" download>下载 JSON</a></p><pre>{info}</pre></details></main></html>'
        (self.folder / 'index.html').write_text(page, encoding='utf-8')
