#!/usr/bin/env python3
"""Local file I/O and explicit commands for Node-RED; no charging policy."""
import argparse
import configparser
import importlib.util
import json
from pathlib import Path
import sys
import time

DATA_DIR = Path('/data/tesla')
DRIVER_CONFIG = Path('/data/dbus-teslaapi-evcharger/config.ini')
MIN_CHARGE_LIMIT = 80


def read_charge_limit():
    # Last limit the driver saw, from its cache file. None if there is no cache yet
    # (after a reboot). Unknown must not change the limit: it could lower a higher one.
    cfg = configparser.ConfigParser()
    cfg.read(str(DRIVER_CONFIG))
    try:
        cache = json.loads((Path('/tmp') / (cfg['DEFAULT']['VehicleId'] + '.json')).read_text())
    except FileNotFoundError:
        return None
    return cache['response']['charge_state'].get('charge_limit_soc')


def read_inputs():
    # Only the saved plug-in session. Plug state comes from Victron's EV charger
    # status in Node-RED, so a sleeping car (stale Tesla cache) cannot block a start.
    state_path = DATA_DIR / 'auto-start-state.json'
    state = json.loads(state_path.read_text()) if state_path.exists() else None
    return {'session': state, 'charge_limit': read_charge_limit()}


ASLEEP_ERROR = 'offline or asleep'
WAKE_WAIT_SECONDS = 120     # Node-RED exec timer is 180 s
RETRY_INTERVAL_SECONDS = 10
REWAKE_AFTER_SECONDS = 45


def is_asleep_error(error):
    return ASLEEP_ERROR in str(error).lower()


def wake(controller):
    # The installed tesla-control returns from "wake" as soon as Tesla accepts the
    # request, while the car is still asleep (pkg/connector/inet/inet.go:219-227
    # treats a 200 "asleep" reply as success). An asleep error here is not fatal.
    try:
        controller.execute(['wake'])
    except RuntimeError as error:
        if not is_asleep_error(error):
            raise


def start(controller, charge_limit=None, sleep=time.sleep, clock=time.monotonic):
    # Wake first: the car is usually asleep when Victron status alone triggers a start.
    # Then retry the first real command until the car is online, re-sending the wake
    # once, so a slow wake-up does not fail the whole start.
    wake(controller)
    began = clock()
    rewoken = False
    while True:
        try:
            controller.run(5)
            break
        except RuntimeError as error:
            elapsed = clock() - began
            if not is_asleep_error(error) or elapsed >= WAKE_WAIT_SECONDS:
                raise
            if not rewoken and elapsed >= REWAKE_AFTER_SECONDS:
                wake(controller)
                rewoken = True
            sleep(RETRY_INTERVAL_SECONDS)
    if charge_limit is not None and charge_limit < MIN_CHARGE_LIMIT:
        controller.execute(['charging-set-limit', str(MIN_CHARGE_LIMIT)])
    controller.execute(['charging-start'])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=['read', 'start', 'wake'])
    args = parser.parse_args(argv)
    try:
        if args.operation == 'read':
            print(json.dumps(read_inputs(), allow_nan=False))
            return 0
        import fcntl
        spec = importlib.util.spec_from_file_location(
            'charging_rate', Path(__file__).with_name('change-tesla-charging-rate.py'))
        rate = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(rate)
        with (DATA_DIR / '.charging-rate.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            controller = rate.ChargingController(DATA_DIR)
            if args.operation == 'wake':
                # Hourly wake flow: only wakes, so the driver's next poll sees the
                # car's real plug state. The auto-start flow does the starting.
                wake(controller)
                print('Wake sent', flush=True)
                return 0
            start(controller, charge_limit=read_charge_limit())
        print('Auto-start command succeeded at 5 A', flush=True)
        return 0
    except Exception as error:
        print('Node-RED Tesla %s failed: %s' % (args.operation, error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
