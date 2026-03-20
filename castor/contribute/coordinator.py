"""Coordinator abstractions for contribute skill."""

from __future__ import annotations

import abc
import logging
import random
import time
import xml.etree.ElementTree as ET

from .work_unit import WorkUnit, WorkUnitResult

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


def make_coordinator(coordinator_type: str, url: str, account_key: str = "") -> Coordinator:
    if coordinator_type == "simulated":
        return SimulatedCoordinator()
    return BOINCCoordinator(url, account_key=account_key)
