"""
OpenCastor Shell -- interactive command REPL for robot control.

Type commands like ``move 0.3 0.0``, ``stop``, ``look``, ``say hello``
and see the robot respond in real time.

Usage:
    castor shell --config robot.rcan.yaml
"""

import cmd
import logging
import time

import yaml

logger = logging.getLogger("OpenCastor.Shell")


class CastorShell(cmd.Cmd):
    """Interactive robot command shell."""

    intro = (
        "\n  OpenCastor Shell -- type 'help' for commands, 'quit' to exit.\n"
        "  Connected to: {robot_name}\n"
    )
    prompt = "castor> "

    def __init__(self, config, brain=None, driver=None, camera=None, speaker=None):
        super().__init__()
        self.config = config
        self.brain = brain
        self.driver = driver
        self.camera = camera
        self.speaker = speaker
        self.intro = self.intro.format(
            robot_name=config.get("metadata", {}).get("robot_name", "Robot")
        )

    def do_move(self, arg):
        """Move the robot: move <linear> <angular>
        Example: move 0.3 0.0  (forward at 30% speed)
                 move 0.0 0.5  (turn right)"""
        parts = arg.split()
        try:
            linear = float(parts[0]) if len(parts) > 0 else 0.0
            angular = float(parts[1]) if len(parts) > 1 else 0.0
        except (ValueError, IndexError):
            print("  Usage: move <linear> <angular>  (e.g., move 0.3 0.0)")
            return

        if self.driver:
            self.driver.move(linear, angular)
            print(f"  Moving: linear={linear:.2f}, angular={angular:.2f}")
        else:
            print(f"  [MOCK] Move: linear={linear:.2f}, angular={angular:.2f}")

    def do_stop(self, arg):
        """Stop all motors immediately."""
        if self.driver:
            self.driver.stop()
        print("  Stopped.")

    def do_look(self, arg):
        """Capture a camera frame and ask the AI what it sees.
        Example: look
                 look what color is the object ahead?"""
        instruction = arg.strip() if arg.strip() else "Describe what you see in detail."

        if self.camera:
            frame = self.camera.capture_jpeg()
            print(f"  Captured frame: {len(frame):,} bytes")
        else:
            frame = b"\x00" * 1024
            print("  No camera -- using blank frame.")

        if self.brain:
            start = time.time()
            thought = self.brain.think(frame, instruction)
            latency = (time.time() - start) * 1000
            print(f"\n  AI says ({latency:.0f}ms):")
            print(f"  {thought.raw_text}\n")
            if thought.action:
                print(f"  Suggested action: {thought.action}")
        else:
            print("  No brain configured. Run with a valid API key.")

    def do_say(self, arg):
        """Speak text through the robot's speaker.
        Example: say hello world"""
        text = arg.strip()
        if not text:
            print("  Usage: say <text>")
            return

        if self.speaker and self.speaker.enabled:
            self.speaker.say(text)
            print(f"  Speaking: {text}")
        else:
            print(f"  [NO SPEAKER] Would say: {text}")

    def do_think(self, arg):
        """Send a text instruction to the AI brain (no camera).
        Example: think plan a route to the door"""
        instruction = arg.strip()
        if not instruction:
            print("  Usage: think <instruction>")
            return

        if self.brain:
            frame = b"\x00" * 1024
            start = time.time()
            thought = self.brain.think(frame, instruction)
            latency = (time.time() - start) * 1000
            print(f"\n  AI response ({latency:.0f}ms):")
            print(f"  {thought.raw_text}\n")
            if thought.action:
                print(f"  Action: {thought.action}")
        else:
            print("  No brain configured.")

    def do_status(self, arg):
        """Show current robot status."""
        print(f"\n  Robot:   {self.config.get('metadata', {}).get('robot_name', '?')}")
        print(f"  Brain:   {'online' if self.brain else 'offline'}")
        print(f"  Driver:  {'online' if self.driver else 'offline'}")
        print(f"  Camera:  {'online' if self.camera and self.camera.is_available() else 'offline'}")
        print(f"  Speaker: {'online' if self.speaker and self.speaker.enabled else 'offline'}")
        print()

    def do_config(self, arg):
        """Show the current RCAN config summary."""
        agent = self.config.get("agent", {})
        print(f"\n  Provider:  {agent.get('provider', '?')}")
        print(f"  Model:     {agent.get('model', '?')}")
        print(f"  Vision:    {agent.get('vision_enabled', False)}")
        print(f"  Latency:   {agent.get('latency_budget_ms', '?')}ms budget")
        drivers = self.config.get("drivers", [])
        if drivers:
            print(f"  Driver:    {drivers[0].get('protocol', '?')}")
        print()

    def do_quit(self, arg):
        """Exit the shell."""
        print("  Goodbye.")
        return True

    def do_exit(self, arg):
        """Exit the shell."""
        return self.do_quit(arg)

    do_EOF = do_quit

    def default(self, line):
        print(f"  Unknown command: {line}")
        print("  Type 'help' for available commands.")

    def emptyline(self):
        pass


def launch_shell(config_path: str):
    """Initialize hardware and start the interactive shell."""
    with open(config_path) as f:
        config = yaml.safe_load(f)

    robot_name = config.get("metadata", {}).get("robot_name", "Robot")
    print(f"\n  Initializing {robot_name}...")

    # Initialize brain
    brain = None
    try:
        from castor.providers import get_provider
        brain = get_provider(config["agent"])
        print("  Brain: online")
    except Exception as e:
        print(f"  Brain: offline ({e})")

    # Initialize driver
    driver = None
    try:
        from castor.main import get_driver
        driver = get_driver(config)
        if driver:
            print("  Driver: online")
        else:
            print("  Driver: offline (no hardware detected)")
    except Exception as e:
        print(f"  Driver: offline ({e})")

    # Initialize camera
    camera = None
    try:
        from castor.main import Camera
        camera = Camera(config)
        status = "online" if camera.is_available() else "offline"
        print(f"  Camera: {status}")
    except Exception as e:
        print(f"  Camera: offline ({e})")

    # Initialize speaker
    speaker = None
    try:
        from castor.main import Speaker
        speaker = Speaker(config)
        status = "online" if speaker.enabled else "offline"
        print(f"  Speaker: {status}")
    except Exception as e:
        print(f"  Speaker: offline ({e})")

    shell = CastorShell(config, brain=brain, driver=driver, camera=camera, speaker=speaker)

    try:
        shell.cmdloop()
    except KeyboardInterrupt:
        print("\n  Interrupted.")
    finally:
        if driver:
            driver.stop()
            driver.close()
        if camera:
            camera.close()
        if speaker:
            speaker.close()
