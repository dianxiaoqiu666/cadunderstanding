"""Local JSON attachments; downloads work without browser blob URL support."""
import hashlib
import json
from pathlib import Path
import tempfile

from services.understanding.contracts import CadPackage
from services.understanding.interpretation import interpreted_area, interpreted_components

RESULTS = Path(__file__).resolve().parents[2] / 'runtime/results'


def save_package(package: CadPackage) -> CadPackage:
    package.provenance['http_export_version'] = 2
    content = package.model_dump(mode='json')
    identifier = hashlib.sha256(json.dumps(content, sort_keys=True, separators=(',', ':'),
                                          ensure_ascii=False, allow_nan=False).encode('utf-8')).hexdigest()
    package.provenance['http_exports'] = {
        'package': f'/v1/cad/results/{identifier}/package',
        'components': f'/v1/cad/results/{identifier}/components',
        'usable-area': f'/v1/cad/results/{identifier}/usable-area',
        'options': f'/v1/cad/results/{identifier}/options',
        'enrichment': f'/v1/cad/results/{identifier}/enrichment',
        'interpreted-components': f'/v1/cad/results/{identifier}/interpreted-components',
        'interpreted-area': f'/v1/cad/results/{identifier}/interpreted-area',
        'result_id': identifier,
    }
    RESULTS.mkdir(parents=True, exist_ok=True)
    for name, value in [('package', package.model_dump(mode='json')),
                        ('components', [e.model_dump(mode='json') for e in package.components()]),
                        ('usable-area', package.usable_area.model_dump(mode='json')),
                        ('interpreted-components', [e.model_dump(mode='json') for e in interpreted_components(package)]),
                        ('interpreted-area', interpreted_area(package).model_dump(mode='json')),
                        ('enrichment', package.enrichment.model_dump(mode='json') if package.enrichment else None),
                        ('options', package.provenance['options'])]:
        target = RESULTS / f'{identifier}.{name}.json'
        if target.exists():
            continue
        data = json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + '\n'
        # Unique staging file + replace makes concurrent equal requests safe.
        with tempfile.NamedTemporaryFile(dir=RESULTS, suffix='.tmp', delete=False, mode='w', encoding='utf-8') as stream:
            temporary = Path(stream.name)
            stream.write(data)
        try:
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
    return package
