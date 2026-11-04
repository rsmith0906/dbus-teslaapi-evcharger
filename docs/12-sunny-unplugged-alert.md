# Sunny-and-unplugged alert

A Node-RED tab, **Sunny Unplugged Alert**, sends a Pushbullet message when the sun is strong and the car is at home but not plugged in. Added 2026-10-31. Source: [`node-red/sunny-unplugged-alert.json`](../node-red/sunny-unplugged-alert.json). Test: `node tests/test_node_red_alert.cjs`.

It makes **no Tesla API calls**. It reads the Victron readings and the cache file the driver already writes.

## When it alerts

The check runs every 5 minutes, and the first one comes 60 s after a deploy or restart. It alerts only when **all** of these are true:

| Rule | Source |
|------|--------|
| Victron EV charger `/Status` is 0 (Disconnected) | `com.victronenergy.evcharger/41` |
| Solar above **700 W** (same as auto-start) | `com.victronenergy.system/0` `/Dc/Pv/Power` |
| Between **9:00 AM and 3:59 PM US Central** | Pi clock is UTC; the function converts with `America/Chicago` |
| Car within `radius_m` of home | `drive_state.latitude/longitude` in `/tmp/<VehicleId>.json` vs `/data/tesla/home-location.json` |
| At least **30 minutes** since the last alert | node context `lastAlertAt` |

The message has the title "Car not plugged in" and the body "Solar is N W and the car is unplugged at home."

The rules node's status shows why it stayed quiet: "Plugged in", "Not sunny (N W)", "Outside 9 AM-4 PM Central", "Car away (N km)", or "Waiting 30 min since last alert". A missing or broken cache file or home file shows a **red** status and never alerts.

All the numbers are constants at the top of the **Sunny & unplugged & home?** function.

## Pieces

- **Driver change:** the data request asks for `location_data` too (`dbus-teslaapi-evcharger.py:262-263`). It is the same single request, so the poll count does not change. Not verified: whether Tesla bills a request with `location_data` differently. Check the developer dashboard. The token has the `vehicle_location` scope. Regression test: `tests/test_driver_sleep.py` `test_data_request_includes_location`.
- **Home location (Pi only, not in the repo):** `/data/tesla/home-location.json` holds `{"latitude":..,"longitude":..,"radius_m":200}`. It was saved from the car's own GPS reading while the car was parked at home on 2026-10-31. To move home or widen the radius, edit this file. There is no need to redeploy.
- **Pushbullet:** uses the existing "Main Account" config `7c3e0c966c39354b`. As of 2026-10-31 it has **no API key saved** (Node-RED log: "No credentials set for pushbullet config"), so no Pushbullet node on the Pi can send anything. To fix it, open any Pushbullet node in the Node-RED editor, edit "Main Account", paste the key from pushbullet.com → Settings → Access Token, then Deploy.

## Known limits

- **False "Connected":** the Victron status can wrongly say Connected while the car is unplugged and asleep. That bug is still open; see doc 11. When it happens, no alert fires.
- **Last known location:** while the car sleeps, its location is the last one the driver saw. That is correct both when it is asleep at home and when it is asleep away.
- **Reboot:** `/tmp` is wiped on reboot. The alert shows red until the driver's first poll writes the cache again.
- **Restarts:** restarting Node-RED forgets `lastAlertAt`, so one extra alert can come sooner than 30 minutes.

## Deploy and rollback

Deployed with the Node-RED admin API (`POST /flows`, `Node-RED-Deployment-Type: nodes`) by appending the tab's nodes. It is the same method as in doc 11.

Backups on the Pi:
- `/data/home/nodered/.node-red/flows.json.pre-sunny-alert-20261031-184027`
- `/data/dbus-teslaapi-evcharger/dbus-teslaapi-evcharger.py.pre-location-20261031-183825`

To remove the alert, delete the tab in the editor and Deploy. To roll back the driver, copy the backup over the file and run `svc -t /service/dbus-teslaapi-evcharger`.

### Crash during the first deploy (2026-10-31)

The first deploy had the EV charger input node without its `pathObj.enum` list. The Victron node reads that list for every value (`/usr/lib/node_modules/@victronenergy/node-red-contrib-victron/src/nodes/victron-nodes.js:191-193`), so Node-RED crashed and restarted twice. It was down about a minute. The fix:

1. Restored the backup.
2. Copied the full `pathObj` from the working auto-start node.
3. Added a check to the test that every enum input has its list.
4. Redeployed.

It has been stable since.
