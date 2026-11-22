"""Standard-library HTTP dashboard bound to a loopback address.

GET  /                         single page (static/index.html)
GET  /api/overview             Overview as JSON
GET  /api/runs/<run_id>/curve  curve points for one run
POST /api/approvals/<id>       {"decision": "approve"|"reject"}
POST /api/workers/<id>/takeover {"on": true|false}
POST /api/stop                 set the user stop flag
POSTs require a JSON body and a same-origin Origin header; everything else is
read-only over the state directory.
"""

from __future__ import annotations

from autoexp.config.schema import DashboardConfig
from autoexp.state.layout import StateLayout


def serve(layout: StateLayout, config: DashboardConfig) -> None:
    raise NotImplementedError
