"""
SO-ARM101 CLI commands.

Registered as subcommands under `castor arm`:

    castor arm assemble  [--arm follower|leader|both]
    castor arm detect    (find USB ports)
    castor arm setup     [--arm follower|leader|bimanual] [--dry-run]
    castor arm verify    [--port /dev/ttyACM0] [--arm follower]
    castor arm config    [--name NAME] [--out PATH]
"""

from __future__ import annotations

import argparse
import os
import sys


def cmd_assemble(args) -> None:
    from castor.hardware.so_arm101.assembly_guide import run_assembly_guide

    arms = ["follower", "leader"] if args.arm == "both" else [args.arm]
    for arm in arms:
        run_assembly_guide(arm=arm)


def cmd_detect(args) -> None:
    from castor.hardware.so_arm101.port_finder import detect_feetech_ports, list_serial_ports

    print("\n[SO-ARM101] Scanning for controller boards...\n")
    feetech = detect_feetech_ports()
    if feetech:
        for p in feetech:
            print(f"  ✓ {p['port']}  —  {p['description']}")
    else:
        all_ports = list_serial_ports()
        if all_ports:
            print("  No Feetech VID:PID matched. Available serial ports:")
            for p in all_ports:
                print(f"    {p}")
        else:
            print("  No serial ports found. Is the board plugged in and powered?")
    print()


def cmd_setup(args) -> None:
    from castor.hardware.so_arm101.motor_setup import setup_motors
    from castor.hardware.so_arm101.port_finder import auto_assign_ports, chmod_ports

    arms_to_setup = []

    if args.port:
        # Explicit port provided
        arms_to_setup.append((args.arm, args.port))
    else:
        # Auto-detect
        ports = auto_assign_ports()
        if not ports:
            print("  No controller boards found. Use --port to specify manually.")
            sys.exit(1)
        if args.arm == "bimanual":
            for arm_name, port in ports.items():
                arms_to_setup.append((arm_name, port))
        else:
            port = ports.get(args.arm)
            if not port:
                print(f"  Port for '{args.arm}' arm not found. Detected: {ports}")
                sys.exit(1)
            arms_to_setup.append((args.arm, port))

    # Grant port access on Linux
    chmod_ports({arm: port for arm, port in arms_to_setup})

    results = {}
    for arm, port in arms_to_setup:
        print(f"\nConfiguring {arm} arm on {port}...")
        result = setup_motors(port=port, arm=arm, dry_run=args.dry_run)
        results[arm] = result

    # Summary
    all_ok = all(v for arm_result in results.values() for v in arm_result.values())
    if all_ok:
        print("✅ All motors configured successfully.\n")
        print("   Next: daisy-chain all motors, then run 'castor arm verify'")
        print("   Then: run 'castor arm config' to generate your RCAN config\n")
    else:
        print("⚠  Some motors failed. Check cables and try again.\n")
        for arm, result in results.items():
            failed = [j for j, ok in result.items() if not ok]
            if failed:
                print(f"  {arm}: failed joints: {', '.join(failed)}")


def cmd_verify(args) -> None:
    from castor.hardware.so_arm101.motor_setup import verify_motors
    from castor.hardware.so_arm101.port_finder import detect_feetech_ports

    port = args.port
    if not port:
        ports = detect_feetech_ports()
        if ports:
            port = ports[0]["port"]
        else:
            print("No port specified and no Feetech boards found.")
            sys.exit(1)

    print(f"\n[SO-ARM101] Verifying {args.arm} arm on {port}...")
    results = verify_motors(port=port, arm=args.arm)
    all_ok = all(results.values())
    for joint, ok in results.items():
        icon = "✓" if ok else "✗"
        print(f"  {icon}  {joint}")
    if all_ok:
        print("\n✅ All motors responding.\n")
    else:
        missing = [j for j, ok in results.items() if not ok]
        print(f"\n⚠  {len(missing)} motor(s) not responding: {', '.join(missing)}")
        print("   Check daisy-chain cable order and power supply.\n")


def cmd_calibrate(args) -> None:
    """castor arm calibrate — run lerobot-calibrate for joint zero-offsets."""
    from castor.hardware.so_arm101.lerobot_bridge import (
        lerobot_available,
        run_calibrate,
    )
    from castor.hardware.so_arm101.lerobot_bridge import (
        status as lr_status,
    )

    if not lerobot_available():
        print(
            "\n⚠  lerobot-calibrate not found.\n"
            "   Install LeRobot first:\n"
            "     git clone https://github.com/huggingface/lerobot.git\n"
            "     cd lerobot && python3 -m venv .venv && source .venv/bin/activate\n"
            "     pip install -e '.[feetech]'\n"
        )
        return

    st = lr_status()
    print(f"\n[SO-ARM101] LeRobot venv: {st['venv']}")

    port = args.port
    if not port:
        from castor.hardware.so_arm101.port_finder import detect_feetech_ports

        ports = detect_feetech_ports()
        port = ports[0]["port"] if ports else "/dev/ttyACM0"

    ok = run_calibrate(port=port, arm=args.arm)
    if ok:
        print(f"\n✅ Calibration complete for {args.arm} arm.\n")
    else:
        print("\n⚠  Calibration exited with an error. Check cables and retry.\n")


def cmd_status(args) -> None:
    """castor arm status — show LeRobot install status and detected ports."""
    from castor.hardware.so_arm101.lerobot_bridge import status as lr_status
    from castor.hardware.so_arm101.port_finder import detect_feetech_ports

    st = lr_status()
    print("\n[SO-ARM101] LeRobot status:")
    print(f"  Available : {'✅ yes' if st['available'] else '❌ no'}")
    print(f"  Venv      : {st['venv'] or 'not found'}")
    for tool, path in st["tools"].items():
        icon = "✓" if path else "✗"
        print(f"  {icon}  {tool}: {path or 'not found'}")

    ports = detect_feetech_ports()
    print("\n[SO-ARM101] Detected controller boards:")
    if ports:
        for p in ports:
            print(f"  ✓  {p['port']}  — {p['description']}")
    else:
        print("  None detected (boards may not be plugged in)")
    print()


def cmd_record(args) -> int:
    """Record demonstration episodes via LeRobot teleoperation.

    Delegates to ``lerobot-record`` with SO-ARM101 follower/leader config.
    Run ``castor arm record --help`` for options.
    """
    from castor.hardware.so_arm101.lerobot_bridge import LeRobotBridge

    bridge = LeRobotBridge()
    if not bridge.available:
        print("LeRobot not available. Install with: pip install 'lerobot[feetech]'")
        return 1

    cmd = [
        "lerobot-record",
        "--robot.type=so101_follower",
        f"--robot.port={args.port or '/dev/ttyACM0'}",
        "--teleop.type=so101_leader",
        f"--teleop.port={args.leader_port or '/dev/ttyACM1'}",
        f"--dataset.repo_id={args.dataset or 'local/so101_demo'}",
        f"--dataset.num_episodes={args.episodes or 10}",
    ]
    if args.push:
        cmd.append("--dataset.push_to_hub=true")

    import subprocess

    result = subprocess.run(bridge._prefix_cmd(cmd))
    return result.returncode


def cmd_grasp(args) -> int:
    """Hailo grasp-planning hook for SO-ARM101.

    This is an integration stub.  When ``castor/hailo_vision.py`` is present
    (installed via ``opencastor[hailo]``), it calls
    ``hailo_vision.detect_grasp_target()`` to get a grasp pose from the
    Hailo NPU and passes it to the arm.

    To add your own grasp logic, implement or replace
    ``castor/hailo_vision.py::detect_grasp_target()``.
    """
    # Locate hailo_vision relative to the castor package root
    import importlib
    import importlib.util

    hailo_spec = importlib.util.find_spec("castor.hailo_vision")

    # Also accept a local file next to this package (development installs)
    _local_path = os.path.join(os.path.dirname(__file__), "..", "..", "hailo_vision.py")
    _local_path = os.path.normpath(_local_path)

    if hailo_spec is None and not os.path.exists(_local_path):
        print(
            "Hailo not available. Ensure opencastor[hailo] is installed and Hailo NPU is connected."
        )
        return 1

    # Import whichever path resolved
    if hailo_spec is not None:
        hailo_vision = importlib.import_module("castor.hailo_vision")
    else:
        spec = importlib.util.spec_from_file_location("castor.hailo_vision", _local_path)
        hailo_vision = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
        spec.loader.exec_module(hailo_vision)  # type: ignore[union-attr]

    # ── Camera ROI (optional) ──────────────────────────────────────────────
    import logging as _logging

    _arm_logger = _logging.getLogger("OpenCastor.Hardware.SOArm101.CLI")
    try:
        from castor.hardware.so_arm101.vision import get_camera_frame_roi

        roi = get_camera_frame_roi()
        if roi:
            _arm_logger.info("Camera ROI: %s", roi)
            print(
                f"[grasp] Camera ROI: {roi['width']}x{roi['height']}"
                f" center=({roi['center_x']}, {roi['center_y']})"
            )
        else:
            _arm_logger.debug("Camera not available — proceeding without ROI")
    except Exception:
        pass  # camera is entirely optional

    # ── Grasp detection ────────────────────────────────────────────────────
    target = hailo_vision.detect_grasp_target()
    if target is None:
        print("No grasp target detected.")
        return 1

    print(f"Grasp target detected: {target}")

    # ── Send target pose via RCAN ──────────────────────────────────────────
    try:
        from castor.hardware.so_arm101.rcan_bridge import send_arm_pose_rcan

        joint_positions = target if isinstance(target, dict) else {}
        send_arm_pose_rcan(joint_positions)
    except Exception:
        pass  # RCAN is optional

    return 0


def cmd_config(args) -> None:
    from castor.hardware.so_arm101.config_generator import write_config
    from castor.hardware.so_arm101.port_finder import auto_assign_ports

    # Detect ports
    ports = auto_assign_ports() if not (args.follower_port or args.leader_port) else {}
    follower_port = args.follower_port or ports.get("follower", "/dev/ttyACM0")
    leader_port = args.leader_port or ports.get("leader")

    out_path = args.out or os.path.expanduser(f"~/.opencastor/{args.name}.rcan.yaml")

    path = write_config(
        path=out_path,
        robot_name=args.name,
        follower_port=follower_port,
        leader_port=leader_port,
    )
    print(f"\n✅ Config written to: {path}")
    print(f"   Start with: castor run --config {path}\n")


def build_parser(subparsers=None) -> argparse.ArgumentParser:
    """Build the 'castor arm' subcommand parser."""
    if subparsers is None:
        parser = argparse.ArgumentParser(prog="castor arm", description="SO-ARM101 setup tools")
        subparsers = parser.add_subparsers(dest="arm_cmd")
    else:
        parser = subparsers.add_parser(
            "arm", help="SO-ARM101 assembly, port detection, motor setup"
        )
        subparsers = parser.add_subparsers(dest="arm_cmd")

    # assemble
    p_asm = subparsers.add_parser("assemble", help="Guided physical assembly walkthrough")
    p_asm.add_argument("--arm", choices=["follower", "leader", "both"], default="follower")
    p_asm.set_defaults(func=cmd_assemble)

    # detect
    p_det = subparsers.add_parser("detect", help="Find USB ports for controller boards")
    p_det.set_defaults(func=cmd_detect)

    # setup
    p_setup = subparsers.add_parser("setup", help="Configure motor IDs and baudrates")
    p_setup.add_argument("--arm", choices=["follower", "leader", "bimanual"], default="follower")
    p_setup.add_argument("--port", help="Serial port (auto-detected if not specified)")
    p_setup.add_argument(
        "--dry-run", action="store_true", help="Simulate without writing to motors"
    )
    p_setup.set_defaults(func=cmd_setup)

    # verify
    p_ver = subparsers.add_parser("verify", help="Ping all motors in daisy chain")
    p_ver.add_argument("--arm", choices=["follower", "leader"], default="follower")
    p_ver.add_argument("--port", help="Serial port")
    p_ver.set_defaults(func=cmd_verify)

    # calibrate
    p_cal = subparsers.add_parser("calibrate", help="Run lerobot-calibrate for joint zero-offsets")
    p_cal.add_argument("--arm", choices=["follower", "leader"], default="follower")
    p_cal.add_argument("--port", help="Serial port")
    p_cal.set_defaults(func=cmd_calibrate)

    # status
    p_st = subparsers.add_parser("status", help="Show LeRobot install status and detected ports")
    p_st.set_defaults(func=cmd_status)

    # record
    p_rec = subparsers.add_parser("record", help="Record teleoperation episodes via LeRobot")
    p_rec.add_argument("--port", help="Follower port (default: /dev/ttyACM0)")
    p_rec.add_argument("--leader-port", help="Leader port (default: /dev/ttyACM1)")
    p_rec.add_argument("--dataset", help="Dataset repo ID (default: local/so101_demo)")
    p_rec.add_argument("--episodes", type=int, default=10, help="Number of episodes")
    p_rec.add_argument("--push", action="store_true", help="Push to HuggingFace Hub")
    p_rec.set_defaults(func=cmd_record)

    # grasp
    p_grasp = subparsers.add_parser("grasp", help="Hailo grasp-planning hook (stub)")
    p_grasp.set_defaults(func=cmd_grasp)

    # config
    p_cfg = subparsers.add_parser("config", help="Generate RCAN config file")
    p_cfg.add_argument("--name", default="so_arm101", help="Robot name")
    p_cfg.add_argument("--out", help="Output path (default: ~/.opencastor/<name>.rcan.yaml)")
    p_cfg.add_argument("--follower-port", help="Follower arm serial port")
    p_cfg.add_argument("--leader-port", help="Leader arm serial port (bimanual)")
    p_cfg.set_defaults(func=cmd_config)

    return parser


def main(argv=None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not hasattr(args, "func") or args.func is None:
        parser.print_help()
        return
    args.func(args)


if __name__ == "__main__":
    main()
