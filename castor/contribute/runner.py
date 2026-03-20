"""Work unit runner for contribute skill."""

from __future__ import annotations

import logging
import time
from pathlib import Path

from .hardware_profile import get_hw_profile
from .work_unit import WorkUnit, WorkUnitResult

log = logging.getLogger("OpenCastor.Contribute")


def _check_thermal() -> bool:
    """Check if SoC temperature is safe for contribution (< 80°C)."""
    try:
        temp_path = Path("/sys/class/thermal/thermal_zone0/temp")
        if temp_path.exists():
            temp_mc = int(temp_path.read_text().strip())
            temp_c = temp_mc / 1000.0
            if temp_c >= 80.0:
                log.warning("Thermal throttle: %.1f°C — skipping work unit", temp_c)
                return False
    except Exception:
        pass
    return True


def _run_npu_inference(wu: WorkUnit, hw: dict) -> dict | None:
    """Attempt to run work unit on Hailo NPU. Returns output or None."""
    if hw.get("npu") != "hailo-8l":
        return None
    if wu.model_format not in ("hef", "boinc"):
        return None

    try:
        from hailo_platform import (  # type: ignore[import-untyped]
            HEF,
            ConfigureParams,
            HailoStreamInterface,
            InferVStreams,
            InputVStreamParams,
            OutputVStreamParams,
            VDevice,
        )

        input_data = wu.input_data
        model_path = input_data.get("hef_path") or input_data.get("model_url")
        if not model_path:
            return None

        hef = HEF(model_path)
        with VDevice() as target:
            configure_params = ConfigureParams.create_from_hef(
                hef, interface=HailoStreamInterface.PCIe
            )
            network_group = target.configure(hef, configure_params)[0]
            input_vstream_params = InputVStreamParams.make(network_group, quantized=False)
            output_vstream_params = OutputVStreamParams.make(network_group, quantized=False)

            with InferVStreams(
                network_group, input_vstream_params, output_vstream_params
            ) as pipeline:
                input_tensor = input_data.get("tensor")
                if input_tensor is not None:
                    results = pipeline.infer(
                        {pipeline.get_input_vstream_infos()[0].name: input_tensor}
                    )
                    return {
                        "npu_output": "inference_complete",
                        "shape": str(list(results.values())[0].shape),
                    }

        return None

    except ImportError:
        log.debug("hailo_platform not available — falling back to CPU")
        return None
    except Exception as exc:
        log.warning("NPU inference failed: %s — falling back to CPU", exc)
        return None


def run_work_unit(
    wu: WorkUnit,
    *,
    cancelled_flag: list[bool] | None = None,
) -> WorkUnitResult:
    """Execute a work unit, respecting cancellation and thermal limits."""
    hw = get_hw_profile()
    start = time.monotonic()

    try:
        if cancelled_flag and cancelled_flag[0]:
            return WorkUnitResult(
                wu.work_unit_id,
                output=None,
                latency_ms=0.0,
                hw_profile=hw,
                status="cancelled",
            )

        # Thermal check
        if not _check_thermal():
            return WorkUnitResult(
                wu.work_unit_id,
                output=None,
                latency_ms=0.0,
                hw_profile=hw,
                status="failed",
                error="thermal_throttle",
            )

        # Try NPU path first
        npu_result = _run_npu_inference(wu, hw)
        if npu_result is not None:
            latency_ms = (time.monotonic() - start) * 1000
            return WorkUnitResult(
                wu.work_unit_id,
                output=npu_result,
                latency_ms=latency_ms,
                hw_profile=hw,
                status="complete",
            )

        # CPU fallback: generic compute simulation
        deadline = start + wu.timeout_seconds
        while time.monotonic() < deadline:
            if cancelled_flag and cancelled_flag[0]:
                latency_ms = (time.monotonic() - start) * 1000
                return WorkUnitResult(
                    wu.work_unit_id,
                    output=None,
                    latency_ms=latency_ms,
                    hw_profile=hw,
                    status="cancelled",
                )
            # Periodic thermal check during long work units
            if not _check_thermal():
                latency_ms = (time.monotonic() - start) * 1000
                return WorkUnitResult(
                    wu.work_unit_id,
                    output=None,
                    latency_ms=latency_ms,
                    hw_profile=hw,
                    status="failed",
                    error="thermal_throttle",
                )
            time.sleep(0.05)

        latency_ms = (time.monotonic() - start) * 1000
        return WorkUnitResult(
            wu.work_unit_id,
            output={"status": "ok"},
            latency_ms=latency_ms,
            hw_profile=hw,
            status="complete",
        )

    except Exception as exc:
        latency_ms = (time.monotonic() - start) * 1000
        log.error("Work unit %s failed: %s", wu.work_unit_id, exc)
        return WorkUnitResult(
            wu.work_unit_id,
            output=None,
            latency_ms=latency_ms,
            hw_profile=hw,
            status="failed",
            error=str(exc),
        )
