#!/usr/bin/env python3
"""Set Tesla charging current from Node-RED; exit nonzero on failure.

Credentials are shared with the driver in /data/tesla. --check runs a signed
ping using the same credentials and executable without changing charge settings.
"""
import argparse
import json
import logging
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

import requests

DATA_DIR = Path('/data/tesla')
MAX_AMPS = 12
COMMAND_TIMEOUT = 20
AUTH_ERRORS = ('token', 'unauthorized', '401', 'authentication failed')


def parse_amps(value):
    try:
        amps = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError('amps must be a whole number')
    if not 0 <= amps <= MAX_AMPS:
        raise argparse.ArgumentTypeError('amps must be between 0 and 12')
    return amps


def atomic_write(path, content):
    """Publish a complete token file, preserving runtime ownership."""
    previous = path.stat() if path.exists() else None
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name, dir=str(path.parent))
    try:
        with os.fdopen(fd, 'w') as stream:
            stream.write(content)
        if previous and hasattr(os, 'chown'):
            os.chown(temporary, previous.st_uid, previous.st_gid)
        os.replace(temporary, str(path))
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class ChargingController:
    def __init__(self, data_dir=DATA_DIR):
        self.data_dir = Path(data_dir)
        with (self.data_dir / 'config.json').open() as stream:
            self.config = json.load(stream)
        self.env = os.environ.copy()
        # Node-RED's non-login shell does not normally include its Go bin.
        # Resolving it here avoids requiring a Go compiler on every invocation.
        self.env['PATH'] = os.pathsep.join([
            self.env.get('PATH', ''), str(Path.home() / 'go/bin'),
            '/usr/local/bin', '/usr/bin', '/bin', '/data/usr/local/go/bin',
        ])
        self.executable = shutil.which('tesla-control', path=self.env['PATH'])
        if not self.executable:
            raise RuntimeError('tesla-control not found in PATH or ~/go/bin')
        self.env['TESLA_VIN'] = self.config['VIN']
        self.env['TESLA_KEY_FILE'] = str(self.data_dir / 'private.pem')
        self.env['TESLA_TOKEN_FILE'] = str(self.data_dir / 'token.txt')

    def token_expired(self):
        token_path = self.data_dir / 'token.txt'
        if not token_path.exists() or not token_path.read_text().strip():
            return True
        expiry_path = self.data_dir / 'tokenexpire.txt'
        if not expiry_path.exists():
            return True
        expiration = time.mktime(time.strptime(
            expiry_path.read_text().strip(), '%Y-%m-%d %H:%M:%S'))
        return expiration <= time.time()

    def refresh_token(self):
        with (self.data_dir / 'authtoken.txt').open() as stream:
            previous = json.load(stream)
        response = requests.post(
            'https://auth.tesla.com/oauth2/v3/token',
            data={'grant_type': 'refresh_token',
                  'client_id': self.config['CLIENT_ID'],
                  'refresh_token': previous['refresh_token']},
            timeout=(10, 20))
        # Do not include the token response body in logs.
        response.raise_for_status()
        refreshed = response.json()
        access_token = refreshed.get('access_token')
        expires_in = int(refreshed.get('expires_in', 0))
        if not access_token or expires_in <= 0:
            raise RuntimeError('Tesla refresh response missing access token or valid expiry')
        refreshed['refresh_token'] = refreshed.get('refresh_token') or previous['refresh_token']
        expiration = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(
            time.time() + max(0, expires_in - 1000)))
        atomic_write(self.data_dir / 'authtoken.txt', json.dumps(refreshed))
        atomic_write(self.data_dir / 'token.txt', access_token)
        atomic_write(self.data_dir / 'tokenexpire.txt', expiration)

    def run(self, amps=None, check=False):
        if not check:
            amps = parse_amps(str(amps))
        self.execute(['ping'] if check else ['charging-set-amps', str(amps)])

    def execute(self, operation):
        """Send one signed operation, with at most one authentication retry."""
        if self.token_expired():
            self.refresh_token()
        command = [self.executable, '-ble=false',
                   '-key-file', self.env['TESLA_KEY_FILE'],
                   '-token-file', self.env['TESLA_TOKEN_FILE']]
        command += operation
        for attempt in range(2):
            result = subprocess.run(command, env=self.env, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, text=True,
                                    timeout=COMMAND_TIMEOUT)
            if result.returncode == 0:
                return
            error = result.stderr.strip() or 'tesla-control exited with code %s' % result.returncode
            if attempt == 0 and any(term in error.lower() for term in AUTH_ERRORS):
                # Node-RED routes stderr directly to its error notification.
                # A recovered authentication error must not emit a false alarm.
                self.refresh_token()
                continue
            raise RuntimeError(error)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('amps', type=parse_amps, nargs='?', help='current setting (0-12 A)')
    parser.add_argument('--check', action='store_true', help='signed ping; no charging change')
    args = parser.parse_args(argv)
    if (args.amps is None) == (not args.check):
        parser.error('provide amps OR --check')
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    try:
        # Exit 75 means temporarily busy. Do not queue stale Node-RED requests.
        import fcntl
        with (DATA_DIR / '.charging-rate.lock').open('a') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                logging.error('Another charging-rate command is running; retry later')
                return 75
            controller = ChargingController(DATA_DIR)
            controller.run(args.amps, check=args.check)
        print('Tesla signed ping succeeded' if args.check else
              'Charging current command accepted: %s A' % args.amps, flush=True)
        return 0
    except subprocess.TimeoutExpired:
        logging.error('Tesla command timed out after %s seconds; outcome unknown', COMMAND_TIMEOUT)
    except Exception as error:
        logging.error('Charging command failed: %s', error)
    return 1


if __name__ == '__main__':
    sys.exit(main())
