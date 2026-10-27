#!/usr/bin/env python3
"""Auto-start from local Tesla cache and Node-RED house SOC/solar readings.

No vehicle-data polls or wake requests. One attempt per observed plug-in session,
persisted across Node-RED restarts. --check reports the decision without writes
or Tesla commands. Start conditions: SOC >50, or SOC >=20 and PV >1600 W.
"""
import argparse
import configparser
import importlib.util
import json
import math
from pathlib import Path
import sys
import time

DATA_DIR = Path('/data/tesla')
DRIVER_CONFIG = Path('/data/dbus-teslaapi-evcharger/config.ini')
MAX_CACHE_AGE = 15 * 60


def decide(soc, solar, cache, cache_mtime, state, now):
    """Return decision and new session state, without I/O."""
    if not all(math.isfinite(x) for x in (soc, solar)) or not 0 <= soc <= 100 or solar < 0:
        return 'invalid local readings', state
    charge = cache.get('response', {}).get('charge_state', {})
    timestamp = charge.get('timestamp')
    observed = min(cache_mtime, float(timestamp) / 1000) if timestamp is not None else cache_mtime
    if not 0 <= now - observed <= MAX_CACHE_AGE:
        return 'waiting for fresh Tesla cache', state
    charging = charge.get('charging_state')
    if charging == 'Disconnected':
        return 'unplugged', {'handled': False}
    if charging in ('Charging', 'Complete'):
        return charging.lower(), {'handled': True}
    if state.get('handled', False):
        return 'already handled this plug-in session', state
    if charging != 'Stopped' or charge.get('charge_port_latch') != 'Engaged':
        return 'not ready to charge', state
    if not (soc > 50 or (soc >= 20 and solar > 1600)):
        return 'waiting for battery or solar', state
    return 'ready', state


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('soc', type=float, help='house battery SOC percent')
    parser.add_argument('solar', type=float, help='local solar power in watts')
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args(argv)
    try:
        import fcntl
        spec = importlib.util.spec_from_file_location(
            'charging_rate', Path(__file__).with_name('change-tesla-charging-rate.py'))
        rate = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(rate)
        cfg = configparser.ConfigParser()
        cfg.read(str(DRIVER_CONFIG))
        vehicle_id = cfg['DEFAULT']['VehicleId']
        if not vehicle_id.isdigit():
            raise ValueError('VehicleId must be numeric')
        cache_path = Path('/tmp') / (vehicle_id + '.json')
        state_path = DATA_DIR / 'auto-start-state.json'
        # Share the amperage script's lock so set-amps and start stay together.
        with (DATA_DIR / '.charging-rate.lock').open('a') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return 0  # A later local update will reevaluate; no API call.
            state = json.loads(state_path.read_text()) if state_path.exists() else {'handled': False}
            try:
                cache = json.loads(cache_path.read_text())
            except (FileNotFoundError, json.JSONDecodeError):
                if args.check:
                    print('waiting for Tesla cache')
                return 0  # Driver may be in the middle of updating its cache.
            decision, next_state = decide(args.soc, args.solar, cache, cache_path.stat().st_mtime,
                                          state, time.time())
            if args.check:
                print(decision)
                return 0
            if decision != 'ready':
                if next_state != state:
                    rate.atomic_write(state_path, json.dumps(next_state))
                return 0
            # Persist BEFORE any commands: failure or process interruption must
            # not create a paid retry loop on every local sensor update.
            rate.atomic_write(state_path, json.dumps({'handled': True, 'attempted_at': time.time()}))
            controller = rate.ChargingController(DATA_DIR)
            controller.run(5)
            controller.execute(['charging-start'])
            print('Auto-start command succeeded at 5 A', flush=True)
            return 0
    except Exception as error:
        print('Auto-start failed: %s. No automatic retry this plug-in session.' % error,
              file=sys.stderr, flush=True)
        return 1


if __name__ == '__main__':
    sys.exit(main())
