# Skill gaps — how a missing piece becomes a community skill

On a new machine, anything "missing" is not a setup failure. It is a **skill
the OpenCastor community should know about**: a chip with no driver, a chassis
with no actuator package, a sensor nothing declares. This document is the rail
that turns one into the other — and the consent model that keeps an AI from
walking it alone.

## What a gap is

`castor up` (and `castor gaps`) writes `<home>/gaps.json`: structured entries,
each with observed **evidence**, one closing **suggestion**, and a
**skill_hint** for whoever drafts the missing piece. Three kinds today:

| kind | example | closed by |
|---|---|---|
| `missing-package` | PCA9685 present, `rc-car-actuator` not installed | `pip install`, rerun `castor up` |
| `unclaimed-peripheral` | LiDAR on `/dev/ttyUSB0`, nothing declares `lidar.*` | a new driver + capability block |
| `no-brain` | no Ollama, no Claude sign-in | pull a model or sign in |

Gaps are recomputed whole on every run: closing one makes it disappear, which
is the feedback loop working.

## The rail

```
detect                the robot notices, writes gaps.json
  → surface           the app / CLI shows the gap to the OPERATOR
    → allow           the operator explicitly permits a draft ("build this skill")
      → draft         an AI (the robot's own brain, or Claude on the host)
                      writes the driver/actuator + the ROBOT.md capability block
        → review      the operator reads the draft — it is code that will
                      touch hardware they own
          → sign      the operator re-signs ROBOT.md; the gateway reloads
            → share   (optional) the skill is published for the next person
                      with the same hardware
```

## The authority rule

**A gap never closes itself.** Every arrow after *allow* is operator-gated:

- The AI **drafts**; it does not install. A draft lives in a working directory
  until a human moves it.
- **ROBOT.md is signed** precisely so nothing changes it silently. Re-signing
  is the operator's act (the manifest key stays under their control), and the
  gateway trusts only what verifies.
- New tools still face the **gateway's allowlist and tier policy** — a freshly
  drafted capability starts un-allowlisted, so even a signed manifest cannot
  move hardware until the operator widens policy too.
- The phone app deliberately **cannot** add capabilities (see the peripherals
  screen's own note): a phone that could grant a robot abilities because it
  saw a chip on a bus would be inventing authority out of an I2C address.

An AI that could close gaps on its own initiative would defeat the entire
signed-manifest design. The value of the rail is that the easy path and the
safe path are the same path.

## Sharing back (the community half)

The `skill_hint` field carries what a matching engine needs: entry-point
groups, suggested config blocks, driver protocols. The intended future is a
registry keyed on hardware evidence (USB IDs, I2C addresses) so that the
second person to plug in the same lidar gets "a community skill exists for
this" instead of a gap. Publishing a skill is itself operator-gated — code
leaves the house only when its author says so.

Until that registry exists, drivers land as packages with entry points
(`robot_md_gateway.actuators`, `rc_car_actuator.drives`) — both groups already
resolve plugins today, so a community skill is installable the moment it is
`pip install`-able.
