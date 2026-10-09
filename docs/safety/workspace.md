# The base workspace: `safety.workspace`

This page describes one thing: how `castor run` keeps a mobile base inside a declared area, and
what it does when it cannot tell where the base is.

## What it is

A **software check on base motion**, off unless configured. It is not safety rated and it does not
cut power. It needs the base's pose from **independent localization**: an overhead camera, motion
capture, or a localizer on the robot's own sensors. Never a position estimated from the commands
the robot was sent; the geofence does that, and in the EV-03 hostile-model test it believed the
rover was 0.73 m from its start when it was 11.47 m away.

## Configuration

```yaml
safety:
  workspace:
    keep_in: [[0, 0], [6, 0], [6, 4], [0, 4]]     # metres, in the pose source's frame
    keep_out: [[[2, 1], [3, 1], [3, 2], [2, 2]]]   # optional
    top_speed_mps: 1.5        # required: m/s at linear = 1.0
    max_decel_mps2: 1.0       # required: the braking the base can ALWAYS achieve
    reaction_s: 0.05          # optional, default 0.05
    margin_m: 0.05            # optional, default 0.05
    enforce_hz: 50            # optional, default 50
    pose_source: overhead_cam # optional, see below
```

- **Absent block:** no workspace policy, and nothing below runs.
- **Invalid block:** `castor run` refuses to boot and names every problem. Unknown keys are errors,
  because a misspelt `top_speed_mps` quietly replaced by a default would understate the stopping
  distance.
- The polygons are for the base's reference point (the point the pose source reports). Shrink
  `keep_in` and grow the keep-outs by the base's footprint radius.

## What it does

1. **On every motor write**, the safety layer predicts the worst-case stopping path: the commanded
   (or current, if larger) speed for `reaction_s`, then braking at `max_decel_mps2`, along the
   heading the command drives, plus `margin_m`. A move whose path leaves `keep_in` or enters a
   keep-out is refused, and `/dev/motor` gets a zero-translation command with the same turn rate.
   The previous command is never left running.
2. **Every control cycle** (`enforce_hz`), a thread re-checks the command standing on `/dev/motor`
   from the current pose. `castor run` writes once per brain step, and with a slow brain a
   full-speed command that was safe when it was accepted stops being safe long before the next
   step. When the standing command has to go, `/dev/motor` gets the zero-translation command,
   `/var/log/safety` a `workspace_enforced` row, and the motors a `driver.stop()`.
3. **The re-check only ever stops the motors.** It never hands the driver a turn, because the
   watchdog, a bounds stop or an e-stop may already have stopped them.
4. **The wheels run what `/dev/motor` holds, or nothing.** The brain step writes, reads back and
   calls the driver under `SafetyLayer.motor_lock`, so a re-check cannot land in between. With a
   workspace configured, an action the wheels do not take (`wait`, `grip`, no `type`) stops them.

## No pose: every translating move is refused

OpenCastor ships no localizer. Whatever provides the pose registers it:

```python
from castor.safety.workspace import register_pose_source, unregister_pose_source

register_pose_source("overhead_cam", tracker.pose)  # -> (x, y, heading_rad, speed_mps) or None
...
unregister_pose_source("overhead_cam")              # when the tracker stops
```

The policy looks the name up on every check. With no `pose_source` configured, nothing registered
under it yet, or a provider that returns None (it must, when its fix is stale), raises, or returns
something that cannot be read as four finite numbers, **every translating move is refused**. Stops
and turning in place still work. `castor run` warns at boot when no `pose_source` is configured.

## Timing

A command that passes one re-check runs until the next one, so `reaction_s` must cover one
enforcement period plus the latency from the check to the motors. The config refuses a `reaction_s`
shorter than `1 / enforce_hz`; the latency is yours to add. Add the localizer's error to
`margin_m`. The stopping path is a straight line along the heading: a base that keeps turning while
it brakes is not modelled.

## Where it runs

`castor run` (`castor/main.py`) only. The `castor up` runtime serves `castor.api`, which does not
read `safety.workspace` yet.

## Testing

`tests/test_workspace_enforcer.py` and `tests/test_workspace_config.py`. The EV-03 harness drives
the same path with a hostile command generator and a 2 Hz brain on a 50 Hz control loop
(`opencastor-fixed-mainloop`, with `opencastor-fixed-mainloop-noenforce` and
`opencastor-fixed-slowbrain` as the cases without the per-cycle re-check).
