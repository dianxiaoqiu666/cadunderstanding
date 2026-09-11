"""Copy the required installed distributions from an authorized local project.

Run with the source project's Python. A fresh target venv gets its own launcher;
RECORD files, shared modules and hashes are checked before/after copying.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata as metadata
import json
from pathlib import Path
import shutil
import subprocess
import sys

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

ROOT = Path(__file__).resolve().parents[1]
PACKAGES = ('ezdxf', 'shapely', 'fastapi', 'uvicorn', 'python-multipart',
            'openpyxl', 'httpx', 'pytest', 'psutil', 'networkx', 'packaging')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True, type=Path)
    args = parser.parse_args()
    source = args.source.resolve()
    site = source / '.venv/Lib/site-packages'
    if not Path(sys.prefix).resolve().is_relative_to(source):
        parser.error('Run this script using the source project .venv/Scripts/python.exe')
    if source == ROOT or not site.is_dir():
        parser.error('A separate source project with a Windows venv is required')
    selected = {}
    pending = list(PACKAGES)
    while pending:
        name = canonicalize_name(pending.pop())
        if name in selected:
            continue
        dist = metadata.distribution(name)
        if not Path(dist.locate_file('')).resolve().is_relative_to(site.resolve()):
            raise RuntimeError(f'Distribution outside authorized source environment: {name}')
        selected[name] = dist
        for text in dist.requires or []:
            req = Requirement(text)
            if req.marker is None or req.marker.evaluate({'extra': ''}):
                dependency = metadata.distribution(req.name)
                if req.specifier and dependency.version not in req.specifier:
                    raise RuntimeError(f'Unsatisfied source dependency: {text}')
                pending.append(req.name)

    # Collect and hash every input before any target copy.
    records = []
    for name, dist in sorted(selected.items()):
        for relative in dist.files or []:
            path = Path(dist.locate_file(relative)).resolve()
            if not path.is_relative_to(site.resolve()) or not path.is_file():
                continue  # Entry-point launchers are deliberately regenerated via python -m.
            if '__pycache__' in path.parts or path.suffix == '.pyc':
                continue
            records.append((path, ROOT / '.venv/Lib/site-packages' / path.relative_to(site.resolve()), sha(path)))
    for path in sorted((source / 'shared').glob('*.py')):
        records.append((path, ROOT / 'shared' / path.name, sha(path)))
    original_lock = source / 'requirements.lock.txt'
    records.append((original_lock, ROOT / 'control/imported/requirements.l3.lock.txt', sha(original_lock)))
    for path, target, checksum in records:
        if target.exists() and sha(target) != checksum:
            raise RuntimeError(f'Refusing to overwrite different target file: {target}')
    target_python = ROOT / '.venv/Scripts/python.exe'
    if not target_python.exists():
        subprocess.run([sys.executable, '-B', '-m', 'venv', str(ROOT / '.venv')], check=True)
    copied = []
    for path, target, checksum in records:
        if sha(path) != checksum:
            raise RuntimeError(f'Source changed during migration: {path}')
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            shutil.copy2(path, target)
        if sha(target) != checksum:
            raise RuntimeError(f'Copy digest mismatch: {target}')
        copied.append({'source': str(path.relative_to(source)),
                       'target': str(target.relative_to(ROOT)), 'sha256': checksum})
    lock = ''.join(f'{name}=={dist.version}\n' for name, dist in sorted(selected.items()))
    # Added after the source migration; setup installs this from PyPI separately.
    lock += 'pillow==12.3.0\n'
    (ROOT / 'requirements.lock.txt').write_text(lock, encoding='utf-8')
    report = {'source_project': str(source), 'target_project': str(ROOT),
              'source_python': sys.version, 'packages': {n: d.version for n, d in sorted(selected.items())},
              'files': copied, 'source_runtime_or_business_data_copied': False,
              'all_target_hashes_match': True}
    (ROOT / 'control/DEPENDENCY_MIGRATION.json').write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    subprocess.run([str(target_python), '-B', '-c',
                    'import ezdxf, shapely, fastapi, uvicorn, pytest, shared.contracts; '
                    'print("Independent environment import check: PASS")'], check=True, cwd=ROOT)
    print(json.dumps({'packages': len(selected), 'copied_files': len(copied), 'hashes': 'PASS'}))


if __name__ == '__main__':
    main()
