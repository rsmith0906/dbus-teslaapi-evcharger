"""Offline regression tests: no Tesla credentials or vehicle commands."""
import argparse
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

try:
    import requests
except ImportError:
    # These tests mock the HTTP boundary; requests is installed on Venus OS.
    sys.modules['requests'] = types.SimpleNamespace(post=Mock())

spec = importlib.util.spec_from_file_location(
    'charging_rate', Path(__file__).resolve().parents[1] / 'change-tesla-charging-rate.py')
rate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rate)


class ChargingRateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        (self.directory / 'config.json').write_text(json.dumps(
            {'VIN': 'test-vehicle', 'CLIENT_ID': 'test-client'}))
        (self.directory / 'authtoken.txt').write_text(json.dumps({'refresh_token': 'old-refresh'}))
        (self.directory / 'token.txt').write_text('old-access')
        with patch.object(rate.shutil, 'which', return_value='/test/tesla-control'):
            self.controller = rate.ChargingController(self.directory)

    def test_rejects_out_of_range_and_non_integer_before_command(self):
        with patch.object(rate.subprocess, 'run') as command:
            for value in ['13', '-1', '3.5', 'bad']:
                with self.subTest(value=value), self.assertRaises(argparse.ArgumentTypeError):
                    self.controller.run(value)
            command.assert_not_called()

    def test_accepts_twelve_and_explicit_file_credentials(self):
        with patch.object(self.controller, 'token_expired', return_value=False), \
                patch.object(rate.subprocess, 'run', return_value=Mock(returncode=0)) as command:
            self.controller.run(12)
        args = command.call_args.args[0]
        self.assertEqual(args[-2:], ['charging-set-amps', '12'])
        self.assertIn('-ble=false', args)
        self.assertEqual(args[args.index('-key-file') + 1], str(self.directory / 'private.pem'))
        self.assertEqual(command.call_args.kwargs['timeout'], 20)

    def test_zero_remains_current_request_not_stop(self):
        with patch.object(self.controller, 'token_expired', return_value=False), \
                patch.object(rate.subprocess, 'run', return_value=Mock(returncode=0)) as command:
            self.controller.run(0)
        self.assertEqual(command.call_args.args[0][-2:], ['charging-set-amps', '0'])

    def test_check_only_sends_ping(self):
        with patch.object(self.controller, 'token_expired', return_value=False), \
                patch.object(rate.subprocess, 'run', return_value=Mock(returncode=0)) as command:
            self.controller.run(check=True)
        self.assertEqual(command.call_args.args[0][-1], 'ping')
        self.assertNotIn('charging-set-amps', command.call_args.args[0])

    def test_auth_failure_refreshes_once_then_retries(self):
        with patch.object(self.controller, 'token_expired', return_value=False), \
                patch.object(self.controller, 'refresh_token') as refresh, \
                patch.object(rate.logging, 'warning') as warning, \
                patch.object(rate.subprocess, 'run', side_effect=[
                    Mock(returncode=1, stderr='401 unauthorized'), Mock(returncode=0)]) as command:
            self.controller.run(5)
        refresh.assert_called_once()
        warning.assert_not_called()
        self.assertEqual(command.call_count, 2)

    def test_non_auth_failure_does_not_refresh(self):
        with patch.object(self.controller, 'token_expired', return_value=False), \
                patch.object(self.controller, 'refresh_token') as refresh, \
                patch.object(rate.subprocess, 'run', return_value=Mock(returncode=1, stderr='not_found')):
            with self.assertRaisesRegex(RuntimeError, 'not_found'):
                self.controller.run(5)
        refresh.assert_not_called()

    def test_bad_refresh_does_not_overwrite_existing_tokens(self):
        response = Mock()
        response.json.return_value = {'error': 'invalid_grant'}
        with patch.object(rate.requests, 'post', return_value=response):
            with self.assertRaises(RuntimeError):
                self.controller.refresh_token()
        self.assertEqual((self.directory / 'token.txt').read_text(), 'old-access')
        self.assertEqual(json.loads((self.directory / 'authtoken.txt').read_text()),
                         {'refresh_token': 'old-refresh'})

    def test_refresh_preserves_unrotated_refresh_token(self):
        response = Mock()
        response.json.return_value = {'access_token': 'new-access', 'expires_in': 3600}
        with patch.object(rate.requests, 'post', return_value=response) as post:
            self.controller.refresh_token()
        self.assertEqual((self.directory / 'token.txt').read_text(), 'new-access')
        self.assertEqual(json.loads((self.directory / 'authtoken.txt').read_text())['refresh_token'],
                         'old-refresh')
        self.assertEqual(post.call_args.kwargs['timeout'], (10, 20))

    def test_node_red_gets_failure_exit_code(self):
        lock_api = types.SimpleNamespace(LOCK_EX=2, LOCK_NB=4, flock=Mock())
        with patch.dict(sys.modules, {'fcntl': lock_api}), \
                patch.object(rate, 'DATA_DIR', self.directory), \
                patch.object(rate, 'ChargingController') as factory:
            factory.return_value.run.side_effect = RuntimeError('not_found')
            self.assertEqual(rate.main(['5']), 1)

    def test_timeout_returns_failure_without_retry(self):
        lock_api = types.SimpleNamespace(LOCK_EX=2, LOCK_NB=4, flock=Mock())
        with patch.dict(sys.modules, {'fcntl': lock_api}), \
                patch.object(rate, 'DATA_DIR', self.directory), \
                patch.object(rate, 'ChargingController') as factory:
            factory.return_value.run.side_effect = subprocess.TimeoutExpired('tesla-control', 20)
            self.assertEqual(rate.main(['5']), 1)
            factory.return_value.run.assert_called_once()

    def test_overlapping_request_returns_busy_without_command(self):
        lock_api = types.SimpleNamespace(LOCK_EX=2, LOCK_NB=4,
                                       flock=Mock(side_effect=BlockingIOError))
        with patch.dict(sys.modules, {'fcntl': lock_api}), \
                patch.object(rate, 'DATA_DIR', self.directory), \
                patch.object(rate, 'ChargingController') as factory:
            self.assertEqual(rate.main(['5']), 75)
            factory.assert_not_called()

    def test_missing_argument_exits_before_loading_credentials(self):
        with patch.object(rate, 'ChargingController') as factory:
            with self.assertRaises(SystemExit) as caught:
                rate.main([])
            self.assertEqual(caught.exception.code, 2)
            factory.assert_not_called()


if __name__ == '__main__':
    unittest.main()
