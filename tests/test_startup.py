from pathlib import Path
import plistlib
import tempfile
import unittest
from unittest.mock import patch

from ag_repatch import startup


class StartupTests(unittest.TestCase):
    def test_macos_login_registration_and_removal(self):
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(startup.sys, 'platform', 'darwin'), \
             patch.object(Path, 'home', return_value=Path(tmp)), \
             patch.object(startup, 'launch_command', return_value=['/Applications/Мой помощник.app/Contents/MacOS/ag-repatch']):
            startup.set_login_launch(True)
            path = Path(tmp) / 'Library/LaunchAgents/io.github.etosheartem.ag-repatch.plist'
            data = plistlib.loads(path.read_bytes())
            self.assertEqual(data['ProgramArguments'], ['/Applications/Мой помощник.app/Contents/MacOS/ag-repatch', '--background'])
            self.assertTrue(data['RunAtLoad'])
            startup.set_login_launch(False)
            self.assertFalse(path.exists())

    def test_frozen_launch_command_uses_executable(self):
        with patch.object(startup.sys, 'frozen', True, create=True), \
             patch.object(startup.sys, 'executable', '/app/ag-repatch.exe'):
            self.assertEqual(startup.launch_command(), ['/app/ag-repatch.exe'])

    def test_installed_package_uses_module(self):
        with patch.object(startup.sys, 'frozen', False, create=True), \
             patch.object(Path, 'exists', return_value=False):
            self.assertEqual(startup.launch_command()[1:], ['-m', 'ag_repatch'])
