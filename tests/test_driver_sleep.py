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
        d._homeWakeDueAt = None
        d._sawDriving = False
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


HOME = {'latitude': 30.0, 'longitude': -97.0, 'radius_m': 200}


def car(state='Disconnected', max_current=12, speed=None, shift=None, lat=30.0, lon=-97.0):
    return {'response': {
        'charge_state': {'charging_state': state, 'charge_port_latch': 'Engaged', 'battery_level': 50,
                         'charger_power': 0, 'charger_voltage': 0, 'charger_actual_current': 0,
                         'charge_energy_added': 1, 'charge_current_request_max': max_current},
        'drive_state': {'speed': speed, 'shift_state': shift, 'latitude': lat, 'longitude': lon}}}


class UnplugAndHomeWakeTests(unittest.TestCase):
    def setUp(self):
        import json, math, tempfile
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        home = Path(folder.name) / 'home-location.json'
        home.write_text(json.dumps(HOME))
        self.run_cmd = Mock()
        keys = ['json', 'math', 'subprocess', 'home_location_file_path']
        saved = {k: namespace.get(k) for k in keys}
        self.addCleanup(namespace.update, saved)
        namespace.update(json=json, math=math, home_location_file_path=str(home),
                         subprocess=types.SimpleNamespace(run=self.run_cmd, PIPE=-1))

    def driver(self, status):
        d = DriverSleepTests.driver(self, status)
        d._dbusserviceev.update({'/Ac/L1/Power': 0, '/Current': 0, '/Position': 0, '/ChargingTime': 0})
        d._dbusservicebatt = {}
        d.resetSavedChargeStart = Mock()
        d._poll_idle = 300
        d._home_wake_delay = 900
        d._sawDriving = False
        d._homeWakeDueAt = None
        d.get_token_is_expired = lambda: False
        return d

    def update_with(self, d, data):
        d._getTeslaAPIData = lambda: data
        d._carData = data
        self.assertTrue(d._update())

    def test_disconnected_clears_status_even_after_charging_at_high_current(self):
        # 2026-11-11: max current 48 A after charging away left Status stuck at 2.
        for max_current in [12, 48]:
            with self.subTest(max_current=max_current):
                d = self.driver(2)
                self.update_with(d, car('Disconnected', max_current, speed=77, shift='D'))
                self.assertEqual(d._dbusserviceev['/Status'], 0)

    def test_driving_polls_every_5_minutes(self):
        d = self.driver(0)
        self.update_with(d, car(speed=40, shift='D'))
        self.assertEqual(d._wait_seconds, 300)

    def test_parking_at_home_unplugged_schedules_one_wake(self):
        d = self.driver(0)
        self.update_with(d, car(speed=40, shift='D', lat=31.0))
        self.assertIsNone(d._homeWakeDueAt)
        before = time.time()
        self.update_with(d, car())
        self.assertGreaterEqual(d._homeWakeDueAt, before + 900)
        # A later parked reading does not schedule another.
        d._homeWakeDueAt = None
        self.update_with(d, car())
        self.assertIsNone(d._homeWakeDueAt)

    def test_no_wake_when_parked_away_or_already_plugged_in(self):
        for data in [car(lat=30.01), car('Stopped')]:  # ~1.1 km away; plugged in at home
            with self.subTest(state=data['response']['charge_state']['charging_state']):
                d = self.driver(0)
                self.update_with(d, car(speed=40, shift='D'))
                self.update_with(d, data)
                self.assertIsNone(d._homeWakeDueAt)

    def test_due_wake_runs_once_and_rereads_soon(self):
        d = self.driver(0)
        d._homeWakeDueAt = time.time() - 1
        d._lastCheckData = datetime(2000, 1, 1)
        d._maybeWakeAfterArrivingHome()
        self.run_cmd.assert_called_once()
        self.assertEqual(self.run_cmd.call_args.args[0], ['tesla-control', 'wake'])
        self.assertEqual(d._wait_seconds, 60)
        self.assertIsNone(d._homeWakeDueAt)
        d._maybeWakeAfterArrivingHome()
        self.run_cmd.assert_called_once()

    def test_no_wake_before_due_or_once_plugged_in(self):
        for status, due in [(0, time.time() + 60), (1, time.time() - 1)]:
            with self.subTest(status=status):
                d = self.driver(status)
                d._homeWakeDueAt = due
                d._maybeWakeAfterArrivingHome()
                self.run_cmd.assert_not_called()

    def test_failed_wake_is_logged_and_does_not_break_update(self):
        self.run_cmd.side_effect = RuntimeError('vehicle is offline or asleep')
        d = self.driver(0)
        d._homeWakeDueAt = time.time() - 1
        with self.assertLogs(level='ERROR'):
            d._maybeWakeAfterArrivingHome()
        self.assertEqual(d._wait_seconds, 60)
