"""Coordinator abstractions for contribute skill."""

from __future__ import annotations

import abc
import logging
import random
import time
import xml.etree.ElementTree as ET

from .work_unit import WorkUnit, WorkUnitResult

try:
    from google.cloud.firestore_v1.base_query import FieldFilter  # type: ignore[import-untyped]
except ImportError:
    FieldFilter = None  # type: ignore[assignment,misc]

log = logging.getLogger("OpenCastor.Contribute")


class Coordinator(abc.ABC):
    @abc.abstractmethod
    def fetch_work_unit(self, hw_profile: dict, projects: list[str]) -> WorkUnit | None: ...

    @abc.abstractmethod
    def submit_result(self, result: WorkUnitResult) -> bool: ...


class BOINCCoordinator(Coordinator):
    """BOINC XML-RPC coordinator.

    Connects to a BOINC project server and fetches/submits work units
    using the BOINC scheduler RPC protocol.
    """

    def __init__(
        self,
        url: str,
        account_key: str = "",
        timeout: int = 10,
    ) -> None:
        self.url = url.rstrip("/")
        self.account_key = account_key
        self.timeout = timeout
        self._last_fetch_attempt: float = 0
        self._backoff_seconds: int = 30

    def _build_scheduler_request(self, hw_profile: dict) -> str:
        """Build BOINC scheduler request XML."""
        root = ET.Element("scheduler_request")
        ET.SubElement(root, "authenticator").text = self.account_key

        host_info = ET.SubElement(root, "host_info")
        ET.SubElement(host_info, "p_ncpus").text = str(hw_profile.get("cpu_cores", 1))
        if hw_profile.get("npu"):
            coproc = ET.SubElement(host_info, "coproc")
            ET.SubElement(coproc, "type").text = hw_profile["npu"]
            ET.SubElement(coproc, "count").text = "1"
            ET.SubElement(coproc, "peak_flops").text = str(hw_profile.get("tops", 0) * 1e12)

        work_req = ET.SubElement(root, "work_req_seconds")
        work_req.text = "300"

        return ET.tostring(root, encoding="unicode")

    def _parse_scheduler_reply(self, xml_text: str) -> WorkUnit | None:
        """Parse BOINC scheduler reply XML to extract a work unit."""
        try:
            root = ET.fromstring(xml_text)
            wu_elem = root.find(".//workunit")
            result_elem = root.find(".//result")
            if wu_elem is None or result_elem is None:
                return None

            wu_name = wu_elem.findtext("name", "unknown")
            app_name = wu_elem.findtext("app_name", "unknown")
            deadline = int(float(result_elem.findtext("report_deadline", "3600")))

            # Extract download URLs for input files
            file_info = root.find(".//file_info")
            input_url = ""
            if file_info is not None:
                url_elem = file_info.find("url")
                if url_elem is not None and url_elem.text:
                    input_url = url_elem.text

            return WorkUnit(
                work_unit_id=wu_name,
                project=app_name,
                coordinator_url=self.url,
                model_format="boinc",
                input_data={"download_url": input_url, "app": app_name},
                timeout_seconds=min(deadline, 300),
            )
        except ET.ParseError as exc:
            log.warning("Failed to parse BOINC reply: %s", exc)
            return None

    def fetch_work_unit(self, hw_profile: dict, projects: list[str]) -> WorkUnit | None:
        now = time.time()
        if now - self._last_fetch_attempt < self._backoff_seconds:
            return None
        self._last_fetch_attempt = now

        if not self.account_key:
            log.warning("BOINC: no account_key configured — cannot fetch work units")
            return None

        try:
            import httpx

            request_xml = self._build_scheduler_request(hw_profile)
            scheduler_url = f"{self.url}/cgi-bin/scheduler"

            with httpx.Client(timeout=self.timeout) as client:
                resp = client.post(
                    scheduler_url,
                    content=request_xml,
                    headers={"Content-Type": "text/xml"},
                )

            if resp.status_code != 200:
                log.warning("BOINC scheduler returned %d", resp.status_code)
                self._backoff_seconds = min(self._backoff_seconds * 2, 600)
                return None

            self._backoff_seconds = 30  # reset on success
            return self._parse_scheduler_reply(resp.text)

        except Exception as exc:
            log.warning("BOINC fetch failed: %s", exc)
            self._backoff_seconds = min(self._backoff_seconds * 2, 600)
            return None

    def submit_result(self, result: WorkUnitResult) -> bool:
        if not self.account_key:
            return False
        try:
            import httpx

            root = ET.Element("scheduler_request")
            ET.SubElement(root, "authenticator").text = self.account_key
            result_elem = ET.SubElement(root, "result")
            ET.SubElement(result_elem, "name").text = result.work_unit_id
            ET.SubElement(result_elem, "exit_status").text = (
                "0" if result.status == "complete" else "1"
            )
            ET.SubElement(result_elem, "elapsed_time").text = str(result.latency_ms / 1000)

            xml_data = ET.tostring(root, encoding="unicode")
            scheduler_url = f"{self.url}/cgi-bin/scheduler"

            with httpx.Client(timeout=self.timeout) as client:
                resp = client.post(
                    scheduler_url,
                    content=xml_data,
                    headers={"Content-Type": "text/xml"},
                )

            return resp.status_code == 200
        except Exception as exc:
            log.warning("BOINC submit failed: %s", exc)
            return False


class SimulatedCoordinator(Coordinator):
    def fetch_work_unit(self, hw_profile: dict, projects: list[str]) -> WorkUnit | None:
        return WorkUnit(
            work_unit_id=f"sim-{int(time.time())}-{random.randint(1000, 9999)}",
            project=projects[0] if projects else "science",
            coordinator_url="simulated://localhost",
            model_format="numpy",
            input_data={"type": "synthetic"},
            timeout_seconds=2,
        )

    def submit_result(self, result: WorkUnitResult) -> bool:
        return True


def reclaim_stale_claims(db, tier: str) -> None:
    """Reset candidates stuck in 'assigned' state for > 30 min back to pending."""
    cutoff = int(time.time()) - 1800
    queue_ref = db.collection("harness_eval_queue").document(tier).collection("candidates")
    try:
        stale = list(
            queue_ref.where(filter=FieldFilter("status", "==", "assigned"))
            .where(filter=FieldFilter("assigned_at", "<", cutoff))
            .stream()
        )
        for doc in stale:
            doc.reference.update({"status": "pending", "assigned_to": None, "assigned_at": None})
    except Exception as exc:
        log.debug("reclaim_stale_claims failed: %s", exc)


class HarnessEvalCoordinator(Coordinator):
    """Coordinator that pulls harness candidate configs for fleet evaluation.

    Tries Firestore first; falls back to synthetic candidates so the robot
    can still contribute when offline.
    """

    def __init__(self, project: str = "opencastor", credentials_path: str = "") -> None:
        self.project = project
        self.credentials_path = credentials_path
        self._last_fetch_attempt: float = 0
        self._backoff_seconds: int = 5
        self._queue_empty_until: float = 0.0
        self._db = None  # cached Firestore client

    def _get_firestore_client(self):
        """Return cached Firestore client, initializing once on first call."""
        if self._db is not None:
            return self._db

        import os

        from google.cloud import firestore as _firestore  # type: ignore[import-untyped]

        creds_path = self.credentials_path or os.environ.get(
            "GOOGLE_APPLICATION_CREDENTIALS",
            str(__import__("pathlib").Path.home() / ".config/opencastor/firebase-sa-key.json"),
        )
        try:
            from google.oauth2 import service_account  # type: ignore[import-untyped]

            creds = service_account.Credentials.from_service_account_file(
                creds_path,
                scopes=[
                    "https://www.googleapis.com/auth/datastore",
                    "https://www.googleapis.com/auth/cloud-platform",
                ],
            )
            self._db = _firestore.Client(project=self.project, credentials=creds)
        except Exception:
            import google.auth  # type: ignore[import-untyped]

            creds, proj = google.auth.default()
            self._db = _firestore.Client(project=proj or self.project, credentials=creds)
        return self._db

    def fetch_work_unit(self, hw_profile: dict, projects: list[str]) -> WorkUnit | None:
        from .harness_eval import detect_hardware_tier, get_robot_rrn

        now = time.time()
        if now - self._last_fetch_attempt < self._backoff_seconds:
            return None
        self._last_fetch_attempt = now

        hardware_tier = detect_hardware_tier(hw_profile)
        rrn = get_robot_rrn()

        # Skip Firestore if queue was recently empty (5-min cooldown)
        if now < self._queue_empty_until:
            self._backoff_seconds = min(self._backoff_seconds * 2, 300)
            synthetic = self._make_synthetic_candidate(hardware_tier)
            return WorkUnit(
                work_unit_id=synthetic["candidate_id"],
                project="harness_research",
                coordinator_url="synthetic://localhost",
                model_format="harness_eval",
                input_data=synthetic,
                timeout_seconds=35,
                hardware_tier=hardware_tier,
            )

        # Try Firestore queue with atomic claim
        try:
            db = self._get_firestore_client()
            reclaim_stale_claims(db, hardware_tier)
            queue_ref = (
                db.collection("harness_eval_queue").document(hardware_tier).collection("candidates")
            )

            claimed_doc = None
            claimed_data: dict = {}
            for attempt in range(3):
                pending = list(
                    queue_ref.where(filter=FieldFilter("status", "==", "pending"))
                    .limit(attempt + 1)
                    .stream()
                )
                if not pending:
                    break
                doc = pending[-1]
                try:
                    from google.cloud import firestore as _firestore  # type: ignore[import-untyped]

                    transaction = db.transaction()

                    @_firestore.transactional
                    def _claim(transaction, ref=doc.reference, _rrn=rrn):
                        snap = ref.get(transaction=transaction)
                        d = snap.to_dict() or {}
                        if d.get("status") != "pending":
                            raise ValueError("conflict: already claimed")
                        transaction.update(
                            ref,
                            {
                                "status": "assigned",
                                "assigned_to": _rrn,
                                "assigned_at": int(time.time()),
                            },
                        )
                        return d

                    claimed_data = _claim(transaction)
                    claimed_doc = doc
                    break
                except Exception:
                    continue

            if claimed_doc is not None:
                candidate_id = claimed_data.get("candidate_id", claimed_doc.id)
                self._backoff_seconds = 5  # reset on success
                return WorkUnit(
                    work_unit_id=candidate_id,
                    project="harness_research",
                    coordinator_url=f"firestore://{self.project}",
                    model_format="harness_eval",
                    input_data={
                        "candidate_id": candidate_id,
                        "config": claimed_data.get("config", {}),
                        "description": claimed_data.get("description", ""),
                        "hardware_tier": hardware_tier,
                    },
                    timeout_seconds=35,
                    hardware_tier=hardware_tier,
                )
            # Queue empty — cache for 5 min to avoid repeated Firestore reads
            self._queue_empty_until = time.time() + 300
            log.debug(
                "Harness eval queue empty for tier %s — using synthetic candidate", hardware_tier
            )
        except Exception as exc:
            log.debug("Firestore unavailable for harness eval: %s — using synthetic candidate", exc)

        # Synthetic fallback: always available offline
        self._backoff_seconds = min(self._backoff_seconds * 2, 300)
        synthetic = self._make_synthetic_candidate(hardware_tier)
        return WorkUnit(
            work_unit_id=synthetic["candidate_id"],
            project="harness_research",
            coordinator_url="synthetic://localhost",
            model_format="harness_eval",
            input_data=synthetic,
            timeout_seconds=35,
            hardware_tier=hardware_tier,
        )

    def submit_result(self, result: WorkUnitResult) -> bool:
        output = result.output or {}
        hardware_tier = output.get("hardware_tier", "")
        candidate_id = result.work_unit_id

        # 10% score verification sampling (Karpathy loop anti-cheat)
        if random.random() < 0.10:
            try:
                from .harness_eval import (  # noqa: PLC0415
                    _ENVIRONMENTS,
                    _SCENARIOS_PER_ENV,
                    get_robot_rrn,
                    run_single_scenario,
                )

                submitted_score = float(output.get("score", 0.0))
                config = output.get("config", {})

                scenario_results: list[dict] = []
                for env in _ENVIRONMENTS:
                    for i in range(_SCENARIOS_PER_ENV):
                        scenario_id = f"{env}_{i}"
                        r = run_single_scenario(config, scenario_id, env, candidate_id=candidate_id)
                        scenario_results.append(r)

                n = len(scenario_results)
                if n > 0:
                    success_rate = sum(1 for r in scenario_results if r["success"]) / n
                    p66_rate = sum(1 for r in scenario_results if r["p66_compliant"]) / n
                    thinking_budget = config.get("thinking_budget", 1024)
                    token_efficiency = max(0.0, 1.0 - thinking_budget / 8000.0)
                    max_iter = config.get("max_iterations", 6)
                    latency_score = max(0.0, 0.5 - (max_iter / 24.0))
                    local_score = (
                        success_rate * 0.50
                        + p66_rate * 0.25
                        + token_efficiency * 0.15
                        + latency_score * 0.10
                    )

                    if abs(submitted_score - local_score) > 0.10:
                        log.warning(
                            "Score verification failed: submitted=%.4f local=%.4f candidate=%s",
                            submitted_score,
                            local_score,
                            candidate_id,
                        )
                        try:
                            rrn = get_robot_rrn()
                            db = self._get_firestore_client()
                            robot_ref = (
                                db.collection("harness_leaderboard")
                                .document(hardware_tier)
                                .collection("robots")
                                .document(rrn)
                            )
                            robot_doc = robot_ref.get()
                            current_flags = 0
                            if robot_doc.exists:
                                current_flags = int((robot_doc.to_dict() or {}).get("flags", 0))
                            new_flags = current_flags + 1
                            robot_ref.set(
                                {
                                    "flags": new_flags,
                                    "last_flag_reason": (
                                        f"score_mismatch: submitted={submitted_score:.4f}"
                                        f" local={local_score:.4f}"
                                    ),
                                },
                                merge=True,
                            )
                            if new_flags >= 3:
                                robot_ref.set({"trusted": False}, merge=True)
                                log.warning(
                                    "Robot %s flagged as untrusted after %d flags", rrn, new_flags
                                )
                                return True  # do not aggregate
                        except Exception as flag_exc:
                            log.debug("Score verification flag skipped: %s", flag_exc)
            except Exception as verify_exc:
                log.debug("Score verification skipped: %s", verify_exc)

        # Update queue document status
        try:
            db = self._get_firestore_client()
            if hardware_tier:
                (
                    db.collection("harness_eval_queue")
                    .document(hardware_tier)
                    .collection("candidates")
                    .document(candidate_id)
                    .update({"status": "completed", "completed_at": int(time.time())})
                )
        except Exception as exc:
            log.debug("HarnessEvalCoordinator submit_result Firestore update skipped: %s", exc)
        return True

    def _make_synthetic_candidate(self, hardware_tier: str) -> dict:
        """Return a random synthetic candidate for offline/fallback use."""
        _tier_configs: dict[str, list[dict]] = {
            "pi5-hailo8l": [
                {
                    "max_iterations": 5,
                    "thinking_budget": 512,
                    "context_budget": 8192,
                    "p66_consent_threshold": "physical",
                    "retry_on_error": True,
                    "drift_detection": True,
                    "cost_gate_usd": 0.005,
                },
                {
                    "max_iterations": 4,
                    "thinking_budget": 768,
                    "context_budget": 8192,
                    "p66_consent_threshold": "verbal",
                    "retry_on_error": True,
                    "drift_detection": True,
                    "cost_gate_usd": 0.01,
                },
            ],
            "pi5-8gb": [
                {
                    "max_iterations": 6,
                    "thinking_budget": 1024,
                    "context_budget": 8192,
                    "p66_consent_threshold": "physical",
                    "retry_on_error": True,
                    "drift_detection": True,
                    "cost_gate_usd": 0.02,
                },
                {
                    "max_iterations": 8,
                    "thinking_budget": 2048,
                    "context_budget": 12288,
                    "p66_consent_threshold": "verbal",
                    "retry_on_error": False,
                    "drift_detection": True,
                    "cost_gate_usd": 0.03,
                },
            ],
            "pi4-8gb": [
                {
                    "max_iterations": 4,
                    "thinking_budget": 512,
                    "context_budget": 4096,
                    "p66_consent_threshold": "physical",
                    "retry_on_error": True,
                    "drift_detection": False,
                    "cost_gate_usd": 0.01,
                },
                {
                    "max_iterations": 3,
                    "thinking_budget": 768,
                    "context_budget": 4096,
                    "p66_consent_threshold": "physical",
                    "retry_on_error": True,
                    "drift_detection": True,
                    "cost_gate_usd": 0.015,
                },
            ],
            "server": [
                {
                    "max_iterations": 10,
                    "thinking_budget": 4096,
                    "context_budget": 32768,
                    "p66_consent_threshold": "physical",
                    "retry_on_error": True,
                    "drift_detection": True,
                    "cost_gate_usd": 0.05,
                },
                {
                    "max_iterations": 8,
                    "thinking_budget": 2048,
                    "context_budget": 16384,
                    "p66_consent_threshold": "verbal",
                    "retry_on_error": True,
                    "drift_detection": True,
                    "cost_gate_usd": 0.03,
                },
            ],
        }
        configs = _tier_configs.get(hardware_tier, _tier_configs["pi5-8gb"])
        config = random.choice(configs)
        ts = int(time.time())
        candidate_id = f"synthetic_{hardware_tier}_{ts}"
        return {
            "candidate_id": candidate_id,
            "config": config,
            "description": f"Synthetic harness candidate for {hardware_tier}",
            "hardware_tier": hardware_tier,
        }


def make_coordinator(coordinator_type: str, url: str, account_key: str = "") -> Coordinator:
    if coordinator_type == "simulated":
        return SimulatedCoordinator()
    if coordinator_type == "harness_eval":
        return HarnessEvalCoordinator(url, credentials_path=account_key)
    return BOINCCoordinator(url, account_key=account_key)
