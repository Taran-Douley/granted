"""Granted — a background agent that tells small UK organisations when *not*
to apply for funding.

The modules use package-relative imports, so import them through the package:

    from granted.config import model_id, region
    from granted.graph import build_graph, gate_passes

Scripts under `scripts/` put `src/` on `sys.path` before importing.
"""

from __future__ import annotations

__version__ = "0.1.0"
