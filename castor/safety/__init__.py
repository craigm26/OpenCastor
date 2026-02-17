"""OpenCastor Safety — anti-subversion and prompt-injection defense."""

from castor.safety.anti_subversion import ScanResult, ScanVerdict, check_input_safety, scan_input

__all__ = ["ScanResult", "ScanVerdict", "scan_input", "check_input_safety"]
