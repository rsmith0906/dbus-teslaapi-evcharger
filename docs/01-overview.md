# 01 — Overview

## What problem this repo solves

Victron's GX ecosystem (Cerbo GX, Venus GX, Ekrano GX, Raspberry Pi running Venus OS) has a first-class component type for EV charging: the **EV Charging Station (EVCS)**. The GX GUI and the VRM cloud portal both render an `EVCS` tile when a DBus service of type `com.victronenergy.evcharger` is present, with built-in support for showing charging power, set-current, max-current, charging time, total energy, and a Start/Stop control.

Victron's own EVCS is a real wallbox. A Tesla isn't a wallbox — it's the *car* that consumes the AC output. But for an off-grid / ESS user, having the car appear in VRM as a load with start/stop and current control is genuinely useful, because the existing Victron tooling (advanced graphs, alerts, the dashboard schematic, the future Dynamic ESS load-shifting) all pivot on those same DBus paths.

This repo bridges the gap: the Tesla owner-API supplies live charge state, and `tesla-control` (the official signed-command CLI) accepts start/stop/amp commands. The Python service in the middle pretends to be an EVCS device on the local DBus.

## Runtime topology

```
+------------------------- Raspberry Pi running Venus OS -------------------------+
|                                                                                  |
|   /service/dbus-teslaapi-evcharger/run  ---> python dbus-teslaapi-evcharger.py   |
|         |                                          |                            |
|         | daemontools supervise                    | velib_python.VeDbusService |
|         |                                          v                            |
|         |                                  +----------------------+              |
|         |                                  | system DBus          |              |
|         |                                  | com.victronenergy.   |              |
|         |                                  |   evcharger.http_41  | <--+         |
|         |                                  +----------------------+    | reads   |
|         |                                                              |         |
|   /service/TokenRefresh/run -----> python tesla-api-token-refresh.py   |         |
|         | refreshes /data/tesla/token.txt every 4h                     |         |
|                                                                        |         |
|   subprocess: /data/usr/local/go/bin/tesla-control                     |         |
|         (wake, charging-start, charging-stop, charging-set-amps)       |         |
|                                                                        |         |
|   GX GUI / Remote Console / VRM Portal  <---------------------- system DBus     |
|                                                                                  |
+----------------------------------+-----------------------------------------------+
                                   |
                                   | HTTPS (OAuth + REST)
                                   v
                          owner-api.teslamotors.com
                          auth.tesla.com (token refresh)
```

Two long-running services, one DBus driver they both feed:

1. **`dbus-teslaapi-evcharger.py`** — the EVCS shim. Polls Tesla every 30 s (back-off varies), writes to DBus, listens for `/StartStop` writes from the GUI, and translates them into BLE-or-HTTP signed commands via `tesla-control`.
2. **`TokenRefresh/tesla-api-token-refresh.py`** — sidecar that rotates the OAuth refresh token periodically so the access token in `/data/tesla/token.txt` stays alive.

Plus two on-demand scripts callable by hand or from a Node-RED flow:

- `change-tesla-charging-rate.py <amps>` — `tesla-control charging-set-amps`
- `change-tesla-charging-status.py 0|1` — `tesla-control charging-stop|charging-start`

## Why this works without a real EVCS

Venus OS is essentially a Linux distro with a strict DBus naming convention. Anything matching `com.victronenergy.<role>.<instance>` and exposing the documented paths is treated by the GUI and the VRM cloud as that kind of device — there is no central registry, no certification, no driver signing. So a Python script with `vedbus.VeDbusService("com.victronenergy.evcharger.http_41")` is, as far as the system is concerned, an EV charger. That is the same mechanism every community driver uses (dbus-serialbattery, dbus-shelly-3em-smartmeter, dbus-solaredge, etc.).

## What this repo deliberately does *not* do

- **It is not a real EVCS load-balancer.** `/SetCurrent` is wired to a stub — `_setcurrent` is a no-op (`test = 0`). Auto-balancing against PV surplus would need to call `change-tesla-charging-rate.py` from `_setcurrent`.
- **It does not send car-side commands directly via the Tesla REST API.** Tesla has been hardening the owner-API for several years; commands now require signed messages via the vehicle-command protocol. That is what `tesla-control` provides. The repo only uses raw HTTP for the *read* path (`vehicle_data`) and OAuth.
- **It does not implement `/Mode = Auto`.** `/Mode` is exposed but never honored — value is hardcoded to 0 (Manual) on first run.
