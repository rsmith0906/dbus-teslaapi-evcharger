# 05 — Runtime walkthrough of `dbus-teslaapi-evcharger.py`

Reading the file top-down with annotations on what's actually going on at each layer.

## Module-level setup (lines 1–46)

```python
script_dir = '/data/tesla'                                           # NOT __file__'s dir
config_file_path = os.path.join(script_dir, 'config.json')           # config.json — NOT config.ini
authtoken_file_path  = '/data/tesla/authtoken.txt'                   # full token JSON (incl. refresh_token)
token_file_path      = '/data/tesla/token.txt'                       # just the access_token
token_expire_file_path = '/data/tesla/tokenexpire.txt'               # YYYY-MM-DD HH:MM:SS

os.environ['TESLA_VIN']        = config['VIN']
os.environ['TESLA_KEY_NAME']   = 'Tessy'
os.environ['TESLA_KEY_FILE']   = '/data/tesla/private.pem'
os.environ['TESLA_TOKEN_FILE'] = '/data/tesla/token.txt'

go_path_output = subprocess.check_output(['/data/usr/local/go/bin/go', 'env', 'GOPATH']).decode().strip()
os.environ['PATH'] += f':{go_path_output}/bin'
```

Key implication: **`config.json` is a separate file from `config.ini`**, lives at `/data/tesla/config.json`, and supplies at minimum:

```json
{ "VIN": "5YJ3...", "CLIENT_ID": "<oauth client id>" }
```

The `config.ini` next to the script supplies `Deviceinstance`, `CustomName`, `VehicleId`, `Phase`, `Position`. So there are two configs: this is intentional but undocumented in the README.

```python
sys.path.insert(1, '/opt/victronenergy/dbus-systemcalc-py/ext/velib_python')
from vedbus import VeDbusService
```

That's how Victron drivers find `velib_python`. It ships with Venus OS at this exact path.

## Constructor `DbusTeslaAPIService.__init__` (lines 48–100)

Steps:

1. Read `config.ini` for `Deviceinstance` and `CustomName`.
2. Construct `VeDbusService("com.victronenergy.evcharger.http_41")`.
3. Initialize state:
   - `_lastCheck`, `_lastCheckData` set to `2023-12-08` so first poll fires immediately.
   - `_wait_seconds = 30`, the throttle baseline.
   - `_cacheInverterPower = 0`, `_cacheChargingPower = -1` for change detection.
   - `_running`, `_firstRun` flags.
4. Call `add_standard_paths()` to register the EVCS DBus contract (see [03-evcs-dbus-contract.md](03-evcs-dbus-contract.md)).
5. Schedule two GLib timers:
   - `_update` every **500 ms** — but inside `_update` the actual API hit is gated by `_wait_seconds`. So the loop is fast, the network call is throttled.
   - `_signOfLife` every `SignOfLifeLog` *minutes* (configured 460 min ≈ 7.6 h).

## `add_standard_paths` (lines 102–124)

Bog-standard Victron driver boilerplate. Walks the `paths` dict and calls `dbusservice.add_path(...)` with `writeable=True` and `onchangecallback=self._handlechangedvalue`. That callback is what catches GUI/VRM-initiated writes to `/StartStop`.

Bug: every path is `writeable=True`, including read-only metrics like `/Ac/Power`. If something writes those, the on-change callback fires but the value isn't pushed back to the car. Mostly harmless but unclean.

## `_getTeslaAPISerial` and `_getTeslaAPIVersion` (lines 140–173)

Both call `read_data(car_id)` which loads `/tmp/{VehicleId}.json` — i.e., the **last cached vehicle_data response**. That's why the constructor doesn't make a network call: it reads VIN and `car_version` from disk if they're there, falling back to `0` otherwise. So if `/tmp/{id}.json` doesn't exist (first-ever boot, or after a reboot since `/tmp` is a tmpfs), `/Serial` and `/FirmwareVersion` register as `"0"` and only update once the first poll succeeds.

## `_getTeslaAPIData` (lines 180–219)

The read-API call. Key behavior:

```python
if checkSecs > self._wait_seconds:                # gate
    self._lastCheckData = datetime.now()
    response = requests.get(URL, headers=headers)
    response.raise_for_status()                   # raises on HTTP 4xx/5xx
    self._carData = response.json()
    self.save_data(car_id, json.dumps(self._carData))   # cache to /tmp
    return self._carData
else:
    return None
```

- The 30-second-default gate is enforced here, not in the timer.
- `save_data` writes to `/tmp/{VehicleId}.json` so other processes (e.g., the rate-change script) can read the cached data without a fresh API call.
- `raise_for_status()` is the source of the `_request_timeout_string` and `_too_many_requests` messages caught upstream — but the strings checked (`"Request Timeout"`, `"Too Many Requests"`) are the standard reason phrases for HTTP 408/429, so the heuristic works.

## `_getAccessToken` (lines 221–254)

Legacy ownerapi OAuth refresh, in-process. Note this path uses `RefreshToken` from `config.ini`, **not** from `authtoken.txt`. So this refresh path is inert if the user only edits `authtoken.txt`. It's a leftover from earlier versions and is the OAuth flow the *read* API expects — which is why it still uses `client_id=ownerapi`.

## `_startstop` (lines 269–328)

The only writable surface that does real work. Triggered when something writes to `/StartStop`:

```python
def _startstop(self, path, value):
    attempt = 0
    while attempt < 2:
        try:
            charge_state = self._carData['response']['charge_state']['charging_state']

            makeChange = True
            if charge_state == 'Charging' and value == 1: makeChange = False
            if charge_state != 'Charging' and value == 0: makeChange = False

            if makeChange:
                if self.get_token_is_expired():
                    self.get_new_token()

                subprocess.run(['tesla-control', 'wake'], check=True, stderr=PIPE)
                time.sleep(10)                                       # blocking sleep on the GLib loop

                if value == 1:
                    subprocess.run(['tesla-control', 'charging-start'], check=True, stderr=PIPE)
                else:
                    subprocess.run(['tesla-control', 'charging-stop'], check=True, stderr=PIPE)

            success = True
            break
        except subprocess.CalledProcessError as e:
            error_output = e.stderr.decode('utf-8')
            if 'token' in error_output.lower():
                self.get_new_token()
                attempt += 1
            else:
                raise
```

Three gotchas:

1. **`time.sleep(10)`** runs on the GLib main thread. For 10 s the entire driver is frozen — no DBus updates, no other writes processed. On a slower Pi this is a real responsiveness issue but works because the GUI's write is asynchronous and just expects an eventual `/UpdateIndex` bump.
2. The **idempotency guard** is correct (`if Charging and value==1: skip`) but only the most recent cache is consulted — if the user has stopped the car *outside* this driver, the cache is stale and the no-op is wrong. With the API throttle that gap can be 5 minutes.
3. **Token-error retry** only retries once (`max_attempts=2`) and only on substring `'token'` in stderr. `tesla-control` stderr varies — a typical token-expired error is `Error: failed to perform handshake: vehicle did not respond` which contains no "token" substring; the retry won't fire.

## `_update` — the heart of the loop (lines 330–464)

Runs every 500 ms. High-level flow:

```
0. Read config (every tick — wasteful but harmless)
1. Apply time-of-day throttle: 06:00–14:00 + idle => _wait_seconds = 600
2. Read inverter power from /tmp/Inverter.json (set by something else!)
3. If inverter power changed, force a fresh poll
4. Call _getTeslaAPIData() (gated by _wait_seconds)
5. If we got fresh data:
     a. If charging_state == 'NoPower' -> raise
     b. For phase L1:
        - if max_current <= 12: write metrics to DBus, set /Status
6. If car driving, push _wait_seconds to 1 hour
7. Catch exceptions, map to /Status and longer wait
8. Bump /UpdateIndex
```

### The `getInverterPower()` mystery

Line 573:

```python
def getInverterPower(self):
    inverter_data = self.read_data("Inverter")            # /tmp/Inverter.json
    if inverter_data:
        return Decimal(inverter_data['Power'])
    return Decimal(0.0)
```

Nothing in this repo writes `/tmp/Inverter.json`. So either:

- Another driver on the user's Pi (likely a custom one) writes the inverter power there, or
- It's wired up via Node-RED, or
- It's a vestige that always returns 0.

The only behavioral consequence in this driver is poll-cadence speedup when inverter power changes. If the file never appears, `getInverterPower()` returns 0 forever, the change detection is a no-op, and everything still works.

### The `max_current <= 12` gate

Line 382:

```python
if max_current <= 12:
    if int(charge_energy_added) == 0:
        ...
    self._dbusserviceev['/MaxCurrent'] = max_current
    if charge_state == 'Stopped' or charging_state == 'Complete':
        ...
    elif charge_state == 'Charging':
        ...
```

This is a hard upper bound: if the car is willing to take more than 12 A, the *entire* DBus update is skipped. That's because the user's installation can't deliver more than 12 A safely — the gate prevents the GUI from showing a misleading >12 A request that the wiring couldn't support. But the side effect is that if the car *actually pulls* 14 A momentarily, `/Current`, `/Ac/Power` etc. just stop updating until the rate request drops back ≤ 12. There's no warning logged.

The screenshot shows the system working in the normal regime (12.0 A request, 12.0 A actual).

### `/Position` overloading

Line 408:

```python
if (current > 12):
    self._dbusserviceev['/Position'] = 1   # spec: AC Input
else:
    self._dbusserviceev['/Position'] = 0   # spec: AC Output
```

`/Position` per the Victron spec is `0=AC Output, 1=AC Input, 2=AC Input 2` — it describes *where the wallbox is wired*, not a runtime quantity. This driver re-uses it as a "high current vs low current" flag, which the GUI may render as "this charger is on AC input" when the car is just drawing >12 A. Almost certainly unintended — see the issues doc.

## `_handlechangedvalue` (lines 489–495)

```python
def _handlechangedvalue(self, path, value):
    logging.info("someone else updated %s to %s" % (path, value))
    if path == '/StartStop':
        self._startstop(path, value)
    return True
```

Only `/StartStop` is wired through. `/SetCurrent` writes from the GUI are accepted (return `True`) and logged but no API call is made — that's why the "Charge current" slider in VRM appears to work but doesn't actually change the car.

## `main()` (lines 617–642)

Standard Victron driver entrypoint:

```python
DBusGMainLoop(set_as_default=True)
service = DbusTeslaAPIService()
mainloop = gobject.MainLoop()
mainloop.run()
```

The DBus main loop integrates with GLib so the timeouts scheduled in `__init__` actually fire. `mainloop.run()` blocks forever; daemontools/runit restarts the process if it exits.
