"""A consistent display/export view; original CAD facts remain in the package."""
from services.understanding.contracts import CadPackage


def interpreted_components(package: CadPackage):
    items = [element.model_copy(deep=True) for element in package.components()]
    enrichment = package.enrichment
    if not enrichment or enrichment.reference_validation != 'PASS':
        return items
    roles = {item.element_id: item for item in enrichment.proposal.role_proposals}
    for element in items:
        if element.id in roles:
            proposed = roles[element.id]
            element.type = proposed.proposed_role
            element.confidence = 'CANDIDATE'
            element.evidence.append({'kind': 'MODEL_PROPOSAL', 'reason': proposed.reason,
                'evidence_element_ids': proposed.evidence_element_ids, 'semantic_confirmation': 'PENDING'})
    return items


def interpreted_area(package: CadPackage):
    enrichment = package.enrichment
    if enrichment and enrichment.reference_validation == 'PASS' and enrichment.usable_area_preview is not None:
        return enrichment.usable_area_preview
    return package.usable_area
