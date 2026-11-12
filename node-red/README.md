# Tesla plug-in auto-start

`car-charging-auto-start.json` is the **Tesla Auto-Start Charging** tab (`262a0e697e4f1c1a`), exported
from the Victron Pi's `/data/home/nodered/.node-red/flows.json` on 2026-11-11. The Pi is the source
of truth. When the flow is changed there, export the tab again so this copy stays current.

The full rules, failure pauses and history are in
[docs/11-plug-detection-and-auto-start.md](../docs/11-plug-detection-and-auto-start.md). In short:

- Plug state comes from the Victron EV charger `/Status` (written by the driver), not the Tesla cache.
- Start at 5 A when battery is above 50%, or at least 20% with solar above 700 W.
- Checks at most every 5 minutes. After a start, waits 30 minutes before trying again.
- A failed start waits 10 or 30 minutes depending on the error, and logs it to
  `/data/tesla/auto-start-errors.log`.
- Unplugging (status 0) clears any pause.

The tab uses the existing Pushbullet configuration (`7c3e0c966c39354b`), which is not in this file.
The older **Car Charging** tab (`2ea0df98a5ef77d5`) is still on the Pi but disabled. Do not run both.

The start command runs `python /data/tesla/node-red-tesla.py start`, which needs that helper and
`change-tesla-charging-rate.py` in `/data/tesla/`.

## Hourly wake

`hourly-wake.json` is the **Tesla Hourly Wake** tab (`tesla_wake_tab`). Between 8 AM and 5 PM Central,
it wakes the car at most once an hour. It wakes only when all of these are true:

- The battery/solar start rules pass. They use the same constants as the start rules, and a test checks that.
- The car is home.
- The charger status is 0 or 2.
- The inverter, which powers only the car, is under 300 W.

It runs `python /data/tesla/node-red-tesla.py wake`. Failures are appended to
`/data/tesla/auto-start-errors.log`. Test: `node tests/test_node_red_wake.cjs`.

## Deploying a change

Back up `flows.json` first. Deploy through the Node-RED admin API (`POST http://127.0.0.1:1880/flows`
with `Node-RED-Deployment-Type: nodes`) or the editor. If an editor tab is open during an API deploy,
reload it instead of clicking Deploy.

Read-only helper check: `python /data/tesla/node-red-tesla.py read`

Test: `node tests/test_node_red_flow.cjs`
