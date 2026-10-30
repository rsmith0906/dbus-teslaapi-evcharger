# 11 — Plug-in detection and the Node-RED auto-start flow

This doc records why solar auto-start failed on 2026-10-28, what was changed on the Victron Pi to fix it, what is still broken, and the plan for detecting plug-in quickly over Bluetooth without paying more for the Tesla API. Read [04-tesla-integration.md](04-tesla-integration.md) and [10-deploy-to-pi.md](10-deploy-to-pi.md) first if you are new to the project.

Everything under "Changes made" lives **only on the Pi**. The Node-RED flow (`/data/home/nodered/.node-red/flows.json`) and the helper scripts in `/data/tesla/` are not in this repo.

## The auto-start flow, in one paragraph

A Node-RED tab called **Tesla Auto-Start Charging** on the Victron Pi starts charging the car at 5 A when the house has spare energy: battery above 50%, or battery at least 20% **and** solar above 1600 W. It combines three Victron readings (battery SOC from the SmartShunt `com.victronenergy.battery/289`, the EV charger `/Status` from this driver's `com.victronenergy.evcharger/41`, and MPPT solar `/Dc/Pv/Power`). It checks at most every 5 minutes. Since 2026-10-29 it starts **as often as needed while the car stays plugged in**, because solar goes up and down during the day. It waits at least 30 minutes between attempts, and longer after a failed start (see "Current start rules" below). The pause is stored in `/data/tesla/auto-start-state.json` before the command is sent. The start itself runs `python /data/tesla/node-red-tesla.py start`.

## Current start rules (since 2026-10-29)

Each check, the start rules node (`da4587e06d759e91`) reads the Victron EV charger `/Status` and the saved pause:

| Victron status | What the flow does |
|---|---|
| `0` Disconnected | Clears any pause (writes `{}`), so a new plug-in can start right away. No start. |
| `1` Connected | If a pause is active, it waits. Otherwise, if battery > 50%, or battery ≥ 20% **and** solar > 1600 W, it saves a 30-minute pause and then starts. |
| `2` Charging, `3` Charged | Nothing. |
| anything else (e.g. `10`, set by the driver on errors, `dbus-teslaapi-evcharger.py:533`) | Waits. No start. |

If the start command fails, **Rearm/pause after failed start** (`5e1a7c0d2b9f4a01`) replaces the pause:
- The car said `could not execute command: disconnected` (Victron status was stale) → try again in **30 min**.
- The car was still `offline or asleep` after the helper's 2-minute wake wait → **30 min** ("Car did not wake").
- Any other error (bad token, usage cap, possibly a full car) → wait **3 h**. A constant error then can't wake the car every 30 minutes.

The saved file looks like `{"attempted_at": <unix time>, "retry_after": <unix time>, "reason": "Started recently" | "Car said unplugged" | "Car did not wake" | "Start failed"}`. The rule node's status text shows the reason and the next try time.

Things to know:
- **Stopping by hand doesn't stick in sunshine.** If you stop charging yourself while the rule passes, it will restart within about 30 minutes.
- **A full car shows as Connected.** The driver maps `Complete` with the latch engaged to status 1 (`dbus-teslaapi-evcharger.py:177`). So the flow may try to start a full car. The 3-hour failure pause limits that cost. The error Tesla returns for a full car hasn't been seen yet.
- **No automatic stop when solar drops.** Only the separate "Low battery with Car Charging" flow stops charging, when the house battery reaches 5–10%.

Thresholds and pause lengths are constants at the top of the two function nodes.

## The problem (2026-10-28)

The car was plugged in, the battery was 43%, and solar was 2021 W, so the rules should have passed. Nothing happened. The rule node said **"Waiting for fresh Tesla cache"**.

The chain of events:
1. The car was asleep. Every 5 minutes the driver called Fleet API `vehicle_data`, and since 09:29 UTC every call had failed with **408 Request Timeout**, which is Tesla's reply for a sleeping car.
2. The driver treats a 408 as "car sleeping". It keeps the last known values and logs "Car Sleeping" once (`dbus-teslaapi-evcharger.py:523-529`).
3. The driver writes its cache file `/tmp/1492677889280637.json` only after a successful response (`dbus-teslaapi-evcharger.py:282`). The file was last written at 00:49 UTC, **15.5 hours** earlier.
4. The old start rule read that cache through `node-red-tesla.py read` and refused to act unless it was under 15 minutes old.
5. The driver never wakes the car just to read data. Only a start/stop command wakes it (`dbus-teslaapi-evcharger.py:357`). The rule wouldn't send a command without fresh data, so once the car slept, the rule **waited forever**.

## Changes made on the Victron Pi (2026-10-28)

### 1. Decide from Victron status instead of the Tesla cache
- `/data/tesla/node-red-tesla.py`:
  - `read` now returns only the saved session, `{"session": ...}`. It no longer opens the Tesla cache.
  - `start` now runs `wake` before setting 5 A and `charging-start`. On 2026-10-29 it was changed again to wait for the car to wake (see section 5). Before this, the start path went straight to `charging-set-amps` (`/data/tesla/change-tesla-charging-rate.py:104-107`) and never woke the car.
- Start rules node (`da4587e06d759e91`) uses the Victron EV charger `/Status`:
  - `0` Disconnected → rearm (`handled: false`).
  - `2` Charging or `3` Charged → `handled: true`.
  - `1` Connected → check battery/solar, save `handled: true`, then start.
  - Anything else (for example `10`, which the driver sets on unexpected errors at `dbus-teslaapi-evcharger.py:533`) → wait, no start.
- Nodes renamed: "Read saved session (no API)", "Parse saved session", "Wake, set 5 A, then start".

### 2. The false "Connected" incident
The first attempt (16:41 UTC) failed. The car itself answered `car could not execute command: disconnected`, meaning it was **not plugged in**, but Victron still showed `1` Connected. The flow had already saved `handled: true`, so it would not try again. Once the car was really plugged in and the session file was cleared, the 16:48 attempt succeeded and Victron showed `2` Charging.

### 3. Don't spend the attempt when the car says it's unplugged
Two nodes were added after the start command's error output:
- **Rearm if car says unplugged** (`5e1a7c0d2b9f4a01`). If the error contains `could not execute command: disconnected`, it writes `{"handled": false, "retry_after": now + 30 min}`. Any other error keeps `handled: true`, so a token or quota failure can't cause a wake and start every 5 minutes.
- **Save rearmed session** (`5e1a7c0d2b9f4a02`) writes that file.
- The start rules now wait until `retry_after` passes ("Car said unplugged; retry after …").

This was tested offline with six cases and deployed. It has **not yet run live**, because that needs the car to answer "disconnected" again.

### 4. Start as often as needed (2026-10-29)
On 2026-10-29 the car had stayed plugged in since the previous day's start. The flow still had `{"handled": true}` from 16:52 the day before, so it showed "Already handled this plug-in session" even though battery was 36% and solar was 1803 W. The once-per-plug-in rule only reset when Victron showed `0`, and that never happened.

The once-per-plug-in rule was replaced by the pause-based rules described in "Current start rules" above. The old `{"handled": true}` file is read as "no pause", so it needed no migration. Nodes renamed: "Rearm/pause after failed start", "Save pause", "Save attempt BEFORE command". Tested offline with 13 cases before deploying.

### 5. Wait for the car to actually wake (2026-10-29)
The first start under the new rules (16:02 UTC) failed after 5 seconds with `vehicle unavailable: vehicle is offline or asleep`. That was then treated as "other error" and paused 3 hours.

**Cause:** the installed `tesla-control` returns from `wake` as soon as Tesla accepts the request, while the car is still asleep. In the source on the Pi, a `200` reply with state `asleep` has `err == nil`, and `protocol.Temporary(nil)` is false, so the loop returns success (`/data/tesla/vehicle-command/pkg/connector/inet/inet.go:219-227`, `pkg/protocol/error.go:110-115`). The next command (`charging-set-amps`) then hits a sleeping car and fails. That error, `ErrVehicleNotAwake`, is marked not-temporary (`inet.go:41`), so it isn't retried. The 2026-10-28 start only worked because an earlier attempt had already woken the car. The installed binary (`/data/home/nodered/go/bin/tesla-control`, Feb 2024) is assumed to be built from that source; this wasn't confirmed. **Upgrading does not fix this.** The latest release, v0.4.1 (2026-02-04), has the same `Wakeup` logic in `pkg/connector/inet/inet.go`: a `200` "asleep" reply still returns success. The helper's retry below is the fix. An upgrade is still needed later for the Bluetooth plan, which uses newer commands.

**Fix**, in `/data/tesla/node-red-tesla.py` `start()`:
- Send `wake`. An "offline or asleep" error from the wake itself is ignored.
- Retry `charging-set-amps 5` every **10 s** while the error is "offline or asleep", for up to **120 s**. Send one more `wake` after **45 s**. Any other error fails at once. The Node-RED exec timer is 180 s, and each `tesla-control` call has a 20 s timeout (`COMMAND_TIMEOUT` in `change-tesla-charging-rate.py:22`).
- Then `charging-start`.
- Each retry is a billed Fleet API request (any reply below status 500 is billed). A slow wake costs roughly 1–2 cents in total.

The failure node now gives an asleep error the 30-minute pause instead of 3 hours. Tested offline: 7 cases for the script and 3 for the node. After the fix, the 16:07 UTC start succeeded in 5 seconds, and Victron showed `2` Charging. The car was probably still awake from the 16:02 attempt, so **the retry loop hasn't yet been exercised on a sleeping car.**

#### Upgrading tesla-control later
Not done; deferred until the Bluetooth work. Facts gathered 2026-10-29:
- There are two identical 2024 binaries (14,012,531 bytes):
  - `/data/home/nodered/go/bin/tesla-control` is used by Node-RED through `change-tesla-charging-rate.py`, which adds `~/go/bin` to `PATH`.
  - `/data/home/root/go/bin/tesla-control` is used by the driver through `GOPATH/bin` (`dbus-teslaapi-evcharger.py:40-41`). `/home/root` links to `/data/home/root`.
  - Upgrade both, or point both at one copy.
- The Pi has Go 1.22.0 (`/data/usr/local/go`, armv7l). v0.4.1 needs Go 1.23, which `GOTOOLCHAIN=auto` would download during the build. There is no `git` on the Pi, and `/data` has plenty of space.
- Build into a staging folder (`GOBIN=/data/tesla/tesla-control-v0.4.1 go install github.com/teslamotors/vehicle-command/cmd/tesla-control@v0.4.1`). Test it by hand, then swap it in and keep the old binaries as backups.

> Sections 1–3 describe the rules as they were on 2026-10-28. "Current start rules" is what runs now.

### Backups and deploy method
- `/data/home/nodered/.node-red/flows.json.pre-victron-status-20261028-162429`
- `/data/tesla/node-red-tesla.py.pre-victron-status-20261028-162429`
- `/data/home/nodered/.node-red/flows.json.pre-rearm-20261028-165115`
- `/data/home/nodered/.node-red/flows.json.pre-multistart-20261029-155938`
- `/data/tesla/auto-start-state.json.pre-multistart-20261029-155938`
- `/data/tesla/node-red-tesla.py.pre-wakewait-*` and `/data/home/nodered/.node-red/flows.json.pre-wakewait-*` (2026-10-29)

The rearm and multi-start changes were deployed through the Node-RED admin API (`POST http://127.0.0.1:1880/flows` with `Node-RED-Deployment-Type: nodes`), which restarts only the changed nodes. If an editor tab is open at the time, reload it instead of clicking Deploy, or it will overwrite the change.

Useful read-only checks:
- Flow memory: `curl http://127.0.0.1:1880/context/flow/262a0e697e4f1c1a`
- Charger status: `dbus -y com.victronenergy.evcharger.http_41 /Status GetValue`
- Live node status: subscribe to `status/#` on the editor websocket `ws://127.0.0.1:1880/comms`.

## Open bug: Victron status goes stale or wrong

Victron showed `/Status` = 1 while the driver's own last Tesla data said `charging_state: Disconnected`. That data should map to 0 (`dbus-teslaapi-evcharger.py:175-176`). Also, after the 16:42 wake the driver should have refreshed within 5 minutes, and the screen still said Connected. **Root cause not found.** Next step: read the rest of the driver's status code and the driver log after 16:42. This affects the Victron screen and VRM as well as the flow.

## Cost facts

From Tesla's [billing and limits](https://developer.tesla.com/docs/fleet-api/billing-and-limits) page (via search):

| Category | Price |
|---|---|
| Data (`vehicle_data`) | $1 per 500 requests |
| Commands | $1 per 1,000 |
| Wakes | $1 per 50 |
| Streaming signals (Fleet Telemetry) | $1 per 150,000 |

- Each account gets **$10 off per month**.
- **Every reply below status 500 is billed**, so a sleeping car's 408 replies count too.
- Rate limits: 60 data requests, 3 wakes and 30 commands per minute.
- Current polling is about 240 calls a day (5 min by day, 10 min at night), about 7,200 a month, about **$14/month**. That is above the credit. This is an estimate; check developer.tesla.com → app → Billing and Usage.
- 30-second polling triggered `403 EXCEEDED_LIMIT` in June 2026. That is why the poll is now 120 s while charging and 300 s idle (`dbus-teslaapi-evcharger.py:83-84`).

## Faster plug-in detection: options

Goal: notice plug-in within about a minute without adding Tesla cost.

| Option | Speed | Tesla cost | Notes |
|---|---|---|---|
| **Bluetooth (BLE) from a Pi near the car** | ~30–60 s | $0, never touches Fleet API | Needs the Pi within Bluetooth range. Chosen. |
| Fleet Telemetry (car pushes changes to your server) | seconds | tiny | Needs a public TLS server the car can reach, plus a telemetry config. Most work. Fallback. |
| Paid relay service (e.g. Teslemetry) | seconds | subscription | Third party holds your token. Not researched in depth. |
| Poll faster | n/a | 2–10× current | Rejected. This caused `EXCEEDED_LIMIT`. |
| Sense power at the charger | n/a | $0 | Rejected. The charger is a Tesla **Mobile Connector** (`fast_charger_type: MCSingleWireCAN`). It has no local API and draws nothing different when plugged in but not charging. The Wall Connector's local `/api/1/vitals` (`vehicle_connected`) does not apply. |

## How Bluetooth works with a sleeping car

From upstream `tesla-control` ([commands.go](https://raw.githubusercontent.com/teslamotors/vehicle-command/main/cmd/tesla-control/commands.go)):
- `body-controller-state`: "Works over BLE when infotainment is asleep." It talks to the always-on security controller (VCSEC), so it can answer without waking the car. It returns only basic body status, such as asleep or awake and locks/doors. **It does not report the charge cable.**
- `state charge`: "Fetch vehicle state over BLE." It needs the infotainment system **awake**.
- Plugging in wakes the car. So the trigger is: poll `body-controller-state` cheaply, and when the car goes from asleep to awake, read `state charge`.

The `tesla-control` installed on the Victron Pi (`/data/home/nodered/go/bin/tesla-control`) is too old to have either command. The Victron Pi does have a working Bluetooth radio (`hci0`), but it may be too far from the car. Hence a second Pi.

None of this has been tested on this car yet.

## Plan: a second Pi near the car

### Phase 0 — hands-on test (no repo code)
Any Pi with built-in Bluetooth, for example a Pi Zero 2 W running Raspberry Pi OS Lite.
1. Install Go and the current tool: `go install github.com/teslamotors/vehicle-command/cmd/tesla-control@latest` (and `tesla-keygen`).
2. **Give it its own key.** Don't copy `/data/tesla/private.pem`. Run `tesla-keygen create`, then `tesla-control -ble -vin <VIN> add-key-request <pub.pem> owner cloud_key`, then tap the key card on the console. That key can be removed on its own if the Pi is lost.
3. From the parking spot, run `tesla-control -ble -vin <VIN> -key-file <key> body-controller-state`. Record the exact output.
4. With the car just plugged in, run `state charge` (and `state closures`). Record which fields show plug state.
5. Run step 3 every 60 s for about 2 h with the car left alone. Confirm the car still falls asleep, and note how fast a plug-in shows up.
6. Choose how the near Pi reports to the Victron Pi, by testing, not assuming:
   - (a) MQTT, a lightweight publish/subscribe protocol: publish to the Venus OS broker on port 1883 under a custom topic like `tesla/ble/plug`. Confirm the broker accepts custom topics.
   - (b) A Node-RED `http in` endpoint. Node-RED listens only on 127.0.0.1:1880. LAN access goes through port 1881, whose authentication hasn't been checked.

**Stop if** Bluetooth can't hold a connection from any workable spot, or polling keeps the car awake. Then plan Fleet Telemetry instead.

### Phase 1 — watcher service (new code in this repo)
New folder `BlePlugWatcher/`, laid out like `TokenRefresh/`:
- `ble-plug-watcher.py`: every 30–60 s run `body-controller-state`.
  - Asleep → publish `{"awake": false, "ts": ...}`.
  - Awake → run `state charge` and publish `{"awake": true, "charging_state", "charge_port_latch", "plugged", "ts"}`.
  - Timeouts on every call. Log failures and keep running. Back off to about 5 min after repeated Bluetooth failures.
- `test_ble_plug_watcher.py`: parser tests built from the outputs recorded in Phase 0.
- `ble-plug-watcher.service`: a systemd unit (the near Pi runs normal Raspberry Pi OS, not Venus/daemontools).
- `README.md`: setup steps.

### Phase 2 — use it in the start flow (Victron Pi)
- An input node (MQTT or HTTP) stores `flow.set("blePlug", {...msg, receivedAt})`.
- When `blePlug` is under 3 min old and `awake: true`, the start rules use it in place of the Victron status:
  - `Disconnected` → clear the pause.
  - `Charging` → nothing.
  - `Complete` → nothing. This also fixes the "full car shows as Connected" problem.
  - `Stopped` with latch `Engaged` → eligible, subject to the pause and the battery/solar rule.
  - Otherwise, fall back to the Victron status logic. Keep "Rearm/pause after failed start" as a safety net.
- A BLE message triggers a check, still behind the 5-minute limit.
- Back up `flows.json` first and deploy with the `nodes` deploy type.

Separate follow-ups: feed BLE state into the driver so the Victron screen and VRM stop going stale, and slow the 5-min Fleet poll to get under the $10 credit.

### Verification
1. `python -m pytest BlePlugWatcher/` passes.
2. `journalctl -u ble-plug-watcher -f` on the near Pi shows heartbeats.
3. The Node-RED context API shows a fresh `blePlug`.
4. Car unplugged and asleep → plug in → start within about 1–2 min. Unplug → pause cleared. Tesla billed usage doesn't rise.

### Not verified yet
- Bluetooth range at the parking spot, and whether polling stops the car from sleeping.
- The exact output format of `body-controller-state` and `state charge` on this car.
- Whether the Venus MQTT broker accepts custom topics, and how port 1881 authenticates.
- Actual billed usage, and whether `GET /api/1/vehicles/{id}` is billed (Tesla's docs don't say).

## Sources
- Tesla Fleet API billing and limits: https://developer.tesla.com/docs/fleet-api/billing-and-limits
- Tesla Fleet API vehicle endpoints: https://developer.tesla.com/docs/fleet-api/endpoints/vehicle-endpoints
- `tesla-control` command list: https://raw.githubusercontent.com/teslamotors/vehicle-command/main/cmd/tesla-control/commands.go
- evcc Tesla BLE: https://docs.evcc.io/en/vehicles/tesla-ble.md
- tesla_ble_mqtt (range and polling notes): https://hub.docker.com/r/iainbullock/tesla_ble_mqtt
- Wall Connector local API (not applicable here): https://community.openhab.org/t/tesla-wall-charger-gen3-api-statistics/133430
