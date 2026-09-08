"""The mDNS record, and the unit that keeps it on the air.

Everything here pins something that has actually failed on this bench. The
record's key names and its service type are a wire contract with an iOS app
that is not in this repository, and every way of getting them wrong produces
the SAME symptom — "I can't find the robot even though it's on the same
network" — with a healthy-looking `systemctl status` behind it. There is no
runtime error to catch, so these are the only thing standing between a rename
and a silent regression.
"""
from __future__ import annotations

import json
import socket
from pathlib import Path

import pytest

from castor import discovery
from castor.discovery import (
    LEGACY_SERVICE_TYPE,
    SERVICE_TYPE,
    RobotAdvertiser,
    RobotRecord,
    browse,
    decode,
    format_found,
    record_from_env,
)


def record(**over) -> RobotRecord:
    defaults = dict(
        rrn="RRN-LOCAL-abc123",
        name="testbot",
        gateway_port=8000,
        castor_port=8001,
        console_port=8002,
        manifest_path="/home/pi/testbot/ROBOT.md",
    )
    defaults.update(over)
    return RobotRecord(**defaults)


# ---------------------------------------------------------------------------
# The wire contract with the app
# ---------------------------------------------------------------------------


def test_THECONTRACT_service_type_is_the_one_the_app_browses():
    # RobotDiscovery.swift: `static let serviceType = "_opencastor._tcp"`.
    # The runtime published `_rcan._tcp` for months while the app browsed this,
    # which is a robot shouting down a channel nobody listens to.
    assert SERVICE_TYPE == "_opencastor._tcp.local."
    assert LEGACY_SERVICE_TYPE == "_rcan._tcp.local."


def test_THECONTRACT_txt_keys_are_exactly_what_the_app_reads():
    # Each of these is read by NAME out of the TXT record in
    # RobotDiscovery.swift's browseResultsChangedHandler. Renaming one here
    # breaks every phone on the LAN and nothing in this repo would notice.
    txt = record().txt()
    assert set(txt) == {
        "rrn",
        "name",
        "gateway_port",
        "console_port",
        "castor_port",
        "manifest_path",
        "v",
    }
    assert txt["rrn"] == "RRN-LOCAL-abc123"
    assert txt["gateway_port"] == "8000"
    assert txt["console_port"] == "8002"
    assert txt["castor_port"] == "8001"
    assert txt["manifest_path"] == "/home/pi/testbot/ROBOT.md"
    assert txt["name"] == "testbot"


def test_THEOPTIONAL_estop_url_is_omitted_not_sent_empty():
    # The app tests for the KEY, and falls back to
    # http://<host>:<castor_port>/api/stop when it is absent — which is exactly
    # what `castor up` writes into the QR. An empty string would beat that
    # correct fallback with nothing.
    assert "estop_url" not in record().txt()
    assert record(estop_url="http://10.0.0.4:9000/halt").txt()["estop_url"] == (
        "http://10.0.0.4:9000/halt"
    )


def test_the_estop_url_is_read_from_the_environment_when_set():
    got = record_from_env({"ROBOT_RRN": "RRN-4", "ROBOT_ESTOP_URL": "http://x/halt"})
    assert got.estop_url == "http://x/halt"


def test_the_check_says_where_a_missing_estop_would_be_derived_from():
    text = format_found([decode(FakeInfo(record().txt()))])
    assert "derived" in text and "/api/stop" in text


def test_every_txt_value_is_a_string():
    # zeroconf will encode ints, but the app reads `txt["gateway_port"]` as a
    # String and parses it. Keeping the producer stringly-typed keeps the two
    # sides looking at the same bytes.
    assert all(isinstance(v, str) for v in record().txt().values())


def test_the_record_carries_a_version_so_a_key_can_ever_change_meaning():
    assert record().txt()["v"] == discovery.RECORD_VERSION


def test_the_instance_name_is_the_rrn_not_the_human_name():
    # Two robots on one bench both called "robot" would collide, and zeroconf
    # would resolve it by renaming one to something the operator never chose.
    assert record(name="robot").instance_name == f"RRN-LOCAL-abc123.{SERVICE_TYPE}"


def test_srv_port_prefers_the_console_and_falls_back_to_the_runtime():
    # The console answers /console/health without a credential; a vehicle with
    # no console answers /health on the runtime. Either way the probe works.
    assert record().srv_port == 8002
    assert record(console_port=0).srv_port == 8001


# ---------------------------------------------------------------------------
# Reading the environment the other units already set
# ---------------------------------------------------------------------------


def test_the_record_reads_the_env_the_discovery_unit_writes():
    got = record_from_env(
        {
            "ROBOT_RRN": "RRN-000000000012",
            "ROBOT_NAME": "rover",
            "ROBOT_GATEWAY_PORT": "8081",
            "ROBOT_CASTOR_PORT": "8003",
            "ROBOT_CONSOLE_PORT": "8003",
            "ROBOT_MANIFEST_PATH": "/home/pi/rover/ROBOT.md",
        }
    )
    assert got == RobotRecord(
        rrn="RRN-000000000012",
        name="rover",
        gateway_port=8081,
        castor_port=8003,
        console_port=8003,
        manifest_path="/home/pi/rover/ROBOT.md",
    )


def test_it_also_reads_the_spellings_the_castor_unit_uses():
    # The castor unit sets ROBOT_MANIFEST, ROBOT_RUNTIME_PORT and a gateway
    # URL rather than a port. Refusing those would make the packaged advertiser
    # unable to read the very units it ships beside.
    got = record_from_env(
        {
            "ROBOT_RRN": "RRN-1",
            "ROBOT_MANIFEST": "/srv/robot/ROBOT.md",
            "ROBOT_RUNTIME_PORT": "8101",
            "ROBOT_GATEWAY_URL": "http://127.0.0.1:8100",
        }
    )
    assert got.manifest_path == "/srv/robot/ROBOT.md"
    assert got.castor_port == 8101
    assert got.gateway_port == 8100


def test_robot_home_alone_is_enough(tmp_path: Path):
    # A unit that sets nothing but ROBOT_HOME still advertises a correct
    # record: the identity `up` reused is on disk, next to the manifest.
    (tmp_path / ".castor-up.json").write_text(
        json.dumps({"rrn": "RRN-LOCAL-fromdisk", "uuid": "u-1", "base_port": 8000})
    )
    got = record_from_env({"ROBOT_HOME": str(tmp_path)})
    assert got.rrn == "RRN-LOCAL-fromdisk"
    assert got.manifest_path == str(tmp_path / "ROBOT.md")


def test_THERULE_no_rrn_is_a_refusal_not_a_blank_record():
    # A record with no identity is worse than no record: it makes a robot LOOK
    # discoverable while matching nothing the app holds in its Keychain, which
    # is a bug report with no evidence in it.
    with pytest.raises(ValueError) as exc:
        record_from_env({"ROBOT_NAME": "nameless"})
    assert "RRN" in str(exc.value)


def test_a_damaged_state_file_is_not_fatal_it_is_just_no_rrn(tmp_path: Path):
    (tmp_path / ".castor-up.json").write_text("{not json")
    with pytest.raises(ValueError):
        record_from_env({"ROBOT_HOME": str(tmp_path)})


def test_the_name_defaults_to_the_rrn_never_to_empty():
    assert record_from_env({"ROBOT_RRN": "RRN-2"}).name == "RRN-2"


def test_garbage_ports_fall_back_instead_of_crashing_the_unit():
    got = record_from_env({"ROBOT_RRN": "RRN-3", "ROBOT_CONSOLE_PORT": "not-a-port"})
    assert got.console_port == discovery.DEFAULT_CONSOLE_PORT


# ---------------------------------------------------------------------------
# What actually goes on the wire
# ---------------------------------------------------------------------------


def test_the_service_info_carries_the_record_verbatim():
    zeroconf = pytest.importorskip("zeroconf")
    assert zeroconf  # the import is the point
    info = RobotAdvertiser(record()).service_info("192.0.2.7")
    assert info.type == SERVICE_TYPE
    assert info.name == f"RRN-LOCAL-abc123.{SERVICE_TYPE}"
    assert info.port == 8002
    assert socket.inet_ntoa(info.addresses[0]) == "192.0.2.7"
    decoded = decode(info)
    assert decoded.rrn == "RRN-LOCAL-abc123"
    assert decoded.gateway_port == 8000
    assert decoded.manifest_path == "/home/pi/testbot/ROBOT.md"


def test_an_advertiser_without_zeroconf_says_so_and_returns_false(monkeypatch):
    # The unit must fail loudly rather than sit "active (running)" publishing
    # nothing — that green dot over a dead record is the original bug.
    import builtins

    real_import = builtins.__import__

    def no_zeroconf(name, *args, **kwargs):
        if name == "zeroconf":
            raise ImportError("no zeroconf here")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_zeroconf)
    assert RobotAdvertiser(record()).start() is False


def test_lan_ip_is_an_address_and_never_raises():
    ip = discovery.lan_ip()
    assert ip.count(".") == 3


# ---------------------------------------------------------------------------
# `castor discovery check` — the newcomer's one command
# ---------------------------------------------------------------------------


class FakeInfo:
    """Shaped like a zeroconf ServiceInfo, including the bytes-keyed TXT."""

    def __init__(self, props: dict, *, port: int = 8002, type_: str = SERVICE_TYPE):
        self.properties = {
            k.encode(): (v.encode() if isinstance(v, str) else v) for k, v in props.items()
        }
        self.port = port
        self.type = type_
        self.name = "x." + type_

    def parsed_addresses(self):
        return ["192.0.2.7"]


def test_decode_tolerates_bytes_keys_and_none_values():
    found = decode(FakeInfo({"rrn": "RRN-9", "name": "car", "manifest_path": None}))
    assert found.rrn == "RRN-9"
    assert found.name == "car"
    assert found.manifest_path == ""
    assert found.addresses == ["192.0.2.7"]


def test_a_record_with_no_rrn_is_reported_as_unusable_not_as_a_find():
    # This is the `_rcan._tcp` record's whole problem, and it must be VISIBLE:
    # the app silently skips such a record, so the check has to say why.
    found = decode(FakeInfo({"name": "car"}))
    assert found.usable is False
    text = format_found([found])
    assert "IGNORE" in text


def test_ONE_ROBOT_ONE_LINE_a_leftover_registration_is_not_a_second_robot():
    # A restart leaves the old registration on the LAN until it ages out, and
    # zeroconf publishes the new one as "<rrn>-2". Both records describe one
    # healthy robot at one address, and listing it twice reads like a
    # misconfiguration. Observed live on this bench.
    from castor.discovery import browse_key

    first = decode(FakeInfo({"rrn": "RRN-11"}))
    second = decode(FakeInfo({"rrn": "RRN-11"}))
    assert browse_key("RRN-11._opencastor._tcp.local.", first) == browse_key(
        "RRN-11-2._opencastor._tcp.local.", second
    )


def test_a_stale_record_at_a_DIFFERENT_address_stays_visible():
    # The one duplicate worth seeing: same identity, wrong address, which is
    # exactly the DHCP-move failure this module exists to fix.
    class Moved(FakeInfo):
        def parsed_addresses(self):
            return ["192.0.2.9"]

    here = decode(FakeInfo({"rrn": "RRN-11"}))
    there = decode(Moved({"rrn": "RRN-11"}))
    from castor.discovery import browse_key

    assert browse_key("a", here) != browse_key("b", there)


def test_a_record_with_no_identity_is_keyed_by_its_instance_name():
    from castor.discovery import browse_key

    anonymous = decode(FakeInfo({}))
    assert browse_key("weird._rcan._tcp.local.", anonymous) == "weird._rcan._tcp.local."


def test_the_check_prints_the_record_not_a_verdict():
    text = format_found([decode(FakeInfo(record().txt()))])
    for expected in ("RRN-LOCAL-abc123", "8000", "8001", "8002", "ROBOT.md", "192.0.2.7"):
        assert expected in text


def test_nothing_found_explains_the_three_reasons():
    text = format_found([])
    assert "discovery" in text  # names the unit to check
    assert "multicast" in text  # names the router failure
    assert "QR" in text  # and the path that always works


def test_check_exit_codes_are_the_answer(monkeypatch):
    monkeypatch.setattr(discovery, "browse", lambda timeout=3.0: [])
    assert discovery.check() == 1
    monkeypatch.setattr(discovery, "browse", lambda timeout=3.0: [decode(FakeInfo({"rrn": "R"}))])
    assert discovery.check() == 0
    monkeypatch.setattr(discovery, "browse", lambda timeout=3.0: [decode(FakeInfo({}))])
    assert discovery.check() == 2


# ---------------------------------------------------------------------------
# The one test that uses a real socket. Skipped where multicast cannot work.
# ---------------------------------------------------------------------------


def _multicast_available() -> bool:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)
        finally:
            s.close()
    except OSError:
        return False
    return discovery.lan_ip() != "127.0.0.1"


@pytest.mark.skipif(not _multicast_available(), reason="no multicast interface on this host")
def test_LOOPBACK_a_published_record_can_be_found_and_reads_back_whole():
    """Publish, browse, and get the same record back.

    The end-to-end claim of this whole module, and the only test that would
    have caught the original fault: the record has to be findable BY THE
    BROWSER, not merely constructible. `avahi-browse` is not the instrument —
    it reports nothing for records python-zeroconf resolves in a second.
    """
    pytest.importorskip("zeroconf")
    mine = record(rrn="RRN-TEST-loopback", name="loopback-testbot")
    advertiser = RobotAdvertiser(mine)
    if not advertiser.start():
        pytest.skip("could not register a service on this host")
    try:
        seen = {r.rrn: r for r in browse(timeout=4.0)}
    finally:
        advertiser.stop()
    if "RRN-TEST-loopback" not in seen:
        pytest.skip("multicast did not loop back on this host")
    got = seen["RRN-TEST-loopback"]
    assert got.usable
    assert got.name == "loopback-testbot"
    assert (got.gateway_port, got.castor_port, got.console_port) == (8000, 8001, 8002)
    assert got.manifest_path == "/home/pi/testbot/ROBOT.md"
