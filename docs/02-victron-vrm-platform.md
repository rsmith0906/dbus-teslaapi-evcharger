# 02 — The Victron VRM Platform & How Custom Development Works

This is background — the *why* and *where* this driver fits. Skip to the next file if you only care about this repo's behavior.

## The three layers

Victron's monitoring stack is three-tiered, and the same data flows through all three:

```
+--------------------------+
|  VRM Portal (cloud)      |   vrm.victronenergy.com  -- the screenshot
|  + Dynamic ESS, alerts   |
+-----------+--------------+
            ^   HTTPS / MQTT-over-TLS
            |
+-----------+--------------+
|  GX device (Venus OS)    |   Cerbo GX, Ekrano GX, Venus GX, CCGX,
|  + GUI / Remote Console  |   or a Raspberry Pi running Venus OS  <-- this user
+-----------+--------------+
            ^   system DBus
            |
+-----------+--------------+
|  Devices on DBus         |   inverters, MPPTs, BMVs, EV chargers,
|  com.victronenergy.*     |   tank sensors, smart meters, third-party drivers
+--------------------------+
```

The VRM Portal does not talk to your inverter directly. It talks to the GX device, which talks to DBus. **Anything appearing on DBus with the right naming will appear in VRM.** That includes drivers you write yourself — VRM has no allowlist of approved devices.

## What VRM actually offers

From the [VRM Portal manual](https://www.victronenergy.com/media/pg/VRM_Portal_manual/en/introduction.html):

- **Live monitoring & schematic dashboard** (the screenshot) showing AC input, AC loads, battery state, PV charger output, EVCS, weather.
- **Advanced graphs** with widgets, time-range comparisons, CSV export.
- **Alerts/alarms** — automatic for inverters/batteries/MPPTs, plus user-defined parameter alerts (email + push).
- **Two-way control without Remote Console** — ESS settings, generator, relays, and **EV charging stations** (this is the hook the screenshot's `Manual / Charging / 12 A` controls use).
- **Remote Console** — full remote access to the GX GUI as if you were on the device.
- **Remote firmware updates** for VE.Bus inverters and the GX itself.
- **Reports** — energy summaries, ESS performance, generator runtime.
- **Dynamic ESS** — tariff-aware battery scheduling using forecast PV + load + grid prices.

## Connectivity

A GX device gets to VRM via Ethernet, WiFi, or 4G/LTE (GlobalLink 520 for very small installs). The user's Pi is on Ethernet/WiFi. Once on the network, the GX phones home and a token-based pairing links the install to the user's VRM account.

## Custom development surface

There are **four** ways to extend this stack. They differ by how invasive they are and where the code runs.

### 1. Custom DBus driver on Venus OS (this repo's choice)

Run a Python (or any language) process on the GX device that publishes a `com.victronenergy.<type>.<instance>` service. The GUI auto-detects it; VRM picks it up automatically. This is what `dbus-shelly-3em-smartmeter`, `dbus-serialbattery`, `dbus-solaredge`, and this repo all do.

Mechanics:

- Need root SSH on the GX device. On a Pi running Venus OS this means setting Access Level to *Superuser*, setting a root password, enabling SSH on LAN, and ideally installing your SSH public key to `~/.ssh/authorized_keys` (the `/data/` partition survives firmware updates; rootfs does not). See [Venus OS root access](https://www.victronenergy.com/live/ccgx:root_access).
- Place your code under `/data/<your-driver>/` so it survives firmware updates.
- Use the daemontools/runit pattern: a `service/run` shell script that `exec`s your process. Symlink that into `/service/<your-driver>/` to make `supervise` watch it. If the process exits, supervise restarts it.
- The symlink lives on rootfs, so it's wiped on every firmware update. Re-create it from `/data/rc.local` (which Venus OS runs at boot from `/data/`).
- Use `velib_python` (`/opt/victronenergy/dbus-systemcalc-py/ext/velib_python`) — it ships with Venus OS and provides `VeDbusService`, `VeDbusItemImport`, etc. (See `dbus-teslaapi-evcharger.py:43`.)
- Debug with `dbus-spy` — a built-in TUI tool that lists every service and lets you watch values change in real time. Essential when reverse-engineering the right paths.

This pattern is entirely officially documented at [How to add a driver to Venus](https://github.com/victronenergy/venus/wiki/howto-add-a-driver-to-Venus) and the parent [DBus paths reference](https://github.com/victronenergy/venus/wiki/dbus).

### 2. Local MQTT (`dbus-flashmq` / `dbus-mqtt`)

Venus OS ships an MQTT broker that mirrors the entire DBus tree to topics of the form:

```
N/<portalid>/<service>/<instance>/<path>     -- notification (publish)
R/<portalid>/<service>/<instance>/<path>     -- read request
W/<portalid>/<service>/<instance>/<path>     -- write request
```

Notifications stop after 60 s of silence; a client keeps them alive by publishing `R/<portalid>/system/0/Serial` with an empty payload (the keep-alive trick). After Venus OS 3.20 the broker is FlashMQ-based and supports `keepalive-options: ["suppress-republish"]` so keepalives don't re-fire the full topic dump. Home Assistant integrations and Node-RED flows almost always go through MQTT rather than DBus.

This driver does not use MQTT — it goes straight to DBus on-device. But MQTT is the right answer if you want to extend VRM behavior from a separate machine.

### 3. VRM JSON API v2 (cloud)

`https://vrmapi.victronenergy.com/v2/` exposes a REST API that returns installations, real-time stats, alarm history, widgets, and (for whitelisted scopes) some control endpoints. Authentication used to be bearer tokens that impersonate a user; the modern recommendation is to register a per-integration **access token** under a user account. Full reference: [vrm-api-docs.victronenergy.com](https://vrm-api-docs.victronenergy.com/). For commercial use Victron now runs a [Software Integrator Program](https://www.victronenergy.com/blog/2024/12/04/introducing-our-new-software-integrator-program/).

### 4. GuiMods / GUIv2 customization

The GX GUI is QML. Source for the older v1 GUI ships in `/opt/victronenergy/gui` and there's an active modding scene (kwindrem's GuiMods, etc.). GUIv2 is the React-based replacement (`venus-html5-app` repo) and uses MQTT-over-WebSockets to talk to the device. This is invasive — your patches are wiped on firmware update unless re-applied via `rc.local`.

## What the screenshot tells us about this user's install

From the dashboard tile:

- **Installation 397385** = *Photon Farm*, real-time updates, OK status.
- **AC Loads 1514 W** total, of which **EVCS 1368 W** is the Tesla — so the rest of the house is drawing about 146 W.
- **EVCS row** shows `Charging status: Charging`, `Vehicle: Guest EV`, `Mode: Manual`, `Charge current: 12.0 A`, `Total energy: 1 kWh`. Those fields all map directly to DBus paths this driver writes (see [03-evcs-dbus-contract.md](03-evcs-dbus-contract.md)).
- **PV Charger 2802 W** (288 V MPPT 1178 W + 290 V MPPT 1722 W) feeding two strings.
- **Battery 67.3 % / 27.14 V / 42.0 A** — a 24 V battery bank, charging at 1140 W.
- No AC Input (off-grid / disconnected), so all loads run from PV + battery via the inverter.

This is the context every line of the driver was tuned for — a 24 V off-grid system where PV surplus charges the car, and the user wants the car to show up in VRM the same way a real wallbox would.
