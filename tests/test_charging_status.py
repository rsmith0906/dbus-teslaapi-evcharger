"""Exercise the real status command handler without Tesla or filesystem I/O."""
from pathlib import Path
import subprocess
import sys
import unittest


HARNESS = r'''
import ast
import logging
import subprocess
import sys
from pathlib import Path

tree = ast.parse(Path(sys.argv[1]).read_text(encoding='utf-8-sig'))
service = next(node for node in tree.body if isinstance(node, ast.ClassDef))
# Import only the service class: module setup would read real credentials.
namespace = dict(subprocess=subprocess, logging=logging, status=sys.argv[2],
                 __file__=sys.argv[1])
import os
namespace['os'] = os
logging.FileHandler = lambda *args, **kwargs: logging.NullHandler()
logging.StreamHandler = lambda *args, **kwargs: logging.NullHandler()
exec(compile(ast.Module(body=[service], type_ignores=[]), '<status-handler>', 'exec'), namespace)
cls = namespace['DbusTeslaAPIService']
instance = cls.__new__(cls)
instance.get_token_is_expired = lambda: False
def command(args, **kwargs):
    expected = 'charging-start' if sys.argv[2] == '1' else 'charging-stop'
    assert args == ['tesla-control', expected], args
    if sys.argv[3] == 'fail':
        raise subprocess.CalledProcessError(1, args, stderr=b'not_found')
    return subprocess.CompletedProcess(args, 0)
subprocess.run = command
instance.run()
'''


class ChargingStatusTests(unittest.TestCase):
    def invoke(self, status, outcome):
        script = Path(__file__).resolve().parents[1] / 'change-tesla-charging-status.py'
        return subprocess.run([sys.executable, '-c', HARNESS, str(script), status, outcome],
                              capture_output=True, text=True, timeout=10)

    def test_failed_start_and_stop_exit_nonzero(self):
        for status in ['0', '1']:
            with self.subTest(status=status):
                result = self.invoke(status, 'fail')
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('Subprocess command failed: not_found', result.stderr)

    def test_successful_start_and_stop_exit_zero(self):
        for status in ['0', '1']:
            with self.subTest(status=status):
                result = self.invoke(status, 'success')
                self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
