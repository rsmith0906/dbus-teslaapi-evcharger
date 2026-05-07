# 09 — References

Every external doc cited while writing these notes. Verified against the live pages.

## Victron — Venus OS, DBus, drivers

- [victronenergy/venus wiki — DBus paths reference](https://github.com/victronenergy/venus/wiki/dbus) — canonical list of every `com.victronenergy.<role>` service and the paths each must expose. Used for [03-evcs-dbus-contract.md](03-evcs-dbus-contract.md).
- [victronenergy/venus wiki — DBus API](https://github.com/victronenergy/venus/wiki/dbus-api) — mechanics of the `vedbus` protocol (`/UpdateIndex`, `GetValue`, `SetValue`, signals).
- [victronenergy/venus wiki — How to add a driver](https://github.com/victronenergy/venus/wiki/howto-add-a-driver-to-Venus) — daemontools/runit, `/service/`, `rc.local` persistence.
- [victronenergy/dbus-modbus-client — `ev_charger.py`](https://github.com/victronenergy/dbus-modbus-client/blob/master/ev_charger.py) — Victron's own EVCS driver source. Authoritative for `/Status`, `/Mode`, `/StartStop`, `/Position` enum values.
- [victronenergy/dbus-spy](https://github.com/victronenergy/dbus-spy) — TUI tool for browsing live DBus values, ships on Venus OS.
- [Venus OS root access](https://www.victronenergy.com/live/ccgx:root_access) — how to enable SSH on a GX device including a Pi.
- [Venus OS operational commandline manual (PDF)](https://www.victronenergy.com/live/open_source:ccgx:commandline?do=export_pdf) — non-web overview of the on-device shell environment.

## Victron — VRM Portal & APIs

- [VRM Portal start](https://www.victronenergy.com/live/vrm_portal:start) — landing page, links to user manual.
- [VRM Portal manual — introduction](https://www.victronenergy.com/media/pg/VRM_Portal_manual/en/introduction.html) — features overview (monitoring, alerts, two-way control, dashboards).
- [VRM API documentation v2](https://vrm-api-docs.victronenergy.com/) — REST API for third-party integrators. `/v2/users/{id}/installations`, access tokens, etc.
- [Software Integrator Program announcement (Dec 2024)](https://www.victronenergy.com/blog/2024/12/04/introducing-our-new-software-integrator-program/) — current commercial integration channel.
- [dirkjanfaber/victron-vrm-api](https://github.com/dirkjanfaber/victron-vrm-api) — Node-RED node wrapping the VRM API; useful as a reference implementation.

## Victron — GUI

- [victronenergy/gui-v2](https://github.com/victronenergy/gui-v2) — React/QML GUI for Cerbo GX, Ekrano GX, GX Touch, and Venus OS web GUI.
  - [`pages/evcs/EvChargerPage.qml`](https://github.com/victronenergy/gui-v2/blob/main/pages/evcs/EvChargerPage.qml) — what the EVCS detail page actually binds to.
  - [`data/common/EvCharger.qml`](https://github.com/victronenergy/gui-v2/blob/main/data/common/EvCharger.qml) — the EvCharger data model with the fixed list of paths.
- [Victron Community — modifying gui-v2](https://community.victronenergy.com/t/modifying-gui-v2/10074) — community thread on patching QML.
- [kwindrem/GuiMods](https://github.com/kwindrem/GuiMods) — community fork of gui-v1/v2 with extra panels.

## Victron — MQTT

- [victronenergy/dbus-mqtt](https://github.com/victronenergy/dbus-mqtt) — original DBus↔MQTT bridge (replaced by dbus-flashmq in Venus OS 3.20+).
- [`venus-html5-app/TOPICS.md`](https://github.com/victronenergy/venus-html5-app/blob/master/TOPICS.md) — best human-readable description of the `R/W/N/<portalid>/...` topic scheme and the keep-alive mechanism.

## Tesla

- [`teslamotors/vehicle-command`](https://github.com/teslamotors/vehicle-command) — official `tesla-control` and `tesla-keygen` source. Documents `TESLA_VIN`, `TESLA_KEY_NAME`/`TESLA_KEY_FILE`, and the BLE/HTTPS signed-command flow.
- [Tesla Fleet API — Vehicle Commands](https://developer.tesla.com/docs/fleet-api/endpoints/vehicle-commands) — official endpoint reference for `charge_start`, `charge_stop`, `set_charging_amps`, `wake_up`. (HTTP 403 from anonymous browsers; accessible after free developer signup.)
- [Tim Dorr's unofficial Tesla JSON API](https://tesla-api.timdorr.com/vehicle/commands/charging) — historical reference for the legacy owner-API including `vehicle_data` shape.
- [TeslaFi 2025 commands limit](https://teslafi.zendesk.com/hc/en-us/articles/35106001690779-2025-Tesla-Commands-Limit) — current rate-limit context.

## Inspirational forks (cited in repo's own README)

- [fabian-lauer/dbus-shelly-3em-smartmeter](https://github.com/fabian-lauer/dbus-shelly-3em-smartmeter) — the structural ancestor; this repo started as a fork.
- [vikt0rm/dbus-shelly-1pm-pvinverter](https://github.com/vikt0rm/dbus-shelly-1pm-pvinverter) — sibling fork that informed the install/service patterns.

## Community examples that follow the same driver pattern

- [mr-manuel/venus-os_dbus-mqtt-ev-charger](https://github.com/mr-manuel/venus-os_dbus-mqtt-ev-charger) — full reference for the EVCS path set, MQTT-fed.
- [mr-manuel/venus-os_dbus-serialbattery](https://mr-manuel.github.io/venus-os_dbus-serialbattery_docs/) — most-downloaded community DBus driver; install/uninstall scripts mirror the pattern in this repo.
- [h4ckst0ck/dbus-solaredge](https://github.com/h4ckst0ck/dbus-solaredge) — SolarEdge inverter as `com.victronenergy.pvinverter`.
- [madsci1016/SMAVenusDriver](https://github.com/madsci1016/SMAVenusDriver) — SMA SunnyIsland integration, same scaffolding.
