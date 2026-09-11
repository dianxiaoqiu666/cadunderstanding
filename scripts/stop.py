"""Stop only the recorded live instance belonging to this project."""
import json
from pathlib import Path
import psutil

ROOT = Path(__file__).resolve().parents[1]
state_path = ROOT / 'runtime/server.json'
if not state_path.exists():
    raise SystemExit('No recorded CAD service instance.')
state = json.loads(state_path.read_text(encoding='utf-8-sig'))
if state.get('project_root') != str(ROOT):
    raise SystemExit('Project identity mismatch; no process was stopped.')
try:
    process = psutil.Process(state['pid'])
    if process.create_time() != state['create_time'] or process.cmdline() != state['cmdline']:
        raise SystemExit('Process identity changed; no process was stopped.')
    owned = process.children(recursive=True) + [process]
    for child in owned:
        try:
            child.terminate()
        except psutil.NoSuchProcess:
            pass
    _, survivors = psutil.wait_procs(owned, timeout=5)
    for child in survivors:
        child.kill()
except psutil.NoSuchProcess:
    pass
state_path.unlink(missing_ok=True)
print('CAD service stopped.')
