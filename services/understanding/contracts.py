"""Public CAD-to-JSON contract. Unknown dimensions never acquire defaults."""
from __future__ import annotations

from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, model_validator

Role = Literal['WALL', 'DOOR', 'WINDOW', 'COLUMN', 'BEAM', 'STAIR', 'SPACE',
               'EXCLUSION', 'HOLE', 'ANNOTATION', 'UNKNOWN']
Point = tuple[FiniteFloat, FiniteFloat]


class Model(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)


class Dimensions(Model):
    height_mm: FiniteFloat | None = Field(default=None, gt=0)
    thickness_mm: FiniteFloat | None = Field(default=None, gt=0)
    sill_height_mm: FiniteFloat | None = Field(default=None, ge=0)
    line_reference: Literal['UNKNOWN', 'CENTERLINE', 'LEFT_FACE', 'RIGHT_FACE'] = 'UNKNOWN'


class EntityOverride(Model):
    role: Role | None = None
    dimensions: Dimensions | None = None


class RegionInput(Model):
    """An explicit indoor ring or equipment footprint in CAD WCS millimeters."""
    boundary_mm: list[Point] = Field(min_length=3, max_length=20000)
    holes_mm: list[list[Point]] = Field(default_factory=list, max_length=1000)


class UsableAreaOptions(Model):
    # Supplying regions asserts that they are indoors; never inferred from a hull.
    indoor_regions: list[RegionInput] = Field(default_factory=list, max_length=100)
    boundary_clearance_mm: FiniteFloat = Field(default=0, ge=0, le=10000)
    obstacle_clearance_mm: FiniteFloat = Field(default=0, ge=0, le=10000)


class ParseOptions(Model):
    source_sha256: str | None = Field(default=None, pattern=r'^[0-9a-f]{64}$')
    layer_roles: dict[str, Role] = Field(default_factory=dict)
    entity_overrides: dict[str, EntityOverride] = Field(default_factory=dict)
    dimensions: dict[Role, Dimensions] = Field(default_factory=dict)
    # Explicit assumption only for unitless source files.
    assume_units: Literal['mm', 'cm', 'm', 'in', 'ft'] | None = None
    curve_tolerance_mm: FiniteFloat = Field(default=0.5, ge=0.01, le=10)
    usable_area: UsableAreaOptions = Field(default_factory=UsableAreaOptions)

    @model_validator(mode='after')
    def require_source_binding(self):
        if (self.entity_overrides or self.usable_area.indoor_regions) and self.source_sha256 is None:
            raise ValueError('entity_overrides / indoor_regions 必须携带本次输入的 source_sha256。')
        keys = [name.casefold() for name in self.layer_roles]
        if len(keys) != len(set(keys)):
            raise ValueError('layer_roles 不允许大小写冲突。')
        return self


class Geometry(Model):
    type: Literal['LINE', 'POLYLINE', 'ARC', 'CIRCLE', 'ELLIPSE', 'SPLINE',
                  'POLYGON', 'MULTIPOLYGON', 'POINT', 'TEXT', 'UNSUPPORTED']
    points_mm: list[Point] = Field(default_factory=list)
    closed: bool = False
    polygons: list[dict[str, Any]] = Field(default_factory=list)
    parameters: dict[str, Any] = Field(default_factory=dict)
    approximation_tolerance_mm: FiniteFloat | None = None


class Element(Model):
    id: str
    type: Role
    source_handles: list[str]
    source_path: list[str]
    source_type: str
    layer: str
    block_names: list[str] = Field(default_factory=list)
    geometry: Geometry
    confidence: Literal['EXPLICIT', 'CANDIDATE', 'UNKNOWN']
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    dimensions: Dimensions = Field(default_factory=Dimensions)
    dimension_sources: dict[str, str] = Field(default_factory=dict)


class Issue(Model):
    code: str
    message: str
    severity: Literal['INFO', 'WARNING', 'ERROR'] = 'WARNING'
    element_ids: list[str] = Field(default_factory=list)
    source_handles: list[str] = Field(default_factory=list)


class Source(Model):
    filename: str
    format: Literal['DXF', 'DWG']
    sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    byte_count: int = Field(gt=0)
    dxf_version: str
    insunits: int
    scale_to_mm: FiniteFloat = Field(gt=0)
    conversion: dict[str, Any] | None = None


class AreaPolygon(RegionInput):
    id: str
    area_mm2: FiniteFloat = Field(gt=0)
    area_m2: FiniteFloat = Field(gt=0)


class Obstacle(Model):
    element_id: str
    role: Role
    source_handles: list[str]
    basis: str
    polygons: list[AreaPolygon]


class Barrier(Model):
    element_id: str
    role: Role
    source_handles: list[str]
    paths_mm: list[list[Point]]
    footprint_known: bool


class UsableArea(Model):
    schema_version: Literal['usable-area/1.0'] = 'usable-area/1.0'
    status: Literal['READY', 'REVIEW_REQUIRED', 'NOT_READY', 'EMPTY'] = 'NOT_READY'
    ready_for_placement: bool = False
    source_sha256: str | None = None
    scope_source: Literal['NONE', 'SOURCE_BOUND_USER', 'EXPLICIT_CAD_SCOPE', 'EXPLICIT_CAD_ROOMS', 'MODEL_PROPOSAL'] = 'NONE'
    scope_element_ids: list[str] = Field(default_factory=list)
    indoor_regions: list[AreaPolygon] = Field(default_factory=list)
    regions: list[AreaPolygon] = Field(default_factory=list)
    obstacles: list[Obstacle] = Field(default_factory=list)
    barriers: list[Barrier] = Field(default_factory=list)
    excluded_regions: list[AreaPolygon] = Field(default_factory=list)
    indoor_area_m2: FiniteFloat | None = None
    excluded_area_m2: FiniteFloat | None = None
    usable_area_m2: FiniteFloat | None = None
    boundary_clearance_mm: FiniteFloat = 0
    obstacle_clearance_mm: FiniteFloat = 0
    issues: list[Issue] = Field(default_factory=list)
    # Regions plus barriers are the complete 2D constraints, not the rings alone.
    footprint_check_required: bool = True
    meaning: str = '已声明室内范围扣除已知占地；REVIEW_REQUIRED 的区域仅供核实，不能直接批准摆放。'


class PlacementRequest(Model):
    result_id: str = Field(pattern=r'^[0-9a-f]{64}$')
    footprint: RegionInput


class PlacementResult(Model):
    decision: Literal['VALID', 'INVALID', 'REVIEW_REQUIRED', 'NOT_READY']
    allowed: bool
    geometric_fit: bool
    reasons: list[str]
    colliding_element_ids: list[str]
    source_sha256: str


class RoleProposal(Model):
    element_id: str
    proposed_role: Role
    evidence_element_ids: list[str] = Field(min_length=1, max_length=100)
    reason: str = Field(min_length=1, max_length=2000)


class GapProposal(Model):
    from_vertex_id: str
    to_vertex_id: str
    reason: str = Field(min_length=1, max_length=2000)


class IndoorProposal(Model):
    id: str = Field(pattern=r'^[a-zA-Z0-9_-]{1,80}$')
    boundary_vertex_ids: list[str] = Field(min_length=3, max_length=1000)
    hole_vertex_ids: list[list[str]] = Field(max_length=100)
    gap_proposals: list[GapProposal] = Field(max_length=100)
    evidence_element_ids: list[str] = Field(min_length=1, max_length=200)
    reason: str = Field(min_length=1, max_length=2000)


class RelationProposal(Model):
    from_element_id: str
    to_element_id: str
    relation: Literal['HOSTED_BY', 'CONNECTS_TO', 'LABELS']
    evidence_element_ids: list[str] = Field(min_length=1, max_length=100)
    reason: str = Field(min_length=1, max_length=2000)


class SemanticUnresolved(Model):
    element_ids: list[str] = Field(max_length=100)
    question: str = Field(min_length=1, max_length=2000)


class AnchorCheck(Model):
    element_id: str
    position_relation: Literal['INSIDE', 'OUTSIDE', 'BOUNDARY', 'UNCERTAIN']
    reason: str = Field(min_length=1, max_length=1000)


class ScopeReview(Model):
    extent: Literal['WHOLE_STORE_CANDIDATE', 'PARTIAL_INTERIOR', 'UNRESOLVED']
    reviewed_view_ids: list[str] = Field(min_length=1, max_length=20)
    summary: str = Field(min_length=1, max_length=2000)
    anchor_checks: list[AnchorCheck] = Field(max_length=200)


class SemanticProposal(Model):
    """Model output: references only; coordinates and dimensions are not writable."""
    schema_version: Literal['cad-semantic-proposal/1.0', 'cad-semantic-proposal/2.0']
    source_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    evidence_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    role_proposals: list[RoleProposal] = Field(max_length=500)
    indoor_proposals: list[IndoorProposal] = Field(max_length=20)
    relation_proposals: list[RelationProposal] = Field(max_length=500)
    unresolved: list[SemanticUnresolved] = Field(max_length=500)
    scope_review: ScopeReview | None = None

    @model_validator(mode='after')
    def require_global_review(self):
        if self.schema_version == 'cad-semantic-proposal/2.0' and self.scope_review is None:
            raise ValueError('v2 requires scope_review')
        return self


class EnrichmentResult(Model):
    status: Literal['REVIEW_REQUIRED', 'UNRESOLVED', 'REJECTED']
    origin: Literal['MODEL_API', 'IMPORTED_PROPOSAL']
    proposal: SemanticProposal
    reference_validation: Literal['PASS', 'FAIL']
    issues: list[Issue]
    indoor_candidates: list[AreaPolygon] = Field(default_factory=list)
    usable_area_preview: UsableArea | None = None
    provenance: dict[str, Any] = Field(default_factory=dict)
    source_geometry_changed: Literal[False] = False
    semantic_confirmation: Literal['PENDING'] = 'PENDING'


class EnrichmentRequest(Model):
    result_id: str = Field(pattern=r'^[0-9a-f]{64}$')


class EnrichmentImportRequest(EnrichmentRequest):
    proposal: SemanticProposal


class CadPackage(Model):
    schema_version: Literal['cad-understanding/1.0'] = 'cad-understanding/1.0'
    status: Literal['COMPLETE', 'PARTIAL']
    source: Source
    units: Literal['mm'] = 'mm'
    coordinate_system: dict[str, Any]
    walls: list[Element] = Field(default_factory=list)
    doors: list[Element] = Field(default_factory=list)
    windows: list[Element] = Field(default_factory=list)
    columns: list[Element] = Field(default_factory=list)
    beams: list[Element] = Field(default_factory=list)
    stairs: list[Element] = Field(default_factory=list)
    spaces: list[Element] = Field(default_factory=list)
    exclusions: list[Element] = Field(default_factory=list)
    holes: list[Element] = Field(default_factory=list)
    annotations: list[Element] = Field(default_factory=list)
    unknown_objects: list[Element] = Field(default_factory=list)
    candidates: list[dict[str, Any]] = Field(default_factory=list)
    topology: dict[str, Any]
    reconstruction: dict[str, Any]
    usable_area: UsableArea = Field(default_factory=UsableArea)
    enrichment: EnrichmentResult | None = None
    inventory: list[dict[str, Any]]
    issues: list[Issue]
    summary: dict[str, Any]
    provenance: dict[str, Any]

    def components(self) -> list[Element]:
        return [item for group in GROUPS.values() for item in getattr(self, group)]


GROUPS = {'WALL': 'walls', 'DOOR': 'doors', 'WINDOW': 'windows', 'COLUMN': 'columns',
          'BEAM': 'beams', 'STAIR': 'stairs', 'SPACE': 'spaces', 'EXCLUSION': 'exclusions',
          'HOLE': 'holes', 'ANNOTATION': 'annotations', 'UNKNOWN': 'unknown_objects'}
