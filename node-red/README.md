# Tesla plug-in auto-start

Replace the existing **Car Charging** tab with `car-charging-auto-start.json`.
The replacement tab is named **Tesla Auto-Start Charging**.
Keep the existing Pushbullet configuration (`7c3e0c966c39354b`). Do not run the
old and replacement start flows together. The other tabs are unchanged.

The Pi needs both `node-red-tesla.py` and the updated
`change-tesla-charging-rate.py` in `/data/tesla/`. Run them as `nodered`, which
must have access to the shared Tesla credentials and write access to that folder.

## Start conditions

- House SOC **above 50%**, regardless of solar; OR
- House SOC **at least 20%** and solar **above 1,600 W**.
- Existing cached Tesla data must say `Stopped` with an `Engaged` charge-port latch.
- Cache file and Tesla charge-state timestamp, when supplied, must be no more
  than 15 minutes old.
- Set 5 A successfully before sending the charging-start command.

## API use and session handling

Local sensor messages can cause a local check at most every 5 minutes. These
checks read `/tmp/<VehicleId>.json`, written by the existing driver, and make
no Tesla data requests. Existing driver polling is unchanged. The start attempt
sends set-amps and start, with at most one authentication retry per operation.
There are no automatic wake requests or failure retry loops.

`/data/tesla/auto-start-state.json` records an attempt before commands run.
An observed `Charging` or `Complete` also marks the session handled. This avoids
restarting a completed charge or a charge manually stopped after it was observed
running. A fresh observed `Disconnected` state rearms auto-start. If unplugging
and reconnecting both happen between driver polls, that session change can be
missed; the flow conservatively does not issue another start. A manual stop
made before any charging/attempt is observed cannot be identified as manual.

The driver may take several minutes (or longer under its backoff rules) to
notice plugging in. Changing local input delivery does not increase driver
polling. Starting charging can still change the driver's existing polling cadence
from idle to charging. This feature does not guarantee a particular monthly bill.

On failure, the script returns nonzero and the flow routes the error to Debug.
It does not try again in that session. Only successful command output triggers
the existing Pushbullet success node. No-op checks are silent.

All start policy is in the **Start rules: >50% OR >=20% + >1600 W** function.
The Python helper only reads local files or executes set-5-A then start. The
flow saves session state using a File node and verifies it by reading it back
before dispatching a start. Invalid or unreadable session state blocks starts.
The former `auto-start-tesla-charging.py` is retained for compatibility with the
previous flow during migration; this replacement never calls it.

## Import

1. Disable the existing Car Charging / Tesla Auto-Start Charging start tab.
2. Import `car-charging-auto-start.json` using Menu > Import > select a file.
3. Replace matching existing nodes if offered; otherwise import as a new flow
   and keep the older start tab disabled. Do not enable both.
4. Confirm the Pushbullet node uses your existing account, then Deploy.
5. Keep Change Charging Level and Low Battery Inverter enabled as before.

The helper has been installed on the Pi. Importing and deploying the enabled
replacement activates automatic charging under the rules above.

Read-only helper check (no API requests or state changes):
`python /data/tesla/node-red-tesla.py read`

Tests: `python -m unittest discover -s tests -v` and
`node tests/test_node_red_flow.cjs`.
