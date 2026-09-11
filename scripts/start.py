"""Start this project's standalone CAD service on localhost."""
import argparse
import json
import os
from pathlib import Path
import socket
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8123)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error('port must be between 1 and 65535')
    os.chdir(ROOT)
    (ROOT / '.tmp').mkdir(exist_ok=True)
    os.environ['TEMP'] = os.environ['TMP'] = str(ROOT / '.tmp')
    with socket.socket() as probe:
        try:
            probe.bind(('127.0.0.1', args.port))
        except OSError:
            parser.error(f'Port {args.port} is occupied. Use --port with a free port.')
    import uvicorn
    import psutil
    runtime = ROOT / 'runtime'
    runtime.mkdir(exist_ok=True)
    process = psutil.Process()
    state_path = runtime / 'server.json'
    state_path.write_text(json.dumps({'pid': os.getpid(), 'create_time': process.create_time(),
                                    'cmdline': process.cmdline(), 'project_root': str(ROOT),
                                    'port': args.port}, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'CAD service: http://127.0.0.1:{args.port} (Ctrl+C stops this service)', flush=True)
    try:
        uvicorn.run('services.understanding.api:app', host='127.0.0.1', port=args.port)
    finally:
        if state_path.exists() and json.loads(state_path.read_text(encoding='utf-8-sig')).get('pid') == os.getpid():
            state_path.unlink()


if __name__ == '__main__':
    main()
