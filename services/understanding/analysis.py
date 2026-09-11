"""User-facing CAD analysis: source parsing followed by the single saved model."""
from services.understanding import evidence, model_client
from services.understanding.contracts import Issue
from services.understanding.enrichment import enrich_package
from services.understanding.geometry import InputError
from services.understanding.model_runs import ModelRun, review_status


def complete_analysis(package):
    run = None
    try:
        run = ModelRun(package)
        config = model_client.load_config()
        run.configure(config)
        if not config.base_url or not config.model or not model_client.resolve_api_key(config):
            package.provenance['analysis'] = {'status': 'NOT_CONFIGURED',
                'message': '已完成基础解析；模型设置尚未填写。'}
            run.finish(package)
            package.provenance['analysis']['diagnostics'] = run.links()
            return package
        packet, manifest = evidence.prepare_evidence(package)
        run.attach_evidence(packet, manifest)
        proposal, metadata = model_client.call_model(packet, manifest, config=config,
                                                    on_response=run.on_response, on_transport=run.on_transport)
        package = enrich_package(package, proposal, origin='MODEL_API', provenance=metadata)
        state = review_status(package)
        package.provenance['analysis'] = {'status': state if state in {'REJECTED', 'UNRESOLVED'} else 'COMPLETED',
            'model': config.model, 'message': '模型候选未通过校验，原因见诊断记录。' if state == 'REJECTED' else
            '模型尚不能确定完整空间，问题已记录。' if state == 'UNRESOLVED' else '已完成解析与理解，待核实项已保留。'}
        run.finish(package)
        package.provenance['analysis']['diagnostics'] = run.links()
    except Exception as exc:
        if run:
            package = run.package.model_copy(deep=True)
        if not isinstance(exc, InputError):
            code, message = 'MODEL_PIPELINE_ERROR', '理解流程发生程序错误，基础结果保留；请查看诊断记录。'
        else:
            code, message = exc.code, exc.message
        package.status = 'PARTIAL'
        package.provenance['analysis'] = {'status': 'FAILED', 'code': code,
            'message': '基础解析已保留，自动理解未完成：' + message}
        if getattr(exc, 'diagnostics', None):
            package.provenance['analysis']['transport'] = exc.diagnostics
        if run and not run.closed:
            try:
                run.fail(exc)
                package.provenance['analysis']['diagnostics'] = run.links()
            except Exception:
                package.provenance['analysis']['diagnostics_error'] = '诊断文件保存失败，请检查本机目录权限或磁盘空间。'
        package.issues.append(Issue(code=code, message=package.provenance['analysis']['message']))
    return package
