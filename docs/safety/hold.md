# The hold: `castor pause`, `castor resume`, and the e-stop latch

This page describes one thing: what happens on a robot when somebody decides it
must not move, and how that decision is undone.

## What the hold is

A **best-effort software hold**. It is not a hardware cut.

When a hold is in force, this robot's own software refuses to issue motion and
asks whatever owns the actuator to stop. That is all it does. It does not
de-energise a motor, it does not open a contactor, it does not cut power, and
nothing about it is safety rated. It has two ordinary ways to fail:

- If the process is killed with SIGKILL between the decision and the write to
  disk, the decision is lost.
- On a robot whose actuator lives behind the gateway, the stop crosses a
  network hop. A stop that cannot reach the gateway does not become a stop by
  being logged. The runtime retries and then reports `stop_not_confirmed`
  rather than a comfortable success.

If your robot needs a stop that is not subject to either of those, you need
hardware that cuts power, and this is not it.

## The latch file

The hold lives in a file, not in a process: `$ROBOT_HOME/safety-latch.json`,
mode 0600, written atomically. `castor up` already exports `ROBOT_HOME` into
every generated unit, so there is nothing to configure.

It exists because a hold that lived only in memory was not a hold. A robot that
e-stopped because its board hit 90 C and was restarted by systemd twelve
seconds later used to come back with the flag cleared. Nobody cleared it. The
restart did. The file is read at construction, so a restart comes back held.

The file carries two separate flags:

- **estop**, with the SOURCE that set it: `sensor`, `api`, `rcan`, `local` or
  `swarm`.
- **pause**, with WHO paused, WHEN, and WHY.

Both block motor writes. They are different things and are lifted differently.

With `ROBOT_HOME` unset there is no file and no persistence at all. That is
deliberate: importing `castor.fs` in a test or a notebook must not start
writing state into whatever directory happened to be current.

Deleting the file does not lift a hold. A clear always leaves a file behind
saying `engaged: false`, so the absence of a file is never evidence that
anybody cleared anything. A running server that finds the file gone keeps
holding and says so in its log.

## `castor pause` and `castor resume`

```bash
castor pause --reason "battery swap"
castor resume
```

`castor pause` is deliberately not an e-stop. Nothing is on fire; somebody
wants the robot to stand still for a while. `--reason` is required, and it is
load-bearing: a sticky pause nobody can account for looks exactly like broken
hardware to the next person who walks up. `castor resume` prints who paused,
when, and why, before it lifts anything, so that person learns what the last
one meant without reading a log.

Both commands write the latch file and then rely on the running server to
notice. It notices within about five seconds: the generated runtime's guard
loop and the stock `castor gateway` app both reconcile their in-memory hold
against the file on that cadence, and both write a `pause`, `resume`, `estop`
or `clear_estop` row to `/var/log/safety` when it changes. Both reconcile on
their own event loop rather than in a worker thread, so a stop arriving
mid-reconcile cannot be mistaken for somebody else's clear.
`GET /api/fs/estop` reports `held`, `paused`, `estopped`, `hold_detail` and the
raw `latch`, so you can see this process' state next to the file it came from.

## The e-stop clear code

Lifting an e-stop needs a second factor, separate from the bearer token: the
value of `OPENCASTOR_ESTOP_AUTH`.

`castor up` provisions it. `castor.up.ensure_estop_auth()` mints one on first
run, writes it into `$ROBOT_HOME/tokens.env` (which the generated unit loads,
so the runtime has it), and prints it once. It is a different secret from the
admin bearer and is held in a different place on purpose: the bearer token
authorises talking to the robot, and the code authorises undoing a stop.

Two ways to send it:

- `POST /api/estop/clear` with the code in the `X-Estop-Auth` header (or
  `auth_code` in a JSON body), plus an admin bearer. The bundled `/gamepad`
  page has a field for it, held in the page for as long as it is open and
  nowhere else, so a reload asks again. Setting a stop stays open to any
  authenticated caller; clearing one does not.
- `castor resume --clear-estop --auth-code <code>` on the robot's own host.

A robot with no code provisioned cannot check one, so `--clear-estop` refuses
rather than clearing unchecked, exits 3, and tells you to run `castor up`. A
wrong `--auth-code`, or none on a robot that has a code, refuses and exits 3
the same way. If you must clear on a robot that has no code at all,
`--no-auth-code` does it and prints a warning saying the clear was
unauthenticated. A missing secret is not consent. On a robot that does have a
code, `--no-auth-code` is refused with exit 3: it is an escape hatch for
nothing to check against, not a way around a code that exists.

Every clear taken at the robot appends `estop_cleared` to
`$ROBOT_HOME/audit.log`, carrying who took it and whether anything checked
them, so an unauthenticated one can be found afterwards rather than only
having been printed once to a terminal.

WHERE THE TWO PATHS DIFFER, which is worth knowing before relying on either.
The CLI looks for the code in `OPENCASTOR_ESTOP_AUTH` and then in
`$ROBOT_HOME/tokens.env`. `POST /api/estop/clear` checks only the environment
variable of the server process. A unit written by `castor up` loads
`tokens.env`, so on a robot built the ten-minute way the two agree. A gateway
started by hand in a shell that does not export the variable has nothing to
check against, and there an admin bearer alone clears the stop. Start the
gateway with the variable set, or let `castor up` write the unit.

## Why a sensor latch clears only at the robot

Three consecutive critical thermal, load or force readings latch the e-stop with
`source=sensor`. That latch is refused by every clear that arrives over the
network, including `POST /api/estop/clear` with a valid code, and including a
remote RCAN RESUME. The refusal is recorded as a `deny_clear_estop` audit row
naming the source.

The reason is simple: a sensor latch means a reading crossed a critical
threshold three times running, and whoever is clearing it from somewhere else
cannot see the robot. Someone has to look at it. So the only clear that lifts a
sensor latch is `castor resume --clear-estop` run on the robot's own host, which
is the one clear that implies a person standing there.

## `POST /api/runtime/resume` is a different thing

`POST /api/runtime/pause` and `POST /api/runtime/resume` pause and resume the
**perception-action loop**. They set `state.paused` in the gateway process, they
do not write the latch file, they do not survive a restart, and
`/api/runtime/resume` never lifts an e-stop or a `castor pause`. The e-stop
latch is cleared only by `POST /api/estop/clear` or
`castor resume --clear-estop`.

## Where the code is

| Thing | File |
|---|---|
| The latch file, its format and its rules | `castor/safety/latch.py` |
| The in-process hold, `resync_from_latch`, `clear_estop` | `castor/fs/safety.py` |
| The stock gateway's reconcile task, `/api/estop/clear`, `/api/fs/estop` | `castor/api.py` |
| `castor pause` / `castor resume` | `castor/cli.py` |
| `ensure_estop_auth` | `castor/up.py` |
| The generated runtime's always-on guard loop | `castor/templates/*/runtime.py.tmpl` |
| Tests | `tests/test_shipped_runtime_stop.py` |
