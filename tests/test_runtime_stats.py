"""Tests for castor.runtime_stats — token/data tracking and status bar generation."""

import json
import os
import time

import pytest

# Reset stats before every test so they don't bleed between tests.
@pytest.fixture(autouse=True)
def fresh_stats():
    from castor import runtime_stats as rs
    rs.reset()
    yield
    rs.reset()


# ── record_api_call ────────────────────────────────────────────────────────

def test_record_api_call_accumulates_tokens():
    from castor import runtime_stats as rs

    rs.record_api_call(tokens_in=100, tokens_out=50, model="claude-sonnet")
    rs.record_api_call(tokens_in=200, tokens_out=80, model="claude-sonnet")

    s = rs.get_stats()
    assert s["tokens_in"] == 300
    assert s["tokens_out"] == 130
    assert s["api_calls"] == 2
    assert s["last_model"] == "claude-sonnet"


def test_record_api_call_accumulates_bytes():
    from castor import runtime_stats as rs

    rs.record_api_call(bytes_in=1024, bytes_out=512)
    rs.record_api_call(bytes_in=512, bytes_out=256)

    s = rs.get_stats()
    assert s["bytes_in"] == 1536
    assert s["bytes_out"] == 768


def test_record_api_call_tracks_cached_tokens():
    from castor import runtime_stats as rs

    rs.record_api_call(tokens_in=500, tokens_cached=400)
    s = rs.get_stats()
    assert s["tokens_cached"] == 400


def test_record_api_call_model_update():
    from castor import runtime_stats as rs

    rs.record_api_call(model="model-a")
    assert rs.get_stats()["last_model"] == "model-a"
    rs.record_api_call(model="model-b")
    assert rs.get_stats()["last_model"] == "model-b"


def test_record_api_call_empty_model_does_not_overwrite():
    from castor import runtime_stats as rs

    rs.record_api_call(model="claude-sonnet")
    rs.record_api_call()  # no model arg
    assert rs.get_stats()["last_model"] == "claude-sonnet"


# ── record_tick ───────────────────────────────────────────────────────────

def test_record_tick_updates_tick_and_action():
    from castor import runtime_stats as rs

    rs.record_tick(42, "move_forward")
    s = rs.get_stats()
    assert s["tick"] == 42
    assert s["last_action"] == "move_forward"


def test_record_tick_empty_action_does_not_overwrite():
    from castor import runtime_stats as rs

    rs.record_tick(1, "stop")
    rs.record_tick(2)  # no action
    assert rs.get_stats()["last_action"] == "stop"


def test_record_tick_does_not_affect_token_counts():
    from castor import runtime_stats as rs

    rs.record_api_call(tokens_in=100, tokens_out=50)
    rs.record_tick(5, "turn_left")
    s = rs.get_stats()
    assert s["tokens_in"] == 100
    assert s["tokens_out"] == 50


# ── reset ─────────────────────────────────────────────────────────────────

def test_reset_clears_all_counters():
    from castor import runtime_stats as rs

    rs.record_api_call(tokens_in=999, tokens_out=888, model="test-model")
    rs.record_tick(100, "spin")
    rs.reset()
    s = rs.get_stats()
    assert s["tokens_in"] == 0
    assert s["tokens_out"] == 0
    assert s["api_calls"] == 0
    assert s["tick"] == 0
    assert s["last_action"] == "—"
    assert s["last_model"] == "—"


def test_reset_refreshes_session_start():
    from castor import runtime_stats as rs

    before = rs.get_stats()["session_start"]
    time.sleep(0.05)
    rs.reset()
    after = rs.get_stats()["session_start"]
    assert after > before


# ── file persistence ───────────────────────────────────────────────────────

def test_stats_written_to_json_file(tmp_path, monkeypatch):
    from castor import runtime_stats as rs

    fake_path = str(tmp_path / "runtime_stats.json")
    monkeypatch.setattr(rs, "_STATS_PATH", fake_path)
    os.makedirs(str(tmp_path), exist_ok=True)

    rs.record_api_call(tokens_in=77, tokens_out=33, model="test")
    assert os.path.exists(fake_path)
    with open(fake_path) as f:
        data = json.load(f)
    assert data["tokens_in"] == 77
    assert data["tokens_out"] == 33


def test_status_bar_file_written(tmp_path, monkeypatch):
    from castor import runtime_stats as rs

    bar_path = str(tmp_path / "status_bar.txt")
    monkeypatch.setattr(rs, "_STATUS_BAR_PATH", bar_path)

    rs.record_api_call(tokens_in=500, tokens_out=100, model="qwen-vl")
    assert os.path.exists(bar_path)
    content = open(bar_path).read()
    assert "500" in content or "0.5k" in content or "qwen" in content.lower()


# ── formatting helpers ────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "n, expected",
    [
        (0, "0"),
        (999, "999"),
        (1000, "1.0k"),
        (1500, "1.5k"),
        (1_000_000, "1.0M"),
        (2_500_000, "2.5M"),
    ],
)
def test_fmt_tokens(n, expected):
    from castor.runtime_stats import _fmt_tokens

    assert _fmt_tokens(n) == expected


@pytest.mark.parametrize(
    "n, expected",
    [
        (0, "0B"),
        (512, "512B"),
        (1024, "1.0KB"),
        (2048, "2.0KB"),
        (1_048_576, "1.0MB"),
    ],
)
def test_fmt_bytes(n, expected):
    from castor.runtime_stats import _fmt_bytes

    assert _fmt_bytes(n) == expected


@pytest.mark.parametrize(
    "secs, expected",
    [
        (0, "0s"),
        (45, "45s"),
        (90, "1m30s"),
        (3600, "1h0m"),
        (3661, "1h1m"),
    ],
)
def test_fmt_uptime(secs, expected):
    from castor.runtime_stats import _fmt_uptime

    assert _fmt_uptime(secs) == expected


# ── short_model ───────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "raw, expected_part",
    [
        ("anthropic/claude-sonnet-4-6", "sonnet-4-6"),
        ("google/gemini-3-flash-preview", "gemini-3-flash"),
        ("Qwen/Qwen2.5-VL-7B-Instruct", "Qwen2.5-VL-7B"),
        ("llama3", "llama3"),
    ],
)
def test_short_model(raw, expected_part):
    from castor.runtime_stats import _short_model

    result = _short_model(raw)
    assert expected_part in result
    assert len(result) <= 22


# ── get_status_bar_string fallback ────────────────────────────────────────

def test_get_status_bar_string_returns_fallback_when_no_file(tmp_path, monkeypatch):
    from castor import runtime_stats as rs

    monkeypatch.setattr(rs, "_STATUS_BAR_PATH", str(tmp_path / "nonexistent.txt"))
    result = rs.get_status_bar_string()
    assert isinstance(result, str)
    assert len(result) > 0


# ── thread safety ─────────────────────────────────────────────────────────

def test_concurrent_record_calls_dont_corrupt_state():
    import threading
    from castor import runtime_stats as rs

    def worker():
        for _ in range(50):
            rs.record_api_call(tokens_in=1, tokens_out=1)

    threads = [threading.Thread(target=worker) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    s = rs.get_stats()
    assert s["tokens_in"] == 500
    assert s["tokens_out"] == 500
    assert s["api_calls"] == 500
