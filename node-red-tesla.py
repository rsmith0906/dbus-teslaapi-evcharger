#!/usr/bin/env python3
"""Local file I/O and explicit commands for Node-RED; no charging policy."""
import argparse
import configparser
import importlib.util
import json
from pathlib import Path
import sys

DATA_DIR = Path('/data/tesla')
DRIVER_CONFIG = Path('/data/dbus-teslaapi-evcharger/config.ini')


def read_inputs():
    cfg = configparser.ConfigParser()
    cfg.read(str(DRIVER_CONFIG))
    vehicle_id = cfg['DEFAULT']['VehicleId']
    if not vehicle_id.isdigit():
        raise ValueError('VehicleId must be numeric')
    cache_path = Path('/tmp') / (vehicle_id + '.json')
    with cache_path.open() as cache_file:
        import os
        mtime = os.fstat(cache_file.fileno()).st_mtime
        cache = json.load(cache_file)
    state_path = DATA_DIR / 'auto-start-state.json'
    state = json.loads(state_path.read_text()) if state_path.exists() else None
    return {'charge': cache['response']['charge_state'], 'cacheMtime': mtime,
            'session': state}


def start(controller):
    controller.run(5)
    controller.execute(['charging-start'])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=['read', 'start'])
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
            start(rate.ChargingController(DATA_DIR))
        print('Auto-start command succeeded at 5 A', flush=True)
        return 0
    except Exception as error:
        print('Node-RED Tesla %s failed: %s' % (args.operation, error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
