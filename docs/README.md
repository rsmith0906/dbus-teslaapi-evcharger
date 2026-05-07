# dbus-teslaapi-evcharger — Documentation

This directory documents how the repository turns a 2022 Tesla Model 3 into a virtual EV charging station inside the Victron VRM portal.

The screenshot in `Photon Farm` shows the desired result: an **EVCS** tile on the VRM dashboard reporting `1368 W` charging at `12.0 A` in `Manual` mode against vehicle `Guest EV`. None of those fields come from a real Victron EV Charging Station — they come from this repo polling the Tesla owner-API and republishing the data on the local DBus under `com.victronenergy.evcharger`.

## Index

| Doc | What's in it |
|-----|--------------|
| [01-overview.md](01-overview.md) | What the system is, the runtime topology, why it works |
| [02-victron-vrm-platform.md](02-victron-vrm-platform.md) | Background on VRM, Venus OS, GX devices, custom dev model |
| [03-evcs-dbus-contract.md](03-evcs-dbus-contract.md) | The `com.victronenergy.evcharger` DBus contract this driver implements |
| [03b-custom-properties.md](03b-custom-properties.md) | Adding custom properties to the EVCS service — yes on the bus, no in GUI/VRM without patching |
| [03c-side-panel-widgets.md](03c-side-panel-widgets.md) | Custom side-panel cards (CPU/Inverter/Weather style) — what's possible and what's not |
| [04-tesla-integration.md](04-tesla-integration.md) | Tesla owner-API, Fleet API, OAuth refresh, `tesla-control` CLI |
| [05-runtime-walkthrough.md](05-runtime-walkthrough.md) | Line-by-line walkthrough of `dbus-teslaapi-evcharger.py` |
| [06-installation-and-config.md](06-installation-and-config.md) | Install/restart/uninstall scripts, `config.ini`, `config.json`, file layout |
| [07-companion-scripts.md](07-companion-scripts.md) | `change-tesla-charging-rate.py`, `change-tesla-charging-status.py`, TokenRefresh service |
| [08-issues-and-improvements.md](08-issues-and-improvements.md) | Bugs, smells, and concrete improvement candidates spotted during the audit |
| [09-references.md](09-references.md) | Every external doc cited while writing these notes |

## TL;DR

- A Python long-running service runs as a daemontools-supervised driver under `/service/` on a Raspberry Pi running Venus OS.
- It registers itself on the system DBus as `com.victronenergy.evcharger.http_41` (the `41` comes from `Deviceinstance` in `config.ini`).
- It polls `https://owner-api.teslamotors.com/api/1/vehicles/<id>/vehicle_data` every ~30 s (longer when idle / sleeping / driving / between midnight and 8 AM logic) and copies `charge_state.*` into DBus paths the GX GUI and VRM portal already know how to render.
- Writes to `/StartStop` are translated into `tesla-control wake` followed by `tesla-control charging-start | charging-stop`. `tesla-control` is the official Go CLI from `teslamotors/vehicle-command` and signs commands with a private key the user enrolled in the vehicle.
- A sidecar service in `TokenRefresh/` rotates the OAuth refresh token every ~4 hours so the access token in `/data/tesla/token.txt` stays fresh.
