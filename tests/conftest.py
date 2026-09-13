"""Test-suite wide fixtures.

Since OC-13 the runtime files an incident from every estop, controlled stop
and guardian veto, and the default log lives at ~/.opencastor/incidents.jsonl.
Without this, every test that trips a stop appends to the operator's own log,
which is both noise in a real file and a chain that grows on each run. Point
the log at a per-run temporary file instead.

This runs at import time, before any test module imports castor.incidents, so
the module-level default picks it up.
"""

from __future__ import annotations

import os
import tempfile

if not os.environ.get("CASTOR_INCIDENT_LOG"):
    _incident_log_dir = tempfile.mkdtemp(prefix="castor-test-incidents-")
    os.environ["CASTOR_INCIDENT_LOG"] = os.path.join(_incident_log_dir, "incidents.jsonl")
