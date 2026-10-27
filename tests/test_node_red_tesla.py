import importlib.util
from pathlib import Path
import unittest
from unittest.mock import Mock

spec = importlib.util.spec_from_file_location('node_red_tesla',
    Path(__file__).resolve().parents[1] / 'node-red-tesla.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class CommandTests(unittest.TestCase):
    def test_set_current_before_start(self):
        controller = Mock()
        module.start(controller)
        self.assertEqual(controller.mock_calls, [
            unittest.mock.call.run(5), unittest.mock.call.execute(['charging-start'])])

    def test_failed_current_setting_never_starts(self):
        controller = Mock()
        controller.run.side_effect = RuntimeError('failed')
        with self.assertRaises(RuntimeError):
            module.start(controller)
        controller.execute.assert_not_called()
