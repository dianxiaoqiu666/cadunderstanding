"""Export only the standalone CAD service's public schemas."""
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from services.understanding.contracts import CadPackage, Element, ParseOptions, UsableArea, PlacementRequest, PlacementResult
from services.understanding.contracts import SemanticProposal, EnrichmentResult, EnrichmentRequest, EnrichmentImportRequest


def main():
    target = ROOT / 'schemas'
    target.mkdir(exist_ok=True)
    for model in (CadPackage, Element, ParseOptions, UsableArea, PlacementRequest, PlacementResult,
                  SemanticProposal, EnrichmentResult, EnrichmentRequest, EnrichmentImportRequest):
        path = target / f'{model.__name__}.schema.json'
        text = json.dumps(model.model_json_schema(), ensure_ascii=False, indent=2) + '\n'
        if not path.exists() or path.read_text(encoding='utf-8-sig') != text:
            path.write_text(text, encoding='utf-8')


if __name__ == '__main__':
    main()
