import importlib.util
from pathlib import Path
import unittest
import json
import tempfile
import types
import sys
from unittest.mock import Mock, patch

spec = importlib.util.spec_from_file_location(
    'auto_start', Path(__file__).resolve().parents[1] / 'auto-start-tesla-charging.py')
auto = importlib.util.module_from_spec(spec)
spec.loader.exec_module(auto)


class AutoStartTests(unittest.TestCase):
    def decision(self, soc=51, solar=0, status='Stopped', latch='Engaged', handled=False,
                 age=0, timestamp=None):
        charge = {'charging_state': status, 'charge_port_latch': latch}
        if timestamp is not None:
            charge['timestamp'] = timestamp
        return auto.decide(soc, solar, {'response': {'charge_state': charge}},
                           10000-age, {'handled': handled}, 10000)

    def test_battery_only_requires_above_fifty(self):
        self.assertEqual(self.decision(soc=51)[0], 'ready')
        self.assertNotEqual(self.decision(soc=50)[0], 'ready')

    def test_solar_assisted_boundaries(self):
        self.assertEqual(self.decision(soc=20, solar=1601)[0], 'ready')
        self.assertNotEqual(self.decision(soc=19, solar=2000)[0], 'ready')
        self.assertNotEqual(self.decision(soc=20, solar=1600)[0], 'ready')

    def test_finished_and_already_charging_are_handled(self):
        for status in ['Charging', 'Complete']:
            decision, state = self.decision(status=status)
            self.assertNotEqual(decision, 'ready')
            self.assertTrue(state['handled'])

    def test_no_repeated_start_or_restart_after_manual_stop(self):
        self.assertNotEqual(self.decision(handled=True)[0], 'ready')

    def test_only_observed_disconnect_rearms(self):
        self.assertFalse(self.decision(status='Disconnected', handled=True)[1]['handled'])
        self.assertTrue(self.decision(status='NoPower', handled=True)[1]['handled'])

    def test_stale_or_unlatched_vehicle_never_starts(self):
        self.assertNotEqual(self.decision(age=901)[0], 'ready')
        self.assertNotEqual(self.decision(latch='Disengaged')[0], 'ready')
        self.assertNotEqual(self.decision(timestamp=1000)[0], 'ready')

    def test_invalid_measurements_never_start(self):
        for soc, solar in [(float('nan'), 2000), (101, 2000), (51, -1)]:
            self.assertNotEqual(self.decision(soc=soc, solar=solar)[0], 'ready')

    def test_failed_attempt_is_persisted_and_not_retried(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / 'config.ini'
            config.write_text('[DEFAULT]\nVehicleId=123\n')
            (root / '123.json').write_text(json.dumps({'response': {'charge_state': {
                'charging_state': 'Stopped', 'charge_port_latch': 'Engaged'}}}))
            fake_rate = types.SimpleNamespace(ChargingController=Mock(),
                atomic_write=lambda path, text: path.write_text(text))
            fake_rate.ChargingController.return_value.run.side_effect = RuntimeError('command rejected')
            fake_spec = types.SimpleNamespace(loader=Mock())
            fake_lock = types.SimpleNamespace(flock=Mock(), LOCK_EX=2, LOCK_NB=4)
            with patch.dict(sys.modules, {'fcntl': fake_lock}), \
                    patch.object(auto, 'DATA_DIR', root), \
                    patch.object(auto, 'DRIVER_CONFIG', config), \
                    patch.object(auto, 'Path', side_effect=lambda p: root if str(p) == '/tmp' else Path(p)), \
                    patch.object(auto.importlib.util, 'spec_from_file_location', return_value=fake_spec), \
                    patch.object(auto.importlib.util, 'module_from_spec', return_value=fake_rate):
                self.assertEqual(auto.main(['51', '0']), 1)
                self.assertEqual(auto.main(['51', '0']), 0)
                fake_rate.ChargingController.return_value.run.assert_called_once_with(5)
                fake_rate.ChargingController.return_value.execute.assert_not_called()
            self.assertTrue(json.loads((root / 'auto-start-state.json').read_text())['handled'])


if __name__ == '__main__':
    unittest.main()
