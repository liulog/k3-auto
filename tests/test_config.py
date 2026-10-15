"""Local config regression: no network, hardware, builds or external tools."""
import contextlib
import importlib.util
import io
import os
from pathlib import Path
import tempfile
import tomllib
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]

def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result

k3 = module('k3ctl', ROOT / 'scripts/k3ctl.py')
migration = module('migration', ROOT / 'scripts/migrate-config.py')
BASE = '[board]\nhost="board.example"\nuser="board-user"\n[lab]\nlog_dir="logs"\n'
JUMP = '[jump]\nhost="jump.example"\nuser="jump-user"\npassword="TEST_ONLY_JUMP"\nsudo_password="TEST_ONLY_SUDO"\n[board]\npassword="TEST_ONLY_BOARD"\n'

class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.base = self.root / 'k3-auto.toml'
        self.jump = self.root / 'jump.toml'
        self.local = self.root / 'k3-auto.local.toml'
        self.base.write_text(BASE)
        self.jump.write_text(JUMP)
        self.env = patch.dict(os.environ, {}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.no_remote = patch.object(k3.subprocess, 'run', side_effect=AssertionError('remote process forbidden'))
        self.no_remote.start()
        self.addCleanup(self.no_remote.stop)

    def migrate(self):
        migration.migrate(self.base, self.jump, self.local)
        return k3.load_config(str(self.local))

    def test_merge_matches_legacy(self):
        old = k3.Config(k3.load_toml(self.base), self.base, k3.load_toml(self.jump), self.jump)
        new = self.migrate()
        for key, value in vars(old).items():
            if key not in ('path', 'jump_path'):
                self.assertEqual(value, getattr(new, key), key)

    def test_private_permissions_and_legacy_preserved(self):
        before = (self.base.read_bytes(), self.jump.read_bytes())
        self.migrate()
        self.assertEqual(self.local.stat().st_mode & 0o777, 0o600)
        self.assertEqual(before, (self.base.read_bytes(), self.jump.read_bytes()))

    def test_no_overwrite(self):
        self.migrate()
        with self.assertRaises(ValueError):
            self.migrate()

    def test_single_file_not_overridden_by_legacy_or_env(self):
        self.migrate()
        self.jump.write_text(JUMP.replace('jump.example', 'wrong.example'))
        os.environ['K3_JUMP_CONFIG'] = str(self.jump)
        cfg = k3.load_config(str(self.local))
        self.assertEqual(cfg.jump_host, 'jump.example')
        self.assertIsNone(cfg.jump_path)

    def test_explicit_overlay_still_works(self):
        self.migrate()
        self.jump.write_text(JUMP.replace('jump.example', 'other.example'))
        self.assertEqual(k3.load_config(str(self.local), str(self.jump)).jump_host, 'other.example')

    def test_legacy_sibling_config(self):
        self.assertEqual(k3.load_config(str(self.base)).board_password, 'TEST_ONLY_BOARD')

    def test_missing_explicit_path_fails(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            k3.load_config(str(self.root / 'absent'))

    def test_default_local_preferred(self):
        directory = self.root / 'config'
        directory.mkdir()
        (directory / 'k3-auto.toml').write_text(BASE)
        (directory / 'k3-auto.local.toml').write_text(BASE)
        with patch.object(k3.Path, 'cwd', return_value=self.root):
            self.assertEqual(k3.resolve_config(None), directory / 'k3-auto.local.toml')

    def test_environment_and_explicit_precedence(self):
        os.environ['K3_AUTO_CONFIG'] = str(self.local)
        self.assertEqual(k3.resolve_config(None), self.local)
        self.assertEqual(k3.resolve_config(str(self.base)), self.base)

    def test_masked_output_has_no_passwords(self):
        text = self.migrate().masked()
        for secret in ('TEST_ONLY_JUMP', 'TEST_ONLY_SUDO', 'TEST_ONLY_BOARD'):
            self.assertNotIn(secret, text)

    def test_malformed_toml_does_not_echo_secrets(self):
        self.local.write_text('password = "TEST_ONLY_SECRET" bad syntax')
        error = io.StringIO()
        with contextlib.redirect_stderr(error), self.assertRaises(SystemExit):
            k3.load_config(str(self.local))
        self.assertNotIn('TEST_ONLY_SECRET', error.getvalue())

    def test_missing_board_identity_not_guessed(self):
        data = tomllib.loads(JUMP)
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            k3.Config(data, self.local)

    def test_migration_roundtrips_quoted_password(self):
        self.jump.write_text(JUMP.replace('TEST_ONLY_BOARD', "a'b$()\\\"xyz"))
        cfg = self.migrate()
        self.assertEqual(cfg.board_password, tomllib.loads(self.jump.read_text())['board']['password'])

    def test_migration_does_not_follow_destination_symlink(self):
        self.local.symlink_to(self.base)
        with self.assertRaises(ValueError):
            self.migrate()

if __name__ == '__main__':
    unittest.main()
