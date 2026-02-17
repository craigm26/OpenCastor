"""
OpenCastor Learn -- interactive step-by-step tutorial.

Walks new users through core concepts with hands-on exercises:
  1. Understanding RCAN configs
  2. Testing the AI brain
  3. Connecting hardware
  4. Running the loop
  5. Using messaging channels

Usage:
    castor learn
    castor learn --lesson 3
"""

import os
import sys
import time


def run_learn(lesson: int = None):
    """Run the interactive tutorial."""
    try:
        from rich.console import Console
        from rich.panel import Panel
        from rich.markdown import Markdown
        console = Console()
        has_rich = True
    except ImportError:
        has_rich = False
        console = None

    lessons = [
        _lesson_welcome,
        _lesson_rcan_config,
        _lesson_brain,
        _lesson_hardware,
        _lesson_loop,
        _lesson_channels,
        _lesson_next_steps,
    ]

    if lesson is not None:
        if 1 <= lesson <= len(lessons):
            lessons[lesson - 1](has_rich, console)
            return
        else:
            print(f"\n  Invalid lesson number. Choose 1-{len(lessons)}.\n")
            return

    # Run all lessons sequentially
    for i, lesson_fn in enumerate(lessons):
        lesson_fn(has_rich, console)
        if i < len(lessons) - 1:
            if not _continue_prompt(has_rich, console):
                print("\n  Tutorial paused. Resume with: castor learn --lesson {}\n".format(i + 2))
                return


def _continue_prompt(has_rich, console) -> bool:
    """Ask user to continue or quit."""
    try:
        answer = input("\n  Press Enter to continue (or 'q' to quit): ").strip().lower()
        return answer != "q"
    except (EOFError, KeyboardInterrupt):
        return False


def _print_lesson(title, content, has_rich, console):
    """Print a lesson with optional Rich formatting."""
    if has_rich:
        from rich.panel import Panel
        console.print(Panel(
            content,
            title=f"[bold]{title}[/]",
            border_style="cyan",
            padding=(1, 2),
        ))
    else:
        print(f"\n  === {title} ===\n")
        for line in content.split("\n"):
            print(f"  {line}")
        print()


def _lesson_welcome(has_rich, console):
    """Lesson 0: Welcome."""
    _print_lesson(
        "Welcome to OpenCastor",
        "OpenCastor connects AI brains to robot bodies.\n\n"
        "This tutorial will walk you through:\n"
        "  1. Understanding RCAN configs\n"
        "  2. Testing the AI brain\n"
        "  3. Connecting hardware\n"
        "  4. Running the perception-action loop\n"
        "  5. Adding messaging channels\n\n"
        "No hardware or API keys needed to start -- we'll use simulation mode.",
        has_rich, console,
    )


def _lesson_rcan_config(has_rich, console):
    """Lesson 1: RCAN configs."""
    example = (
        "An RCAN config (.rcan.yaml) describes your robot:\n\n"
        "  metadata:\n"
        "    robot_name: MyRobot        # Your robot's name\n"
        "    model: custom_rover         # Hardware model\n\n"
        "  agent:\n"
        "    provider: google            # AI brain (google/openai/anthropic)\n"
        "    model: gemini-2.0-flash     # Specific model\n\n"
        "  drivers:\n"
        "    - protocol: pca9685_rc      # Motor controller type\n"
        "      i2c_address: 0x40         # Hardware address\n\n"
        "Try it: castor wizard --simple --accept-risk\n"
        "  This creates a basic config in seconds."
    )
    _print_lesson("Lesson 1: RCAN Configuration", example, has_rich, console)


def _lesson_brain(has_rich, console):
    """Lesson 2: The AI brain."""
    content = (
        "The 'brain' is an LLM that sees through the camera and decides actions.\n\n"
        "Supported providers:\n"
        "  - Google Gemini  (fast, affordable, recommended)\n"
        "  - OpenAI GPT-4.1 (powerful vision)\n"
        "  - Anthropic Claude (strong reasoning)\n"
        "  - Ollama          (local, no API key needed)\n\n"
        "The brain receives:\n"
        "  1. A camera frame (JPEG bytes)\n"
        "  2. An instruction (text)\n\n"
        "And returns a Thought:\n"
        "  - raw_text: reasoning in natural language\n"
        "  - action: {type: 'move', linear: 0.3, angular: 0.0}\n\n"
        "Try it: castor demo\n"
        "  This runs a simulated loop with mock AI responses."
    )
    _print_lesson("Lesson 2: The AI Brain", content, has_rich, console)


def _lesson_hardware(has_rich, console):
    """Lesson 3: Hardware drivers."""
    content = (
        "Drivers translate AI actions into physical motor commands.\n\n"
        "Supported drivers:\n"
        "  - PCA9685 (I2C PWM) -- most Amazon robot kits\n"
        "  - Dynamixel         -- Robotis smart servos\n\n"
        "Hardware setup:\n"
        "  1. Connect motors to the controller board\n"
        "  2. Connect controller to Raspberry Pi via I2C or USB\n"
        "  3. Run: castor doctor --config robot.rcan.yaml\n"
        "  4. Test: castor test-hardware --config robot.rcan.yaml\n"
        "  5. Tune: castor calibrate --config robot.rcan.yaml\n\n"
        "No hardware? Use --simulate:\n"
        "  castor run --config robot.rcan.yaml --simulate"
    )
    _print_lesson("Lesson 3: Hardware Drivers", content, has_rich, console)


def _lesson_loop(has_rich, console):
    """Lesson 4: The perception-action loop."""
    content = (
        "The core loop runs continuously:\n\n"
        "  1. OBSERVE  -- Capture a camera frame\n"
        "  2. ORIENT   -- Send frame + instruction to the brain\n"
        "  3. DECIDE   -- Brain returns a Thought with an action\n"
        "  4. ACT      -- Driver executes the motor command\n"
        "  5. MEASURE  -- Check latency vs budget\n\n"
        "Key config values:\n"
        "  agent.latency_budget_ms: 3000   # Max loop time\n"
        "  physics.max_speed_ms: 0.5       # Safety speed limit\n"
        "  physics.safety_stop: true       # Emergency stop\n\n"
        "Run it:\n"
        "  castor run --config robot.rcan.yaml\n"
        "  castor run --config robot.rcan.yaml --simulate"
    )
    _print_lesson("Lesson 4: The Perception-Action Loop", content, has_rich, console)


def _lesson_channels(has_rich, console):
    """Lesson 5: Messaging channels."""
    content = (
        "Control your robot remotely via messaging platforms:\n\n"
        "  - WhatsApp (QR code scan -- no account needed)\n"
        "  - Telegram (bot token from @BotFather)\n"
        "  - Discord  (bot token from Discord Developer Portal)\n"
        "  - Slack    (bot token + app token from api.slack.com)\n\n"
        "Setup:\n"
        "  1. Add channel config to your .rcan.yaml\n"
        "  2. Set credentials in .env\n"
        "  3. Run: castor gateway --config robot.rcan.yaml\n\n"
        "The gateway starts the API server AND all configured channels.\n"
        "Send a message to your bot and the robot responds with what it sees."
    )
    _print_lesson("Lesson 5: Messaging Channels", content, has_rich, console)


def _lesson_next_steps(has_rich, console):
    """Final: Next steps."""
    content = (
        "You're ready to build with OpenCastor!\n\n"
        "Useful commands:\n"
        "  castor wizard          # Create a new config\n"
        "  castor doctor          # Check system health\n"
        "  castor demo            # Simulated demo\n"
        "  castor test-hardware   # Test motors\n"
        "  castor calibrate       # Tune servos\n"
        "  castor benchmark       # Profile performance\n"
        "  castor status          # Check provider/channel readiness\n\n"
        "Resources:\n"
        "  Docs:     https://opencastor.com\n"
        "  GitHub:   https://github.com/continuonai/OpenCastor\n"
        "  RCAN Spec: https://rcan.dev/spec/\n\n"
        "Happy building!"
    )
    _print_lesson("Next Steps", content, has_rich, console)
