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
        for state, latch, expected in [('Stopped','Engaged',1), ('Complete','Engaged',3),
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

    def test_data_request_includes_location(self):
        import tempfile
        with tempfile.TemporaryDirectory() as folder:
            token = Path(folder) / 'token.txt'
            token.write_text('token')
            response = Mock(json=Mock(return_value={'response': {}}))
            get = Mock(return_value=response)
            saved = namespace['requests']
            self.addCleanup(namespace.__setitem__, 'requests', saved)
            namespace.update(token_file_path=str(token), json=__import__('json'),
                             requests=types.SimpleNamespace(get=get, exceptions=saved.exceptions))
            d = self.driver(0)
            d._getTeslaAPIStatusUrl = lambda: 'url'
            d.get_token_is_expired = lambda: False
            d._lastCheckData = datetime(2000, 1, 1)
            d.save_data = Mock()
            d._getTeslaAPIData()
            endpoints = get.call_args.kwargs['params']['endpoints'].split(';')
            self.assertIn('location_data', endpoints)
            self.assertIn('charge_state', endpoints)

    def test_full_car_reports_charged_not_connected(self):
        for state, expected in [('Complete', 3), ('Stopped', 1)]:
            with self.subTest(state=state):
                d = self.driver(0)
                d._dbusserviceev.update({'/Ac/L1/Power': 0, '/Current': 0, '/Position': 0})
                d._dbusservicebatt = {}
                d.resetSavedChargeStart = Mock()
                d._getCarDriving = lambda: False
                d._getTeslaAPIData = lambda: {'response': {'charge_state': {
                    'charging_state': state, 'charge_port_latch': 'Engaged', 'battery_level': 59,
                    'charger_power': 0, 'charger_voltage': 0, 'charger_actual_current': 0,
                    'charge_energy_added': 1, 'charge_current_request_max': 12}}}
                self.assertTrue(d._update())
                self.assertEqual(d._dbusserviceev['/Status'], expected)
