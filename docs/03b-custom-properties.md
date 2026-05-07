# 03b — Can I add custom properties to the EVCS control?

Short answer: **Yes on the bus, no in the GUI/VRM — without GUI modification.**

## What the bus accepts

`vedbus.VeDbusService.add_path()` lets a driver register *any* path. The Venus OS DBus is not a sealed contract; nothing rejects a non-standard path. So you can do:

```python
self._dbusserviceev.add_path('/Tesla/BatteryLevel', 67)
self._dbusserviceev.add_path('/Tesla/RangeMiles', 234)
self._dbusserviceev.add_path('/Tesla/Geofence', 'home')
```

…and those values will:

1. ✅ Be visible from `dbus-spy` (the built-in TUI debug tool — `dbus-spy` from `/usr/bin`).
2. ✅ Be published over local MQTT as `N/<portalid>/evcharger/41/Tesla/BatteryLevel` automatically by `dbus-flashmq`.
3. ✅ Be readable/writable by other DBus clients (Node-RED, dbus-systemcalc, your own scripts).
4. ❌ Not appear in the GX GUI unless you patch QML.
5. ❌ Not appear in the VRM Portal's standard EVCS tile or Advanced graphs.

## Why the GUI ignores them

The GX device's GUI (gui-v2, the React/QML app) is a **hand-written renderer**, not a dbus introspector. The EVCS detail page binds to a fixed list of UIDs declared in [`data/common/EvCharger.qml`](https://github.com/victronenergy/gui-v2/blob/main/data/common/EvCharger.qml):

```qml
/Session/Energy   /Ac/Power        /Session/Time
/Connected        /Current         /MaxCurrent
/Mode             /Status          /Position
```

Plus `/StartStop`, `/SetCurrent`, `/AllowedRoles`, and `/Ac/L<n>/Power` from [`pages/evcs/EvChargerPage.qml`](https://github.com/victronenergy/gui-v2/blob/main/pages/evcs/EvChargerPage.qml). Any path outside that whitelist is invisible to the page.

> **A by-product of this:** the screenshot shows `Charging time -` (empty). That's because this driver writes to `/ChargingTime`, but gui-v2 reads `/Session/Time`. They're different paths. Adding `/Session/Time` (and `/Session/Energy`) would fix that without touching QML. See [08-issues-and-improvements.md](08-issues-and-improvements.md).

## Why the VRM cloud ignores them

VRM's data ingestion is also hand-curated. Each device class has a known attribute set that the cloud-side accepts, stores, and graphs. The schema is private but the public surface is the [VRM API v2](https://vrm-api-docs.victronenergy.com/) — fields that aren't in their model don't reach widgets, alerts, or Advanced graphs. The GX device uploads only the attributes VRM knows about (driven by the `dbus_modbustcp` attribute table and the cloud-side allowlist).

## What "yes" actually buys you

Custom paths are useful even without GUI surface, in three concrete ways:

1. **Local automation.** Node-RED on the same Pi (or another machine talking to the local MQTT broker) can subscribe to `N/<portalid>/evcharger/41/Tesla/BatteryLevel` and trigger flows: e.g., stop charging when SOC ≥ 80 %, start when surplus PV > 1500 W.
2. **Cross-driver coordination.** Another DBus driver on the same GX device can read your custom paths via `VeDbusItemImport`. This is how `dbus-systemcalc` aggregates everything for the system schematic.
3. **Logging/analytics.** Your own time-series scraper (InfluxDB, etc.) reading the local MQTT firehose stores whatever you publish.

## How to make custom data show up in the GUI

You have three options, ordered by effort:

### Option A — Use the standard paths, just creatively

The path of least resistance. The GUI already renders these; map your Tesla data into the existing slots:

| You have | Map it to | What the GUI shows |
|---------:|-----------|--------------------|
| `charge_state.battery_level` | (no native slot — but you could surface as `/Soc` on a *separate* `com.victronenergy.battery` service) | Battery widget |
| `charge_state.charge_energy_added` | `/Session/Energy` | Session energy line |
| `charge_state.charger_power` (W) | `/Ac/Power` | Power column |
| `charge_state.minutes_to_full_charge` | (no native slot) | n/a |

### Option B — GuiMods / QML patching

Modify `/opt/victronenergy/gui-v2/qml/pages/evcs/EvChargerPage.qml` on the device to add new `ListItem` rows bound to your custom UIDs. Patches live on rootfs and are wiped on every firmware update — re-apply them from `/data/rc.local`.

This is what kwindrem's [GuiMods](https://github.com/kwindrem/GuiMods) does for many other devices. It's not officially supported but is fully tolerated.

### Option C — A second DBus service of a different role

If your custom data fits a different device class, register a *second* service and let the GUI render it natively. e.g., publish the car's SoC as `com.victronenergy.battery.tesla` with `/Soc`, `/Dc/0/Voltage`, etc. The GUI then shows a "Battery: Tesla" tile alongside the EVCS. This is a clean separation — but be careful, because dbus-systemcalc may try to roll the new battery into system totals.

## Summary table

| Goal | Approach | GUI shows it? | VRM shows it? | Effort |
|------|----------|:-:|:-:|------|
| Read from another script | `add_path('/Tesla/X', value)` | ❌ | ❌ | trivial |
| Show in GUI | QML patch via GuiMods | ✅ | ❌ | medium |
| Show in VRM | Fit into standard paths only | ✅ | ✅ | low |
| Show as a separate VRM tile | Register a second `com.victronenergy.<role>` service | ✅ | ✅ | medium |
| Send to external dashboard | local MQTT → Node-RED / Grafana | n/a | n/a | low |

For *this* driver the most valuable, low-effort wins are:
- Add `/Session/Time` (= `/ChargingTime`) and `/Session/Energy` (= `charge_energy_added`) so the existing GUI fields populate.
- Add `/Ac/Energy/Forward` so kWh accumulates correctly.
- Optionally publish `/Tesla/BatteryLevel`, `/Tesla/RangeMiles` for Node-RED automations even though VRM ignores them.
