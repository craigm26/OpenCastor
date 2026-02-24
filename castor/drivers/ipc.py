from __future__ import annotations

import os
import tempfile
import threading
import time
from multiprocessing import Process
from multiprocessing.connection import Client, Listener, wait
from pathlib import Path
from typing import Any, Dict


class DriverIPCAdapter:
    """Proxy a hardware driver running in a dedicated worker process.

    The worker exposes a tiny RPC API over a Unix domain socket.  Calls are
    synchronous and bounded by ``rpc_timeout_s``.  A background heartbeat keeps
    the worker supervised; missed heartbeats force a fail-safe stop.
    """

    def __init__(
        self,
        sub_id: str,
        sub_cfg: dict,
        full_config: dict,
        *,
        rpc_timeout_s: float = 1.5,
        heartbeat_interval_s: float = 0.75,
        heartbeat_timeout_s: float = 3.0,
    ):
        self.sub_id = sub_id
        self._cfg = dict(sub_cfg)
        self._full_config = dict(full_config)
        self._rpc_timeout_s = max(0.1, float(rpc_timeout_s))
        self._heartbeat_interval_s = max(0.2, float(heartbeat_interval_s))
        self._heartbeat_timeout_s = max(self._heartbeat_interval_s * 2, float(heartbeat_timeout_s))
        self._lock = threading.Lock()
        self._alive = True
        self._last_heartbeat_ok = time.monotonic()

        sock_dir = Path(tempfile.gettempdir()) / "castor-drivers"
        sock_dir.mkdir(parents=True, exist_ok=True)
        self.socket_path = str(sock_dir / f"{os.getpid()}-{sub_id}.sock")
        try:
            os.unlink(self.socket_path)
        except FileNotFoundError:
            pass

        self._proc = Process(
            target=_driver_worker_main,
            args=(self.socket_path, self._cfg, self._full_config, self._heartbeat_timeout_s),
            daemon=True,
        )
        self._proc.start()

        self._wait_until_ready()

        self._hb_stop = threading.Event()
        self._hb_thread = threading.Thread(target=self._heartbeat_loop, daemon=True)
        self._hb_thread.start()

    def _wait_until_ready(self) -> None:
        deadline = time.monotonic() + self._rpc_timeout_s
        last_error = "worker not ready"
        while time.monotonic() < deadline:
            try:
                self._rpc("health_check", wait_timeout=0.2)
                return
            except Exception as exc:  # pragma: no cover - tiny timing window
                last_error = str(exc)
                time.sleep(0.05)
        raise RuntimeError(f"Driver worker {self.sub_id!r} failed to start: {last_error}")

    def _heartbeat_loop(self) -> None:
        while not self._hb_stop.wait(self._heartbeat_interval_s):
            try:
                self._rpc("heartbeat", wait_timeout=self._heartbeat_interval_s)
                self._last_heartbeat_ok = time.monotonic()
            except Exception:
                if time.monotonic() - self._last_heartbeat_ok > self._heartbeat_timeout_s:
                    self._alive = False
                    try:
                        self.stop()
                    except Exception:
                        pass
                    self.close()
                    return

    def _rpc(self, method: str, *args: Any, wait_timeout: float | None = None, **kwargs: Any) -> Any:
        if not self._alive:
            raise RuntimeError(f"driver worker {self.sub_id!r} is unavailable")

        timeout = self._rpc_timeout_s if wait_timeout is None else wait_timeout
        with self._lock:
            conn = Client(self.socket_path, family="AF_UNIX")
            try:
                conn.send({"method": method, "args": args, "kwargs": kwargs})
                if not conn.poll(timeout):
                    raise TimeoutError(f"RPC timeout waiting for {method} from {self.sub_id}")
                resp: Dict[str, Any] = conn.recv()
            finally:
                conn.close()

        if not resp.get("ok", False):
            raise RuntimeError(resp.get("error", f"RPC call failed: {method}"))
        return resp.get("result")

    def move(self, linear_or_action, angular: float = 0.0):
        return self._rpc("move", linear_or_action, angular)

    def stop(self):
        try:
            return self._rpc("stop")
        except Exception:
            return None

    def close(self):
        if not self._alive and not self._proc.is_alive():
            return
        self._alive = False
        self._hb_stop.set()
        try:
            self._rpc("close", wait_timeout=0.2)
        except Exception:
            pass
        if self._proc.is_alive():
            self._proc.join(timeout=0.8)
        if self._proc.is_alive():
            self._proc.terminate()
            self._proc.join(timeout=0.4)
        try:
            os.unlink(self.socket_path)
        except FileNotFoundError:
            pass

    def health_check(self) -> dict:
        if not self._proc.is_alive():
            return {"ok": False, "mode": "isolated", "error": "worker process exited"}
        try:
            res = self._rpc("health_check", wait_timeout=min(0.5, self._rpc_timeout_s))
            if isinstance(res, dict):
                res.setdefault("worker_pid", self._proc.pid)
                res.setdefault("socket_path", self.socket_path)
                return res
            return {"ok": True, "mode": "isolated", "worker_pid": self._proc.pid}
        except Exception as exc:
            return {"ok": False, "mode": "isolated", "error": str(exc), "worker_pid": self._proc.pid}


def _driver_worker_main(socket_path: str, sub_cfg: dict, full_config: dict, heartbeat_timeout_s: float) -> None:
    driver = None
    listener = None
    last_heartbeat = time.monotonic()
    try:
        from castor.drivers import get_driver as _get_driver

        mini_config = {**full_config, "drivers": [sub_cfg]}
        driver = _get_driver(mini_config)
        listener = Listener(socket_path, family="AF_UNIX")

        while True:
            if time.monotonic() - last_heartbeat > heartbeat_timeout_s:
                if driver is not None:
                    try:
                        driver.stop()
                    except Exception:
                        pass
                break

            ready = wait([listener], timeout=0.2)
            if not ready:
                continue

            conn = listener.accept()
            try:
                msg = conn.recv()
                method = msg.get("method")
                args = msg.get("args", ())
                kwargs = msg.get("kwargs", {})

                if method == "heartbeat":
                    last_heartbeat = time.monotonic()
                    conn.send({"ok": True, "result": {"ts": last_heartbeat}})
                    continue

                if method == "close":
                    if driver is not None:
                        try:
                            driver.close()
                        except Exception:
                            pass
                    conn.send({"ok": True, "result": None})
                    break

                if driver is None:
                    conn.send({"ok": False, "error": "driver failed to initialize"})
                    continue

                fn = getattr(driver, method, None)
                if fn is None:
                    conn.send({"ok": False, "error": f"unknown method: {method}"})
                    continue

                result = fn(*args, **kwargs)
                conn.send({"ok": True, "result": result})
            except Exception as exc:
                try:
                    conn.send({"ok": False, "error": str(exc)})
                except Exception:
                    pass
            finally:
                conn.close()
    finally:
        if listener is not None:
            try:
                listener.close()
            except Exception:
                pass
        if driver is not None:
            try:
                driver.close()
            except Exception:
                pass
        try:
            os.unlink(socket_path)
        except FileNotFoundError:
            pass
