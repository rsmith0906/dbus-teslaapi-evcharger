# 04 — Tesla integration: API, OAuth, and `tesla-control`

This driver hits Tesla through three different surfaces. Knowing which one does what is essential because Tesla has been actively deprecating one of them.

## The three Tesla surfaces in use

```
                       +------------------------------------+
                       |   tesla-control (Go binary)        |
                       |   github.com/teslamotors/          |
                       |        vehicle-command             |
   /StartStop write -> |   * signs commands with private    |
                       |     key registered on the car      |
                       |   * speaks the new vehicle-command |
                       |     protocol over BLE or HTTPS     |
                       +------------------------------------+
                                       |
                                       v
                           Tesla Fleet API endpoints
                           ----------------------------
                           wake, charging-start,
                           charging-stop,
                           charging-set-amps

   poll loop --------->  GET https://owner-api.teslamotors.com
                              /api/1/vehicles/{id}/vehicle_data
                         (read-only, plain bearer token, still works)

   token refresh ----->  POST https://auth.tesla.com/oauth2/v3/token
                         (OAuth refresh_token grant)
```

So one binary for **commands**, raw HTTP for **reads**, and OAuth for **auth refresh**.

## 1. Reading vehicle state — `vehicle_data`

`dbus-teslaapi-evcharger.py:177` builds:

```
GET https://owner-api.teslamotors.com/api/1/vehicles/<VehicleId>/vehicle_data
Authorization: Bearer <access_token>
```

This returns a JSON document with sub-objects for `charge_state`, `drive_state`, `vehicle_state`, `climate_state`, `gui_settings`. The driver only reads from `charge_state`, plus `vehicle_state.car_version` (used as the FirmwareVersion) and `drive_state.{speed, shift_state}` (to detect driving so the poll backs off).

Specific fields read (line 373 onwards):

| JSON path | DBus path it lands on | Notes |
|-----------|----------------------|-------|
| `charge_state.charger_actual_current` | `/Current` | A |
| `charge_state.charger_voltage` | (used in `voltage * current` for `/Ac/Power`) | V |
| `charge_state.charger_power` | (read but unused — see issue list) | kW |
| `charge_state.charging_state` | drives `/Status` | `Disconnected | Stopped | Starting | Charging | Complete | NoPower` |
| `charge_state.charge_port_latch` | drives `/Status` (port engaged or not) | `Engaged | Disengaged` |
| `charge_state.charge_energy_added` | (read, never written to dbus) | kWh — bug; should land on `/Ac/Energy/Forward` |
| `charge_state.charge_current_request_max` | `/MaxCurrent` | A |
| `charge_state.battery_level` | (read, never written) | % |
| `drive_state.speed` | (poll back-off only) | mph |
| `drive_state.shift_state` | (poll back-off only) | `P/R/N/D` or null |
| `vehicle_state.car_version` | `/FirmwareVersion` | string |
| `vin` | `/Serial` | string |

### Rate-limit awareness

The `_too_many_requests` and `_request_timeout_string` checks (lines 76-77, 438-447) catch HTTP 429 and timeouts. On 429 it bumps `_wait_seconds` by 30 s; on timeout it backs off to 5 minutes (the car has gone to sleep). Tesla's owner-API cap was historically very forgiving but became stricter in 2024–2025 — the [TeslaFi 2025 commands limit explainer](https://teslafi.zendesk.com/hc/en-us/articles/35106001690779-2025-Tesla-Commands-Limit) is a useful reference.

### Polling cadence

`_wait_seconds` is the throttle. It starts at 30 s and is rewritten throughout `_update`:

| Condition | `_wait_seconds` set to |
|-----------|------------------------|
| Default | 30 |
| Inverter power changed > 1 W | 30 |
| Inverter power changed > 400 W | reset `_lastCheckData` to force immediate poll |
| Charge state == `Stopped` or `Complete` | 300 (5 min) |
| Charge state == anything else (idle) | 300 (5 min) |
| Car driving | 3600 (1 h) |
| Time between 06:00 and 14:00 *and* not charging | 600 (10 min) — see note below |
| HTTP 429 | `_wait_seconds + 30` |
| Request timeout (sleeping car) | 300 (5 min) |
| `NoPower` charge state | 300 (5 min) |

> Note: `is_time_between_midnight_and_8am()` (line 606) is misleadingly named — the actual range hardcoded is 06:00 to 14:00. The function name reflects an older draft.

## 2. Sending commands — `tesla-control`

Tesla's owner-API stopped accepting unsigned `command/charge_start` etc. on most newer vehicles (notably 2021+ Model S/X and 2024+ Model 3/Y after the migration to the [Fleet API + vehicle-command protocol](https://github.com/teslamotors/vehicle-command)). The driver therefore shells out to `tesla-control`, the official Go CLI Tesla publishes for that purpose:

```bash
tesla-control wake
tesla-control charging-start
tesla-control charging-stop
tesla-control charging-set-amps <amps>
```

The CLI signs each request with an Ed25519 private key the user enrolled in the car ahead of time (typically by walking up to it with the BLE-paired key once, scanned from a QR pointing to the public key, then accepting in the Tesla app). The signed envelope can be sent **over BLE locally** or **over the Internet via the Fleet API**, depending on flags.

This driver uses the Internet path implicitly because it sets:

```python
os.environ['TESLA_VIN']        = config['VIN']
os.environ['TESLA_KEY_NAME']   = 'Tessy'
os.environ['TESLA_KEY_FILE']   = '/data/tesla/private.pem'
os.environ['TESLA_TOKEN_FILE'] = '/data/tesla/token.txt'
```

…and `tesla-control` needs the `private.pem` (whose public key is enrolled on the car) and a current OAuth token in `token.txt`. There's no `-ble` flag in the calls, so it goes over HTTPS.

`TESLA_KEY_FILE` and `TESLA_TOKEN_FILE` are environment variables the driver sets explicitly. The current upstream `vehicle-command` README emphasizes `TESLA_KEY_NAME` + the system keyring as the primary mechanism, but file-based mode still works — the binary checks `TESLA_KEY_FILE` before falling back to the keyring.

The Go binary is built locally (the script extends `PATH` with `$(go env GOPATH)/bin`), implying the user did:

```bash
go install github.com/teslamotors/vehicle-command/cmd/tesla-control@latest
```

…on the Pi, with Go installed under `/data/usr/local/go` so it survives Venus OS firmware updates.

## 3. Refreshing the OAuth token — `auth.tesla.com`

The token Tesla issues for both the read API and `tesla-control` is short-lived. Refresh happens via:

```
POST https://auth.tesla.com/oauth2/v3/token
Content-Type: application/x-www-form-urlencoded
grant_type=refresh_token
client_id=<from config.json CLIENT_ID>
refresh_token=<from authtoken.txt>
scopes=user_data vehicle_device_data vehicle_cmds vehicle_charging_cmds
```

On success, response contains `access_token`, `refresh_token` (rotated), `expires_in`, `token_type`. The driver:

1. Writes the entire JSON back to `/data/tesla/authtoken.txt` (so the next refresh uses the new refresh_token).
2. Writes only the access token to `/data/tesla/token.txt` (so `tesla-control` can read it).
3. Writes `now + expires_in - 1000` seconds (≈ 17 min safety margin if `expires_in` is 8 h) to `/data/tesla/tokenexpire.txt`.

There are **two** code paths that do this refresh:

- **In-process** (`_getAccessToken`, line 221) — used to get a token for the *read* API (`vehicle_data`). This path uses the legacy `client_id=ownerapi` and the old `scope=openid email offline_access` style, which still works for owner-API reads.
- **`get_new_token`** (line 516) — used when `tesla-control` reports a token error. Uses the *Fleet-API*-style scopes (`user_data vehicle_device_data vehicle_cmds vehicle_charging_cmds`) and `CLIENT_ID` from `config.json`.

These two code paths use **different OAuth client IDs and scopes** because they're talking to different APIs. The legacy ownerapi flow reads telemetry; the Fleet-API flow signs commands.

The sidecar `TokenRefresh/tesla-api-token-refresh.py` runs the second one preemptively every 4 hours so the file system always has a fresh token, independent of whether `tesla-control` was recently invoked.

## Why two flows in one repo?

Because Tesla split their API in 2023:

- **Owner API** (`owner-api.teslamotors.com`) — what enthusiasts have been using since 2014. Reads still work; commands are mostly being phased out for newer cars.
- **Fleet API** (`fleet-api.prd.na.vn.cloud.tesla.com` / `auth.tesla.com` for OAuth) — the new official API for third parties. Commands require the vehicle-command signed-envelope flow.

The driver reads from the Owner API (cheap, simple, still reliable for `vehicle_data`) and writes via Fleet API through `tesla-control`. That hybrid is awkward but not unusual — many community tools currently look like this during the transition.

## References for this section

- [`teslamotors/vehicle-command`](https://github.com/teslamotors/vehicle-command) — official `tesla-control` source.
- [Tesla Fleet API endpoints](https://developer.tesla.com/docs/fleet-api/endpoints/vehicle-commands) — `charge_start`, `charge_stop`, `set_charging_amps`, `wake_up`.
- [Unofficial Owner API reference (timdorr)](https://tesla-api.timdorr.com/vehicle/commands/charging) — the historical reference that defined `/api/1/vehicles/{id}/vehicle_data` semantics.
- [TeslaFi 2025 commands limit](https://teslafi.zendesk.com/hc/en-us/articles/35106001690779-2025-Tesla-Commands-Limit) — current rate-limit context.
