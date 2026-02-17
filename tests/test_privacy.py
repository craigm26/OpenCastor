"""Tests for castor.privacy -- default-deny policy for sensitive sensors."""

import os
from unittest.mock import patch

from castor.privacy import PrivacyPolicy


# =====================================================================
# PrivacyPolicy -- defaults
# =====================================================================
class TestPrivacyPolicyDefaults:
    @patch.dict(os.environ, {}, clear=True)
    def test_defaults(self):
        policy = PrivacyPolicy({})
        assert policy.camera_streaming is False
        assert policy.audio_recording is False
        assert policy.location_sharing is False
        assert policy.telemetry_collection is True
        assert policy.data_retention_days == 7


# =====================================================================
# PrivacyPolicy -- config overrides
# =====================================================================
class TestPrivacyPolicyConfigOverrides:
    @patch.dict(os.environ, {}, clear=True)
    def test_config_enables_camera(self):
        config = {"privacy": {"camera_streaming": True}}
        policy = PrivacyPolicy(config)
        assert policy.camera_streaming is True

    @patch.dict(os.environ, {}, clear=True)
    def test_config_enables_audio(self):
        config = {"privacy": {"audio_recording": True}}
        policy = PrivacyPolicy(config)
        assert policy.audio_recording is True

    @patch.dict(os.environ, {}, clear=True)
    def test_config_enables_location(self):
        config = {"privacy": {"location_sharing": True}}
        policy = PrivacyPolicy(config)
        assert policy.location_sharing is True

    @patch.dict(os.environ, {}, clear=True)
    def test_config_disables_telemetry(self):
        config = {"privacy": {"telemetry_collection": False}}
        policy = PrivacyPolicy(config)
        assert policy.telemetry_collection is False

    @patch.dict(os.environ, {}, clear=True)
    def test_config_custom_retention(self):
        config = {"privacy": {"data_retention_days": 30}}
        policy = PrivacyPolicy(config)
        assert policy.data_retention_days == 30


# =====================================================================
# PrivacyPolicy -- env var overrides
# =====================================================================
class TestPrivacyPolicyEnvOverrides:
    @patch.dict(os.environ, {"OPENCASTOR_ALLOW_CAMERA_STREAM": "true"}, clear=True)
    def test_env_overrides_config_camera(self):
        # Config says False, but env says true -> should be True
        config = {"privacy": {"camera_streaming": False}}
        policy = PrivacyPolicy(config)
        assert policy.camera_streaming is True

    @patch.dict(os.environ, {"OPENCASTOR_ALLOW_AUDIO_RECORD": "1"}, clear=True)
    def test_env_overrides_config_audio_with_1(self):
        policy = PrivacyPolicy({})
        assert policy.audio_recording is True

    @patch.dict(os.environ, {"OPENCASTOR_ALLOW_LOCATION": "yes"}, clear=True)
    def test_env_overrides_config_location_with_yes(self):
        policy = PrivacyPolicy({})
        assert policy.location_sharing is True

    @patch.dict(os.environ, {"OPENCASTOR_ALLOW_TELEMETRY": "false"}, clear=True)
    def test_env_overrides_config_telemetry_disable(self):
        # Telemetry defaults to True, but env says false -> should be False
        config = {"privacy": {"telemetry_collection": True}}
        policy = PrivacyPolicy(config)
        assert policy.telemetry_collection is False

    @patch.dict(os.environ, {"OPENCASTOR_ALLOW_CAMERA_STREAM": "false"}, clear=True)
    def test_env_override_false_disables(self):
        config = {"privacy": {"camera_streaming": True}}
        policy = PrivacyPolicy(config)
        assert policy.camera_streaming is False


# =====================================================================
# PrivacyPolicy.get_policy_summary (aliased as get_status in spec)
# =====================================================================
class TestPrivacyPolicyGetStatus:
    @patch.dict(os.environ, {}, clear=True)
    def test_get_status_returns_correct_dict(self):
        config = {
            "privacy": {
                "camera_streaming": True,
                "audio_recording": False,
                "location_sharing": False,
                "telemetry_collection": True,
                "data_retention_days": 14,
            }
        }
        policy = PrivacyPolicy(config)
        summary = policy.get_policy_summary()

        assert summary == {
            "camera_streaming": True,
            "audio_recording": False,
            "location_sharing": False,
            "telemetry_collection": True,
            "data_retention_days": 14,
        }

    @patch.dict(os.environ, {}, clear=True)
    def test_get_status_default_config(self):
        policy = PrivacyPolicy({})
        summary = policy.get_policy_summary()

        assert summary["camera_streaming"] is False
        assert summary["audio_recording"] is False
        assert summary["location_sharing"] is False
        assert summary["telemetry_collection"] is True
        assert summary["data_retention_days"] == 7
