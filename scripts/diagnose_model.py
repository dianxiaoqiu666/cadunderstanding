"""Non-billable endpoint check: HEAD only, no API key, CAD, or model POST."""
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import httpx
from services.understanding.model_client import load_config, request_path
from services.understanding.model_transport import TransportAudit


def main():
    config = load_config()  # Deliberately never calls resolve_api_key.
    if not config.base_url:
        raise SystemExit('No configured API endpoint.')
    endpoint = config.base_url.rstrip('/') + request_path(config)
    probes = []
    for trust_env in (True, False):
        audit = TransportAudit()
        started = time.monotonic()
        record = {'route': 'environment' if trust_env else 'direct_diagnostic', 'method': 'HEAD'}
        try:
            with httpx.Client(timeout=httpx.Timeout(8, connect=5), trust_env=trust_env,
                              follow_redirects=False) as client:
                response = client.head(endpoint, extensions={'trace': audit.trace})
                record.update(http_status=response.status_code, status='HTTP_RESPONSE_RECEIVED')
        except httpx.HTTPError as exc:
            record.update(status='CONNECTION_FAILED', error_type=type(exc).__name__, stage=audit.snapshot()['stage'])
        record['elapsed_seconds'] = round(time.monotonic() - started, 3)
        probes.append(record)
    report = {'checked_at_utc': datetime.now(timezone.utc).isoformat(), 'endpoint': endpoint,
              'configured_model': config.model, 'model_calls': 0, 'api_key_sent': False, 'cad_sent': False,
              'authentication_verified': False, 'model_inference_verified': False,
              'note': 'HTTP 401 is expected for this unauthenticated HEAD probe; it does not test the saved key.',
              'probes': probes}
    with httpx.Client(timeout=5, trust_env=False) as client:
        try:
            response = client.get('http://127.0.0.1:8123/openapi.json')
            report['running_api_version'] = response.json().get('info', {}).get('version')
        except (httpx.HTTPError, ValueError):
            report['running_api_version'] = None
    destination = ROOT / 'outputs/model-diagnostics'
    destination.mkdir(parents=True, exist_ok=True)
    target = destination / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '.json')
    with target.open('x', encoding='utf-8') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    print(json.dumps({'output': str(target), **report}, ensure_ascii=False))
    return 0 if all(p['status'] == 'HTTP_RESPONSE_RECEIVED' for p in probes) else 2


if __name__ == '__main__':
    raise SystemExit(main())
