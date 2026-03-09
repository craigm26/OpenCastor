"""Tests for Dashboard Mission Control panel (Issue #283)."""

from __future__ import annotations

import types
from unittest.mock import MagicMock

import pytest

# ── Helper: stub out streamlit (not installed in test env) ────────────────────


def _make_streamlit_stub():
    """Create a minimal streamlit module stub that doesn't raise on attribute access."""
    st = types.ModuleType("streamlit")

    # Make any attribute access return a no-op mock
    class _StMeta(type):
        def __getattr__(cls, name):
            return MagicMock()

    class _St(metaclass=_StMeta):
        pass

    for attr in [
        "expander",
        "columns",
        "text_input",
        "button",
        "success",
        "info",
        "warning",
        "toast",
        "markdown",
        "metric",
        "caption",
        "divider",
        "sidebar",
        "write",
        "session_state",
        "set_page_config",
    ]:
        setattr(st, attr, MagicMock())

    # Make context managers work
    expander_ctx = MagicMock()
    expander_ctx.__enter__ = MagicMock(return_value=MagicMock())
    expander_ctx.__exit__ = MagicMock(return_value=False)
    st.expander = MagicMock(return_value=expander_ctx)

    col_mock = MagicMock()
    col_mock.__enter__ = MagicMock(return_value=col_mock)
    col_mock.__exit__ = MagicMock(return_value=False)
    st.columns = MagicMock(return_value=[col_mock, col_mock, col_mock])

    sidebar_mock = MagicMock()
    sidebar_mock.__enter__ = MagicMock(return_value=sidebar_mock)
    sidebar_mock.__exit__ = MagicMock(return_value=False)
    st.sidebar = sidebar_mock

    return st


# ── Import guard for dashboard_memory_timeline ────────────────────────────────


def test_memory_timeline_importable():
    from castor.dashboard_memory_timeline import MemoryTimeline

    assert MemoryTimeline is not None


def test_memory_timeline_get_outcome_summary_structure():
    from castor.dashboard_memory_timeline import MemoryTimeline

    tl = MemoryTimeline(db_path="/tmp/nonexistent_mc_test.db")
    result = tl.get_outcome_summary(window_h=24)
    assert "total" in result
    assert "outcomes" in result
    assert "ok_rate" in result


def test_memory_timeline_get_latency_percentiles_structure():
    from castor.dashboard_memory_timeline import MemoryTimeline

    tl = MemoryTimeline(db_path="/tmp/nonexistent_mc_test.db")
    result = tl.get_latency_percentiles(window_h=24)
    assert "p50_ms" in result
    assert "p95_ms" in result
    assert "p99_ms" in result
    assert "count" in result


def test_memory_timeline_ok_rate_range():
    from castor.dashboard_memory_timeline import MemoryTimeline

    tl = MemoryTimeline(db_path="/tmp/nonexistent_mc_test.db")
    result = tl.get_outcome_summary()
    assert 0.0 <= result["ok_rate"] <= 1.0


def test_memory_timeline_empty_returns_zero_total():
    from castor.dashboard_memory_timeline import MemoryTimeline

    tl = MemoryTimeline(db_path="/tmp/nonexistent_mc_test_empty.db")
    result = tl.get_outcome_summary()
    assert result["total"] == 0


# ── Mission Control API endpoint tests ────────────────────────────────────────


def test_behavior_run_endpoint_exists():
    """The /api/behavior/run endpoint should exist in the gateway."""
    try:
        from castor.gateway import app
        from fastapi.testclient import TestClient

        client = TestClient(app)
        # Just verify the endpoint is registered (405 is fine, 404 is not)
        response = client.get("/api/behavior/run")
        assert response.status_code != 404
    except ImportError:
        pytest.skip("FastAPI / gateway not available")


def test_behavior_stop_endpoint_exists():
    """The /api/behavior/stop endpoint should exist in the gateway."""
    try:
        from castor.gateway import app
        from fastapi.testclient import TestClient

        client = TestClient(app)
        response = client.get("/api/behavior/stop")
        assert response.status_code != 404
    except ImportError:
        pytest.skip("FastAPI / gateway not available")


def test_behavior_status_endpoint_exists():
    """The /api/behavior/status endpoint should exist in the gateway."""
    try:
        from castor.gateway import app
        from fastapi.testclient import TestClient

        client = TestClient(app)
        response = client.get("/api/behavior/status")
        assert response.status_code in (200, 401, 403)  # Not 404
    except ImportError:
        pytest.skip("FastAPI / gateway not available")


# ── Dashboard module checks ───────────────────────────────────────────────────


def test_dashboard_mission_control_section_in_source():
    """Verify the Mission Control section is present in dashboard.py source."""
    from pathlib import Path

    dashboard_path = Path("castor/dashboard.py")
    if not dashboard_path.exists():
        dashboard_path = Path("/home/craigm26/OpenCastor/castor/dashboard.py")
    assert dashboard_path.exists(), "dashboard.py not found"
    source = dashboard_path.read_text()
    assert "Mission Control" in source


def test_dashboard_has_memory_timeline_import():
    """Verify the dashboard imports the memory timeline."""
    from pathlib import Path

    dashboard_path = Path("castor/dashboard.py")
    if not dashboard_path.exists():
        dashboard_path = Path("/home/craigm26/OpenCastor/castor/dashboard.py")
    source = dashboard_path.read_text()
    assert "dashboard_memory_timeline" in source


def test_dashboard_mission_control_has_launch_button():
    from pathlib import Path

    dashboard_path = Path("castor/dashboard.py")
    if not dashboard_path.exists():
        dashboard_path = Path("/home/craigm26/OpenCastor/castor/dashboard.py")
    source = dashboard_path.read_text()
    assert "Launch Mission" in source or "mc_launch" in source


def test_dashboard_mission_control_has_stop_button():
    from pathlib import Path

    dashboard_path = Path("castor/dashboard.py")
    if not dashboard_path.exists():
        dashboard_path = Path("/home/craigm26/OpenCastor/castor/dashboard.py")
    source = dashboard_path.read_text()
    assert "Stop Mission" in source or "mc_stop" in source


# ── T-001: gamepad removal & D-pad HTML component ─────────────────────────────


def _dashboard_source() -> str:
    from pathlib import Path

    for candidate in ("castor/dashboard.py", "/home/craigm26/OpenCastor/castor/dashboard.py"):
        p = Path(candidate)
        if p.exists():
            return p.read_text()
    raise FileNotFoundError("dashboard.py not found")


def test_no_gamepad_button():
    """'Open Gamepad Controller' button must be removed from the control tab."""
    assert "Open Gamepad Controller" not in _dashboard_source()


def test_no_gamepad_link():
    """'/gamepad' link must not appear in the control tab section."""
    source = _dashboard_source()
    ctrl_start = source.find("with _tab_ctrl:")
    status_start = source.find("with _tab_status:")
    assert ctrl_start != -1, "_tab_ctrl block not found"
    assert status_start != -1, "_tab_status block not found"
    ctrl_section = source[ctrl_start:status_start]
    assert "/gamepad" not in ctrl_section


def test_dpad_html_component():
    """D-pad must use st.components.v1.html with pointer events and direction arrows."""
    source = _dashboard_source()
    assert "st.components.v1.html" in source
    # Locate the D-pad html block (after the speed/turn sliders)
    dpad_idx = source.find("hold-to-move")
    assert dpad_idx != -1, "D-pad HTML component marker not found"
    dpad_block = source[dpad_idx : dpad_idx + 4000]
    assert "pointerdown" in dpad_block
    assert "⬆" in dpad_block or "⬅" in dpad_block
