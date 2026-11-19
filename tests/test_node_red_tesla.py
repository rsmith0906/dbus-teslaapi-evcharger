import importlib.util
from pathlib import Path
import unittest
from unittest.mock import Mock, call

spec = importlib.util.spec_from_file_location('node_red_tesla',
    Path(__file__).resolve().parents[1] / 'node-red-tesla.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

ASLEEP = RuntimeError('vehicle unavailable: vehicle is offline or asleep')


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class StartTests(unittest.TestCase):
    def start(self, controller, **kwargs):
        clock = FakeClock()
        module.start(controller, sleep=clock.sleep, clock=clock, **kwargs)
        return clock

    def test_wakes_then_sets_5_amps_then_starts(self):
        controller = Mock()
        self.start(controller)
        self.assertEqual(controller.mock_calls, [
            call.execute(['wake']), call.run(5), call.execute(['charging-start'])])

    def test_asleep_reply_to_wake_is_not_an_error(self):
        controller = Mock()
        controller.execute.side_effect = [ASLEEP, None]
        self.start(controller)
        controller.run.assert_called_once_with(5)

    def test_other_wake_error_fails(self):
        controller = Mock()
        controller.execute.side_effect = RuntimeError('token expired')
        with self.assertRaises(RuntimeError):
            self.start(controller)
        controller.run.assert_not_called()

    def test_retries_while_asleep_and_rewakes_once(self):
        controller = Mock()
        controller.run.side_effect = [ASLEEP] * 6 + [None]
        self.start(controller)
        self.assertEqual(controller.run.call_count, 7)
        self.assertEqual(controller.execute.call_args_list,
                         [call(['wake']), call(['wake']), call(['charging-start'])])

    def test_gives_up_after_wake_wait(self):
        controller = Mock()
        controller.run.side_effect = ASLEEP
        with self.assertRaises(RuntimeError):
            clock = FakeClock()
            module.start(controller, sleep=clock.sleep, clock=clock)
        self.assertGreaterEqual(clock.now, module.WAKE_WAIT_SECONDS)
        self.assertNotIn(call(['charging-start']), controller.execute.call_args_list)

    def test_failed_current_setting_never_starts(self):
        controller = Mock()
        controller.run.side_effect = RuntimeError('failed')
        with self.assertRaises(RuntimeError):
            self.start(controller)
        self.assertNotIn(call(['charging-start']), controller.execute.call_args_list)

    def test_low_charge_limit_raised_to_80_before_start(self):
        for limit, expected in [(60, True), (80, False), (90, False), (None, False)]:
            with self.subTest(limit=limit):
                controller = Mock()
                self.start(controller, charge_limit=limit)
                raised = call(['charging-set-limit', '80']) in controller.execute.call_args_list
                self.assertEqual(raised, expected)
                self.assertEqual(controller.execute.call_args_list[-1], call(['charging-start']))


class ReadChargeLimitTests(unittest.TestCase):
    """read_charge_limit() decides whether start raises the limit to 80%."""

    def read(self, vehicle_id='1492677889280637', cache=None):
        import json, tempfile
        from unittest.mock import patch
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        root = Path(folder.name)
        (root / 'config.ini').write_text(f'[DEFAULT]\nVehicleId = {vehicle_id}\n')
        if cache is not None:
            (root / f'{vehicle_id}.json').write_text(cache if isinstance(cache, str) else json.dumps(cache))
        with patch.object(module, 'DRIVER_CONFIG', root / 'config.ini'), patch.object(module, 'CACHE_DIR', root):
            return module.read_charge_limit()

    def test_returns_the_cached_limit(self):
        for limit in [60, 80, 90]:
            self.assertEqual(self.read(cache={'response': {'charge_state': {'charge_limit_soc': limit}}}), limit)

    def test_unknown_limit_is_none_so_start_leaves_it_alone(self):
        self.assertIsNone(self.read())  # no cache file, e.g. after a reboot
        self.assertIsNone(self.read(cache={'response': {'charge_state': {}}}))

    def test_non_numeric_vehicle_id_is_refused(self):
        for bad in ['../etc/passwd', '123abc', '']:
            with self.subTest(vehicle_id=bad), self.assertRaises(ValueError):
                self.read(vehicle_id=bad)

    def test_broken_cache_fails_loudly(self):
        with self.assertRaises(ValueError):
            self.read(cache='not json')
        with self.assertRaises(KeyError):
            self.read(cache={'response': {}})


class WakeOperationTests(unittest.TestCase):
    """`node-red-tesla.py wake`, used by the hourly wake flow."""

    def run_main(self, controller):
        import contextlib, io, sys, tempfile, types
        from unittest.mock import patch
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        rate = types.SimpleNamespace(ChargingController=Mock(return_value=controller))
        out, err = io.StringIO(), io.StringIO()
        fake_fcntl = types.SimpleNamespace(LOCK_EX=2, LOCK_NB=4, flock=Mock())
        with patch.dict(sys.modules, {'fcntl': fake_fcntl}), \
             patch.object(module, 'DATA_DIR', Path(folder.name)), \
             patch.object(module.importlib.util, 'spec_from_file_location', Mock()), \
             patch.object(module.importlib.util, 'module_from_spec', Mock(return_value=rate)), \
             contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = module.main(['wake'])
        fake_fcntl.flock.assert_called_once()  # same lock as start and manual amp changes
        return code, out.getvalue(), err.getvalue()

    def test_wake_only_wakes(self):
        controller = Mock()
        code, out, _ = self.run_main(controller)
        self.assertEqual(code, 0)
        self.assertIn('Wake sent', out)
        self.assertEqual(controller.mock_calls, [call.execute(['wake'])])

    def test_asleep_reply_still_counts_as_sent(self):
        controller = Mock()
        controller.execute.side_effect = ASLEEP
        code, out, _ = self.run_main(controller)
        self.assertEqual(code, 0)
        self.assertIn('Wake sent', out)

    def test_other_error_fails_with_message(self):
        controller = Mock()
        controller.execute.side_effect = RuntimeError('token expired')
        code, _, err = self.run_main(controller)
        self.assertEqual(code, 1)
        self.assertIn('Node-RED Tesla wake failed: token expired', err)
