# 03 — The `com.victronenergy.evcharger` DBus contract

This driver registers as an EV charger on the system bus. Below is the contract VRM and the GX GUI expect, mapped to which paths this code actually drives.

## Service name

```
com.victronenergy.evcharger.http_<deviceinstance>
```

The `http_` prefix is the connection-type hint Victron uses for HTTP-fed virtual devices (mirroring the `http_` prefix used by the Shelly drivers this repo was forked from). `<deviceinstance>` comes from `config.ini → DEFAULT.Deviceinstance` (currently `41`). See `dbus-teslaapi-evcharger.py:63`.

## Mandatory management paths

Every Venus DBus service is required to expose these (added in `add_standard_paths`, line 102):

| Path | Value used | Source |
|------|------------|--------|
| `/Mgmt/ProcessName` | `__file__` | line 104 |
| `/Mgmt/ProcessVersion` | `'Unknown version, and running on Python ' + platform.python_version()` | line 105 |
| `/Mgmt/Connection` | `'Tesla API HTTP JSON service'` | line 106 |
| `/DeviceInstance` | `41` (from config) | line 109 |
| `/ProductId` | `0xFFFF` (placeholder; real EVCS would have a Victron-assigned ID) | line 110 |
| `/ProductName` | `'Tesla API'` | line 111 |
| `/CustomName` | `TESLACHARGER` (from config) | line 112 |
| `/Connected` | `1` | line 113 |
| `/FirmwareVersion` | car's `vehicle_state.car_version` | line 115 |
| `/HardwareVersion` | `0` | line 116 |
| `/Position` | `0` (AC Output, from config) | line 117 |
| `/Serial` | car's VIN | line 118 |
| `/UpdateIndex` | bumped on every poll, wraps 0..255 | lines 119, 466 |

Bumping `/UpdateIndex` is how subscribers know data changed even when none of the actual values did.

## EVCS-specific paths this driver exposes

| Path | Direction | Meaning per Victron spec | Driver behavior |
|------|-----------|--------------------------|-----------------|
| `/Mode` | RW | `0=Manual, 1=Auto, 2=Scheduled` | Initialized to `0` on first run (line 343); writes are accepted but ignored — there is no auto-mode logic. |
| `/Status` | RO | `0=Disconnected, 1=Connected, 2=Charging, 3=Charged, 4=Waiting for sun, 5=Waiting for RFID, 6=Waiting for start, 7=Low SOC, 8..14 fault codes, 20=Charging limit, 21=Start charging, 22=Switch to 3-phase, 23=Switch to 1-phase, 24=Stop charging` | Drives a small subset: `0` (disconnected), `1` (connected, port latched, not charging), `2` (charging), `10` (mapped to "CP input test error" by spec — the code uses it as a generic "error/unknown" sentinel; this is wrong, see [08-issues-and-improvements.md](08-issues-and-improvements.md)). |
| `/StartStop` | RW | `0=Stop, 1=Start` | Listened for via `_handlechangedvalue` → `_startstop` (line 489, 269). Writes trigger `tesla-control wake` then `charging-start`/`charging-stop`. |
| `/SetCurrent` | RW | Target charging current (A) | Wired to `_setcurrent` (defined line 260) — **no-op stub**. Should call `change-tesla-charging-rate.py`. |
| `/MaxCurrent` | RO | Maximum allowed current (A) | Mirrored from `charge_state.charge_current_request_max`. |
| `/Current` | RO | Actual charging current (A, real EVCS reports 0.1 A increments — this driver reports integer A) | Mirrored from `charge_state.charger_actual_current`. |
| `/ChargingTime` | RO | Session charging time (seconds) | Computed locally as `now - self._startDate` while `charge_state == 'Charging'`. |
| `/Ac/Power` | RO | Total AC power (W) | Computed as `voltage * current` — see `_update` line 399. |
| `/Ac/L1/Power` | RO | Per-phase power (W) | Same value as `/Ac/Power`; only L1 is used because `Phase=L1` in config. |
| `/Ac/Energy/Forward` | RO | Total energy (kWh) | **Currently never written** — see line 387 (commented out). The screenshot shows `Total energy 1 kWh` so something else is populating this, possibly stale. |

## Paths defined by Victron but NOT exposed here

These are part of the spec but the driver does not register them:

- `/AutoStart` — would let the GX/VRM control auto-start when plugged in.
- `/Session/Time`, `/Session/Energy`, `/Session/Cost`, `/Session/SavedCost` — modern session-tracking; the GX GUI uses these instead of `/ChargingTime` + `/Ac/Energy/Forward` on newer firmware.
- `/Ac/L2/Power`, `/Ac/L3/Power` — only relevant for 3-phase wallboxes. A Tesla on L1 only doesn't need them, but writing zeros explicitly avoids stale data.
- `/PositionIsAdjustable`, `/Model`, `/EnableDisplay`, `/MinCurrent` — optional, not used.

## Status code used by the driver — what each value maps to in the wild

```
self._dbusserviceev['/Status'] = 0    # NOT plugged in / disconnected
self._dbusserviceev['/Status'] = 1    # Plugged in, latched, idle (port_latch == 'Engaged' but charge_state in {Stopped, Complete})
self._dbusserviceev['/Status'] = 2    # Actively charging (charge_state == 'Charging')
self._dbusserviceev['/Status'] = 10   # "Something else / unknown" — spec calls this 'CP input test error (shorted)'
```

The use of `10` here is a misuse of a fault code as a catch-all — the GUI may render this as a charge-pilot error.

## Why the GUI auto-discovers this driver

Two things are required:

1. The DBus service name must start with `com.victronenergy.evcharger.` (case-sensitive).
2. `/DeviceInstance` must be unique across all EV chargers in the system. The Pi running Venus OS keeps a settings table at `com.victronenergy.settings` mapping `(serial, role) -> deviceinstance`; the first time the GUI sees a new service it allocates one and writes it into the settings tree. By using a hardcoded `Deviceinstance=41` in `config.ini` the driver bypasses that mechanism, which works as long as nothing else claims `41`.

Sources for the contract above:
- [victronenergy/venus wiki — DBus paths](https://github.com/victronenergy/venus/wiki/dbus)
- [victronenergy/dbus-modbus-client — ev_charger.py](https://github.com/victronenergy/dbus-modbus-client/blob/master/ev_charger.py) (canonical Python implementation)
- [mr-manuel/venus-os_dbus-mqtt-ev-charger](https://github.com/mr-manuel/venus-os_dbus-mqtt-ev-charger) (community implementation that mirrors the spec)
