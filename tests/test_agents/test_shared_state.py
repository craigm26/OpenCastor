"""Tests for SharedState — set/get, TTL expiry, pub/sub, thread safety."""

import threading
import time

from castor.agents.shared_state import SharedState


# ---------------------------------------------------------------------------
# Basic get / set
# ---------------------------------------------------------------------------


class TestSharedStateBasic:
    def test_set_and_get(self):
        state = SharedState()
        state.set("key", 42)
        assert state.get("key") == 42

    def test_get_missing_returns_none(self):
        state = SharedState()
        assert state.get("nonexistent") is None

    def test_get_missing_returns_custom_default(self):
        state = SharedState()
        assert state.get("missing", "fallback") == "fallback"

    def test_overwrite_value(self):
        state = SharedState()
        state.set("k", 1)
        state.set("k", 2)
        assert state.get("k") == 2

    def test_set_integer_value(self):
        state = SharedState()
        state.set("n", 0)
        assert state.get("n") == 0

    def test_set_string_value(self):
        state = SharedState()
        state.set("s", "hello")
        assert state.get("s") == "hello"

    def test_set_list_value(self):
        state = SharedState()
        state.set("lst", [1, 2, 3])
        assert state.get("lst") == [1, 2, 3]

    def test_set_dict_value(self):
        state = SharedState()
        payload = {"a": 1, "b": {"nested": True}}
        state.set("d", payload)
        assert state.get("d") == payload

    def test_set_bool_value(self):
        state = SharedState()
        state.set("flag", False)
        assert state.get("flag") is False

    def test_multiple_keys_independent(self):
        state = SharedState()
        state.set("a", 1)
        state.set("b", 2)
        assert state.get("a") == 1
        assert state.get("b") == 2


# ---------------------------------------------------------------------------
# keys() and snapshot()
# ---------------------------------------------------------------------------


class TestSharedStateIntrospection:
    def test_keys_empty_initially(self):
        state = SharedState()
        assert state.keys() == []

    def test_keys_after_set(self):
        state = SharedState()
        state.set("x", 1)
        state.set("y", 2)
        assert set(state.keys()) == {"x", "y"}

    def test_snapshot_empty_initially(self):
        state = SharedState()
        assert state.snapshot() == {}

    def test_snapshot_returns_all_values(self):
        state = SharedState()
        state.set("a", 10)
        state.set("b", 20)
        snap = state.snapshot()
        assert snap["a"] == 10
        assert snap["b"] == 20

    def test_snapshot_is_copy_not_reference(self):
        state = SharedState()
        state.set("k", [1, 2, 3])
        snap = state.snapshot()
        snap["k"].append(4)
        assert state.get("k") == [1, 2, 3]  # original unchanged

    def test_key_present_after_set(self):
        state = SharedState()
        state.set("mykey", "val")
        assert "mykey" in state.keys()


# ---------------------------------------------------------------------------
# TTL expiry
# ---------------------------------------------------------------------------


class TestSharedStateTTL:
    def test_expired_value_returns_default(self):
        state = SharedState()
        state.set("k", "val", ttl_s=0.01)
        time.sleep(0.05)
        assert state.get("k") is None

    def test_non_expired_value_available(self):
        state = SharedState()
        state.set("k", "val", ttl_s=60.0)
        assert state.get("k") == "val"

    def test_expired_key_missing_from_keys(self):
        state = SharedState()
        state.set("k", "val", ttl_s=0.01)
        time.sleep(0.05)
        assert "k" not in state.keys()

    def test_expired_key_missing_from_snapshot(self):
        state = SharedState()
        state.set("k", "val", ttl_s=0.01)
        time.sleep(0.05)
        assert "k" not in state.snapshot()

    def test_no_ttl_persists(self):
        state = SharedState()
        state.set("k", "persistent")
        time.sleep(0.05)
        assert state.get("k") == "persistent"

    def test_two_keys_different_ttl(self):
        state = SharedState()
        state.set("short", "gone", ttl_s=0.01)
        state.set("long", "here", ttl_s=60.0)
        time.sleep(0.05)
        assert state.get("short") is None
        assert state.get("long") == "here"


# ---------------------------------------------------------------------------
# Pub/sub
# ---------------------------------------------------------------------------


class TestSharedStatePubSub:
    def test_subscribe_returns_string_id(self):
        state = SharedState()
        sub_id = state.subscribe("k", lambda key, val: None)
        assert isinstance(sub_id, str)
        assert len(sub_id) > 0

    def test_callback_called_on_set(self):
        state = SharedState()
        received = []
        state.subscribe("k", lambda key, val: received.append((key, val)))
        state.set("k", 99)
        assert received == [("k", 99)]

    def test_multiple_subscribers_all_notified(self):
        state = SharedState()
        calls = []
        state.subscribe("k", lambda key, val: calls.append("A"))
        state.subscribe("k", lambda key, val: calls.append("B"))
        state.set("k", "x")
        assert len(calls) == 2
        assert "A" in calls
        assert "B" in calls

    def test_unsubscribe_stops_callback(self):
        state = SharedState()
        calls = []
        sub_id = state.subscribe("k", lambda key, val: calls.append(val))
        state.set("k", 1)
        state.unsubscribe(sub_id)
        state.set("k", 2)
        assert calls == [1]

    def test_subscriber_for_other_key_not_called(self):
        state = SharedState()
        calls = []
        state.subscribe("a", lambda key, val: calls.append(val))
        state.set("b", 99)
        assert calls == []

    def test_unsubscribe_unknown_id_no_error(self):
        state = SharedState()
        state.unsubscribe("nonexistent-uuid")  # must not raise

    def test_callback_exception_does_not_propagate(self):
        state = SharedState()

        def bad(key, val):
            raise RuntimeError("oops")

        state.subscribe("k", bad)
        state.set("k", 1)  # must not raise

    def test_callback_receives_updated_value(self):
        state = SharedState()
        last = []
        state.subscribe("x", lambda key, val: last.append(val))
        state.set("x", "first")
        state.set("x", "second")
        assert last == ["first", "second"]

    def test_two_keys_separate_subscribers(self):
        state = SharedState()
        a_calls, b_calls = [], []
        state.subscribe("a", lambda k, v: a_calls.append(v))
        state.subscribe("b", lambda k, v: b_calls.append(v))
        state.set("a", 1)
        state.set("b", 2)
        assert a_calls == [1]
        assert b_calls == [2]

    def test_unsubscribe_one_of_two_keeps_other(self):
        state = SharedState()
        calls_a, calls_b = [], []
        id_a = state.subscribe("k", lambda k, v: calls_a.append(v))
        state.subscribe("k", lambda k, v: calls_b.append(v))
        state.unsubscribe(id_a)
        state.set("k", 5)
        assert calls_a == []
        assert calls_b == [5]


# ---------------------------------------------------------------------------
# Thread safety
# ---------------------------------------------------------------------------


class TestSharedStateThreadSafety:
    def test_concurrent_writes_do_not_crash(self):
        state = SharedState()
        errors = []

        def writer(i):
            try:
                for j in range(100):
                    state.set(f"key_{i}", j)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=writer, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == []

    def test_concurrent_reads_and_writes(self):
        state = SharedState()
        state.set("shared", 0)
        errors = []

        def reader():
            try:
                for _ in range(200):
                    state.get("shared")
            except Exception as exc:
                errors.append(exc)

        def writer():
            try:
                for i in range(200):
                    state.set("shared", i)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=reader) for _ in range(4)]
        threads += [threading.Thread(target=writer) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == []

    def test_concurrent_subscribe_and_set(self):
        state = SharedState()
        errors = []

        def subscriber_thread():
            try:
                state.subscribe("key", lambda k, v: None)
            except Exception as exc:
                errors.append(exc)

        def setter_thread():
            try:
                for i in range(50):
                    state.set("key", i)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=subscriber_thread) for _ in range(5)]
        threads.append(threading.Thread(target=setter_thread))
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == []

    def test_concurrent_snapshot(self):
        state = SharedState()
        errors = []

        def snapshot_loop():
            try:
                for _ in range(50):
                    state.snapshot()
            except Exception as exc:
                errors.append(exc)

        def writer_loop():
            try:
                for i in range(50):
                    state.set(f"k{i}", i)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=snapshot_loop) for _ in range(3)]
        threads += [threading.Thread(target=writer_loop) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == []
