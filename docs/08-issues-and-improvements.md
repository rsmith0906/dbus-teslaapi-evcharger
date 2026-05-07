# 08 — Bugs, smells, and improvement candidates

Findings from the audit. Severity is my judgment call about user-visible impact in a typical solar-EV setup.

## High-impact

### 1. `/Session/Time` and `/Session/Energy` not written → empty fields in GUI/VRM

**Symptom:** screenshot shows `Charging time -` and the Total energy reading looks stuck.
**Cause:** the gui-v2 EvCharger component reads `/Session/Time` and `/Session/Energy`, but this driver writes to `/ChargingTime` only, and never writes `/Ac/Energy/Forward` (line 387 is commented out). See [03b-custom-properties.md](03b-custom-properties.md).
**Fix:** also register `/Session/Time` and `/Session/Energy` and copy `delta.total_seconds()` and `charge_state.charge_energy_added` into them on every update.
**Files:** `dbus-teslaapi-evcharger.py:83–93, 387, 414`.

### 2. `/SetCurrent` is a no-op → "Charge current" slider in VRM does nothing

**Cause:** `_setcurrent` (line 260) literally just assigns `test = 0`. The on-change callback at line 489 doesn't even dispatch `/SetCurrent`.
**Fix:** wire `/SetCurrent` writes to `change-tesla-charging-rate.py` (or its inlined logic). Two approaches:
- Direct: in `_handlechangedvalue`, on `/SetCurrent` call `subprocess.run(['tesla-control', 'charging-set-amps', str(int(value))])`.
- Indirect: call the existing `change-tesla-charging-rate.py` so Pushbullet etc. is reused.
**Files:** `dbus-teslaapi-evcharger.py:260, 489`.

### 3. `_startstop` blocks the GLib loop for 10 s

**Cause:** `time.sleep(10)` after `tesla-control wake` (line 302). During this time, `_update` doesn't fire, no other writes are processed, GUI feels frozen.
**Fix:** instead of `time.sleep`, schedule the start/stop with `gobject.timeout_add(10000, lambda: self._send_charging_command(value))` and return immediately.
**Files:** `dbus-teslaapi-evcharger.py:302`.

### 4. `max_current <= 12` gate freezes all updates if car wants more amps

**Cause:** the entire `if max_current <= 12:` branch (line 382) is the only place metrics get written. If the car requests more than 12 A momentarily, the DBus values stop updating and the GUI shows stale data.
**Fix:** invert the logic — clamp `/MaxCurrent` to 12 if needed, but always update `/Current`, `/Ac/Power`, `/Status`.
**Files:** `dbus-teslaapi-evcharger.py:382`.

### 5. `/Position` overloaded as "high-current flag"

**Cause:** lines 408–411 set `/Position = 1` when current > 12 A. Spec says `/Position` is `0=AC Output, 1=AC Input, 2=AC Input 2` and is configuration, not state. The GUI may render this as the wallbox suddenly being on AC input.
**Fix:** leave `/Position` as the configured value from `config.ini`. Drop those four lines.
**Files:** `dbus-teslaapi-evcharger.py:408–411, 396, 424`.

### 6. `/Status = 10` is a hardware fault code, used as catch-all

**Cause:** lines 417, 455 set `/Status = 10` for "unknown / error". Per Victron's enum, 10 is "CP input test error (shorted)" — a *hardware* fault. The GUI may render this with a fault icon that misleads operators.
**Fix:** map unknown states to `/Status = 0` (Disconnected) or leave the previous value untouched.
**Files:** `dbus-teslaapi-evcharger.py:417, 455`.

## Medium-impact

### 7. Two parallel OAuth-refresh code paths with different scopes

**Cause:** `_getAccessToken` (line 221) uses legacy ownerapi scopes; `get_new_token` (line 516) uses Fleet-API scopes. The first uses `RefreshToken` from `config.ini`; the second from `authtoken.txt`. Confusing and easy to break.
**Fix:** unify on the Fleet-API flow. Drop `RefreshToken` from `config.ini`. Have `_getAccessToken` read `token.txt` if present, fall back to `get_new_token`.
**Files:** `dbus-teslaapi-evcharger.py:221, 516`.

### 8. Token-error retry only matches substring `'token'`

**Cause:** line 318 — `if 'token' in error_output.lower()`. `tesla-control` errors that should trigger a refresh ("authentication failed", "401 unauthorized", "session expired") may not contain the word "token".
**Fix:** match on broader patterns: `'unauthorized'`, `'expired'`, `'401'`, `'authentication'`, *or* check the exit code.
**Files:** `dbus-teslaapi-evcharger.py:318, 79`.

### 9. `is_time_between_midnight_and_8am()` actually checks 06:00–14:00

**Cause:** function name lies (line 606). Body uses `hour=6` and `hour=14`.
**Fix:** rename to `_is_in_offpeak_window()` or whatever the intent is. Make the bounds configurable via `config.ini`.
**Files:** `dbus-teslaapi-evcharger.py:606–615`.

### 10. `getInverterPower` reads `/tmp/Inverter.json` that nothing in this repo writes

**Cause:** orphan dependency on a file produced by an external process that isn't documented (line 573).
**Fix:** either document where `/tmp/Inverter.json` comes from in the README, or replace with a DBus read of `com.victronenergy.system /Ac/PvOnOutput/L1/Power` (or the equivalent) using `VeDbusItemImport`.
**Files:** `dbus-teslaapi-evcharger.py:573`.

### 11. Sidecar `_signOfLife` will crash with AttributeError

**Cause:** `TokenRefresh/tesla-api-token-refresh.py:62` references `self._lastUpdate` which is never initialized.
**Fix:** add `self._lastUpdate = 0` in `__init__`.
**Files:** `TokenRefresh/tesla-api-token-refresh.py:24, 62`.

### 12. Sidecar imports `gobject`/`DBusGMainLoop` but never uses DBus

**Cause:** copy-paste from the main driver.
**Fix:** simplify to `while True: refresh_if_due(); time.sleep(10)` or similar. Drops a dependency.
**Files:** `TokenRefresh/tesla-api-token-refresh.py`.

### 13. `change-tesla-charging-rate.py` will KeyError on `PushBulletKey`

**Cause:** line 46 reads `config['DEFAULT']['PushBulletKey']` but `config.ini` example does not include that key.
**Fix:** `config.ini` should document the key, or the script should `config.get('DEFAULT', 'PushBulletKey', fallback=None)` and skip the notification when missing.
**Files:** `change-tesla-charging-rate.py:46`.

### 14. `change-tesla-charging-rate.py` reads `config.json` from script dir, but main driver from `/data/tesla/`

**Cause:** path mismatch as documented in [07-companion-scripts.md](07-companion-scripts.md).
**Fix:** standardize on `/data/tesla/config.json`.
**Files:** `change-tesla-charging-rate.py:17, 20`.

## Low-impact / cosmetic

### 15. Every DBus path is `writeable=True`

**Cause:** line 124 — paths like `/Ac/Power`, `/Status`, `/Current` should be read-only.
**Fix:** add a `'writeable'` field to the path dict default to `False` for telemetry, `True` only for `/StartStop`, `/SetCurrent`, `/Mode`.

### 16. `/Mode` accepted but never honored

**Cause:** writes to `/Mode` are accepted in `_handlechangedvalue` (returns True) but the value is hardcoded to 0 on first run (line 343) and never revisited.
**Fix:** either implement Auto mode (= track inverter power and call charging-set-amps) or reject writes by returning `False` from `_handlechangedvalue`.

### 17. Config read on every `_update` tick (every 500 ms)

**Cause:** `_getConfig()` reads the file from disk each call. `_update`, `_signOfLifeInterval`, `_getTeslaAPIStatusUrl`, every helper does this.
**Fix:** read once in `__init__` and cache. Negligible cost on a Pi but unnecessary I/O.

### 18. `_lastCheck` is set but never read

**Cause:** line 69 — defined and never used. Looks like a vestige from the Shelly fork.
**Fix:** delete.

### 19. README is the Shelly fork's, not updated for Tesla

**Cause:** `README.md` still describes the Shelly 1PM, mentions `/data/dbus-teslaapi-evcharger` correctly but the rest is wrong.
**Fix:** rewrite the README using this docs/ folder as source. Ideally point users at `docs/`.

## Security observations (not bugs, just notes)

- `private.pem` and OAuth tokens live in `/data/tesla/` with default permissions. On a single-user Pi running Venus OS this is fine. On a shared-access install, restrict to `chmod 600 /data/tesla/*`.
- `config.json` includes `CLIENT_ID`. If this is a Tesla-issued OAuth client ID under your developer account, treat it as a secret.
- The driver logs full error tracebacks via `exc_info=e` (line 312, 458). Token-related errors may leak access-token values into `current.log`. Consider scrubbing.
- No TLS certificate pinning on `auth.tesla.com` or `owner-api.teslamotors.com` — the library default trust store is fine but worth noting if you're paranoid.
