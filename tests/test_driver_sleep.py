"""Run driver methods offline, without importing hardware or reading credentials."""
import ast
from datetime import datetime
import logging
from pathlib import Path
import time
import types
import unittest
from unittest.mock import Mock

source = Path(__file__).resolve().parents[1] / 'dbus-teslaapi-evcharger.py'
tree = ast.parse(source.read_text())
service = next(n for n in tree.body if isinstance(n, ast.ClassDef))
namespace = dict(datetime=datetime, time=time, logging=logging,
                 requests=types.SimpleNamespace(exceptions=types.SimpleNamespace(Timeout=TimeoutError)))
exec(compile(ast.Module(body=[service], type_ignores=[]), str(source), 'exec'), namespace)
Driver = namespace['DbusTeslaAPIService']


class DriverSleepTests(unittest.TestCase):
    def driver(self, status):
        d = Driver.__new__(Driver)
        d._dbusserviceev = {'/Status': status, '/Ac/Power': 0}
        d._getConfig = lambda: {'DEFAULT': {'Phase': 'L1', 'VehicleId': '123'}}
        d.is_time_between_midnight_and_8am = lambda: False
        d.getInverterPower = lambda: 0
        d._firstRun = True
        d._cacheInverterPower = 0
        d._request_timeout_string = 'Request Timeout'
        d._too_many_requests = 'Too Many Requests'
        d._wait_seconds = 300
        d._showInfoMessage = Mock()
        d._signalChanges = Mock()
        d.get_new_token = Mock()
        return d

    def test_sleep_preserves_last_status(self):
        for status in [0, 1, 2]:
            for error in [RuntimeError('408 Client Error: Request Timeout'), TimeoutError('read timed out')]:
                with self.subTest(status=status, error=str(error)):
                    d = self.driver(status)
                    d._getTeslaAPIData = Mock(side_effect=error)
                    self.assertTrue(d._update())
                    self.assertEqual(d._dbusserviceev['/Status'], status)
                    self.assertEqual(d._wait_seconds, 300)
                    d.get_new_token.assert_not_called()

    def test_restore_known_status_after_restart(self):
        for state, latch, expected in [('Stopped','Engaged',1), ('Complete','Engaged',1),
                                       ('Charging','Engaged',2), ('Disconnected','Disengaged',0),
                                       ('Stopped','Disengaged',0)]:
            d = self.driver(0)
            d.read_data = lambda _: {'response': {'charge_state': {
                'charging_state': state, 'charge_port_latch': latch}}}
            d._restore_cached_connection_status()
            self.assertEqual(d._dbusserviceev['/Status'], expected)

    def test_missing_or_incomplete_cache_does_not_invent_connection(self):
        for cache in [None, {}, {'response': {'charge_state': {'charging_state': 'Stopped'}}}]:
            d = self.driver(0)
            d.read_data = lambda _: cache
            d._restore_cached_connection_status()
            self.assertEqual(d._dbusserviceev['/Status'], 0)
