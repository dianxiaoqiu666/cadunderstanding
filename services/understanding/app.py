"""Stable import entry for the standalone CAD service.

The original L3 entry is preserved under _archive/l3_import/.
"""
from services.understanding.api import app
from services.understanding.parser import parse_bytes

# Python callers and HTTP uploads now use the same CAD-only JSON contract.
understand_bytes = parse_bytes
parse_cad = parse_bytes

# Geometry-only compatibility for retained L3 observation utilities.
from services.understanding.geometry import (
    InputError, check_plane, digest, fail, hatch_polygons, point,
    polygon_data, strict_polyline,
)
