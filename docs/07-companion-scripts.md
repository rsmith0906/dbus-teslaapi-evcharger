# 07 — Companion scripts

The repo ships three executable Python files. The main driver lives forever; the other two are command-line tools you call from the shell, a Node-RED flow, or a cron-like trigger.

## `change-tesla-charging-rate.py <amps>`

Single-shot script that calls `tesla-control charging-set-amps <amps>` and notifies via Pushbullet.

```bash
python change-tesla-charging-rate.py 8
```

Flow:

1. Read `amps` from `argv[1]`. Refuses to run without an argument.
2. Read `config.json` from the script's own dir (NOT `/data/tesla/`) — bug if you ran the user's full setup, both work because the file path resolves the same when the repo lives at `/data/dbus-teslaapi-evcharger/` and someone copied or symlinked `config.json` into the repo dir.
3. Set environment vars for `tesla-control`.
4. Use Pushbullet to send a "rate changed to N amps" notification (`pbApiKey` from `config.ini.DEFAULT.PushBulletKey` — note this key is referenced but **not present in the example `config.ini`**; the script will crash with `KeyError` until added).
5. Up to 2 attempts; on token error in stderr, refresh and retry.

Use cases:
- A Node-RED node fires this when surplus PV is in a band (e.g., > 1500 W → 6 A; > 2500 W → 8 A; etc.).
- A cron-like schedule throttles the rate to off-peak.

The main driver does **not** call this — the EVCS GUI's "Charge current" slider is connected to the no-op `_setcurrent`. Bridging the two would be a few lines (see [08-issues-and-improvements.md](08-issues-and-improvements.md)).

### Path mismatch with the main driver

The main driver hardcodes `script_dir = '/data/tesla'` for `config.json`. This script uses `os.path.dirname(os.path.abspath(__file__))`. So the main driver expects `config.json` at `/data/tesla/config.json`; this script expects it at `/data/dbus-teslaapi-evcharger/config.json`. Either:
- Symlink one into the other, or
- Edit the script to match.

## `change-tesla-charging-status.py <0|1>`

```bash
python change-tesla-charging-status.py 1   # start
python change-tesla-charging-status.py 0   # stop
```

Flow:

1. Read `status` from `argv[1]`.
2. Refresh token if expired.
3. If starting and on retry attempt, wake first (`tesla-control wake`).
4. Call `tesla-control charging-start` or `charging-stop`.
5. On token-or-sleep error, refresh and retry once.

Differences from the main driver's `_startstop`:
- Doesn't check current `charging_state` first (no idempotency).
- Always wakes only on **retry**, not on first attempt — so if the car's asleep, the first try fails, then it wakes, then the second try succeeds. The main driver wakes every time before the first attempt.
- No Pushbullet notification.

Use cases:
- A Node-RED button fires this directly.
- A scheduled task starts charging at midnight.

## `TokenRefresh/tesla-api-token-refresh.py`

Long-running daemon that just rotates the OAuth token every 4 hours. Detailed in [04-tesla-integration.md](04-tesla-integration.md) and [06-installation-and-config.md](06-installation-and-config.md).

A few things specific to this script worth noting:

- It defines its own `DBusGMainLoop` and `MainLoop` even though it doesn't talk to DBus at all. This is a copy-paste from the main driver — the GLib loop is needed only because the timer is a `gobject.timeout_add()`. Could be replaced with `time.sleep()` and a plain `while True`, but the daemontools shape is preserved by keeping it as is.
- It has a bug at line 62: `self._lastUpdate` is referenced in `_signOfLife` but is never initialized in `__init__`. The first `_signOfLife` call would `AttributeError` if the user actually configured `SignOfLifeLog` to a small enough value to fire — at the default 460 minutes it does fire, every ≈ 7.6 hours. Likely the user has just never seen the crash because daemontools restarts and the next interval may not fire before another error.
- It loads `config.json` from `/data/tesla/config.json` (matches the main driver), and `config.ini` from its own directory (`TokenRefresh/config.ini`).

## Why three places to start/stop charging?

| Surface | Triggered by | Calls into | Notes |
|---|---|---|---|
| `/StartStop` write on DBus | GX GUI button, VRM Portal toggle, Node-RED via MQTT | `dbus-teslaapi-evcharger.py` `_startstop` | Idempotent, blocks for 10 s |
| `change-tesla-charging-status.py 0\|1` | direct shell, Node-RED exec, cron | `tesla-control` directly | Not idempotent, no Pushbullet |
| `tesla-control charging-start\|stop` | direct shell on the Pi | Tesla Fleet API | Lowest level |

They all end up at the same Tesla endpoint. The `/StartStop` path is the only one that updates the DBus state immediately; the other two cause a state change but the EVCS tile only reflects it after the next `vehicle_data` poll (up to 5 minutes).
