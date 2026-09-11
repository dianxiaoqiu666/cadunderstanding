"""Read-only verification of this input CAD, migrated dependencies and archives."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def audit():
    cad = ROOT / '选定规划只留墙体.dxf'
    expected = '0753b00fae27eaa1355b9a3274789079ad9d2b33b28ba343f9afe5516d91468d'
    manifest_path = ROOT / 'control/DEPENDENCY_MIGRATION.json'
    migration = json.loads(manifest_path.read_text(encoding='utf-8-sig'))
    failures = []
    for item in migration['files']:
        path = (ROOT / item['target']).resolve()
        if not path.is_relative_to(ROOT) or not path.is_file() or digest(path) != item['sha256']:
            failures.append(item['target'])
    archived = json.loads((ROOT / 'control/IMPORT_ARCHIVE.json').read_text(encoding='utf-8-sig'))
    archive_failures = []
    for item in archived:
        path = Path(item['archive'])
        if not path.is_file() or digest(path).lower() != item['sha256'].lower():
            archive_failures.append(item['path'])
    source_ok = cad.is_file() and digest(cad) == expected
    return {'status': 'PASS' if source_ok and not failures and not archive_failures else 'FAIL',
            'cad_sha256': digest(cad) if cad.is_file() else None, 'cad_hash_unchanged': source_ok,
            'migrated_files_checked': len(migration['files']), 'migration_target_mismatches': failures,
            'archives_checked': len(archived), 'archive_mismatches': archive_failures}


if __name__ == '__main__':
    report = audit()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(0 if report['status'] == 'PASS' else 1)
