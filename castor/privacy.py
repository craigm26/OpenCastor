"""
OpenCastor Privacy -- default-deny policy for sensitive sensors.

Camera streaming, audio recording, and location data are denied by
default and require explicit opt-in via RCAN config or environment
variables. This protects users from inadvertent data exposure.

RCAN config format::

    privacy:
      camera_streaming: false       # Default: deny
      audio_recording: false        # Default: deny
      location_sharing: false       # Default: deny
      telemetry_collection: true    # Default: allow (anonymous usage stats)
      data_retention_days: 7        # How long to keep logs/recordings

Environment overrides (take precedence over config):
    OPENCASTOR_ALLOW_CAMERA_STREAM=true
    OPENCASTOR_ALLOW_AUDIO_RECORD=true
    OPENCASTOR_ALLOW_LOCATION=true
"""

import logging
import os

logger = logging.getLogger("OpenCastor.Privacy")


class PrivacyPolicy:
    """Enforces default-deny privacy policies for sensitive sensors."""

    def __init__(self, config: dict):
        privacy = config.get("privacy", {})

        # All sensor access is denied by default
        self.camera_streaming = self._resolve(
            "OPENCASTOR_ALLOW_CAMERA_STREAM",
            privacy.get("camera_streaming", False),
        )
        self.audio_recording = self._resolve(
            "OPENCASTOR_ALLOW_AUDIO_RECORD",
            privacy.get("audio_recording", False),
        )
        self.location_sharing = self._resolve(
            "OPENCASTOR_ALLOW_LOCATION",
            privacy.get("location_sharing", False),
        )
        self.telemetry_collection = self._resolve(
            "OPENCASTOR_ALLOW_TELEMETRY",
            privacy.get("telemetry_collection", True),
        )
        self.data_retention_days = privacy.get("data_retention_days", 7)

        self._log_policy()

    def _resolve(self, env_var: str, config_value: bool) -> bool:
        """Resolve a boolean setting: env var overrides config."""
        env_val = os.getenv(env_var)
        if env_val is not None:
            return env_val.lower() in ("true", "1", "yes")
        return config_value

    def _log_policy(self):
        """Log the resolved privacy policy."""
        policies = {
            "camera_streaming": self.camera_streaming,
            "audio_recording": self.audio_recording,
            "location_sharing": self.location_sharing,
            "telemetry_collection": self.telemetry_collection,
        }
        denied = [k for k, v in policies.items() if not v]
        allowed = [k for k, v in policies.items() if v]

        if denied:
            logger.info(f"Privacy: denied [{', '.join(denied)}]")
        if allowed:
            logger.info(f"Privacy: allowed [{', '.join(allowed)}]")

    def check_camera_stream(self) -> bool:
        """Check if camera streaming is allowed."""
        if not self.camera_streaming:
            logger.warning(
                "Camera streaming denied by privacy policy. "
                "Set privacy.camera_streaming: true in config to enable."
            )
        return self.camera_streaming

    def check_audio_record(self) -> bool:
        """Check if audio recording is allowed."""
        if not self.audio_recording:
            logger.warning(
                "Audio recording denied by privacy policy. "
                "Set privacy.audio_recording: true in config to enable."
            )
        return self.audio_recording

    def check_location(self) -> bool:
        """Check if location sharing is allowed."""
        if not self.location_sharing:
            logger.warning(
                "Location sharing denied by privacy policy. "
                "Set privacy.location_sharing: true in config to enable."
            )
        return self.location_sharing

    def get_policy_summary(self) -> dict:
        """Return a dict summary of the current privacy policy."""
        return {
            "camera_streaming": self.camera_streaming,
            "audio_recording": self.audio_recording,
            "location_sharing": self.location_sharing,
            "telemetry_collection": self.telemetry_collection,
            "data_retention_days": self.data_retention_days,
        }


def print_privacy_policy(config: dict):
    """Print the current privacy policy."""
    policy = PrivacyPolicy(config)
    summary = policy.get_policy_summary()

    try:
        from rich.console import Console
        from rich.table import Table

        console = Console()
        has_rich = True
    except ImportError:
        has_rich = False
        console = None

    if has_rich:
        table = Table(title="Privacy Policy", show_header=True)
        table.add_column("Setting", style="bold")
        table.add_column("Status")
        table.add_column("Override Env Var", style="dim")

        env_vars = {
            "camera_streaming": "OPENCASTOR_ALLOW_CAMERA_STREAM",
            "audio_recording": "OPENCASTOR_ALLOW_AUDIO_RECORD",
            "location_sharing": "OPENCASTOR_ALLOW_LOCATION",
            "telemetry_collection": "OPENCASTOR_ALLOW_TELEMETRY",
        }

        for key, value in summary.items():
            if key == "data_retention_days":
                table.add_row(key, f"{value} days", "")
            else:
                status = "[green]ALLOWED[/]" if value else "[red]DENIED[/]"
                table.add_row(key, status, env_vars.get(key, ""))

        console.print()
        console.print(table)
        console.print()
    else:
        print("\n  Privacy Policy:\n")
        for key, value in summary.items():
            if key == "data_retention_days":
                print(f"    {key}: {value} days")
            else:
                status = "ALLOWED" if value else "DENIED"
                print(f"    {key}: {status}")
        print()
