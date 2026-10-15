"""Sudo failure reproduction and credential transport regression. No network."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module
m=load('lease_sudo',ROOT/'scripts/lease_power.py')
ctl=load('ctl_sudo',ROOT/'scripts/k3ctl.py')
SECRET="TEST_ONLY_'sudo_secret"

def result(code=0,out='',err=''):
    return subprocess.CompletedProcess([],code,out,err)

class Tests(unittest.TestCase):
    def hardware(self,password=SECRET):
        return m.Hardware({'sudoPassword':password,'serials':['/dev/debug','/dev/relay'],
                           'powerScript':'/test/power.sh','helper':'/test/helper.py',
                           'boardTarget':'user@board.example','expectedModel':'com260'})
    def test_original_password_required_is_explicit_and_does_not_claim(self):
        with tempfile.TemporaryDirectory() as t,patch.object(m.subprocess,'run',return_value=result(1,err='sudo: a password is required\n')) as run:
            path=Path(t)/'lease'; c=m.Controller(path,self.hardware(''))
            with self.assertRaises(m.Refused) as error:c.run('acquire','mine')
            self.assertEqual(error.exception.code,'SUDO_PASSWORD_REQUIRED')
            self.assertEqual(error.exception.stage,'jump.preflight')
            self.assertFalse(path.exists()); self.assertEqual(run.call_count,1)
    def test_configured_password_is_stdin_only(self):
        with patch.object(m.subprocess,'run',return_value=result(out='{"rootVisible":true}')) as run:
            self.assertTrue(self.hardware().host()['rootVisible'])
            args,kwargs=run.call_args
            self.assertEqual(args[0][:4],['sudo','-S','-p',''])
            self.assertEqual(kwargs['input'],SECRET+'\n')
            self.assertNotIn(SECRET,str(args));self.assertNotIn(SECRET,str(kwargs['env']))
            self.assertEqual(kwargs['env']['LC_ALL'],'C')
    def test_nopasswd_does_not_wait_for_input(self):
        with patch.object(m.subprocess,'run',return_value=result(out='{}')) as run:
            self.hardware('').host()
            self.assertEqual(run.call_args.args[0][:2],['sudo','-n'])
            self.assertEqual(run.call_args.kwargs['input'],'')
    def test_error_categories_and_no_raw_output(self):
        for message,code in [('sudo: a password is required','SUDO_PASSWORD_REQUIRED'),
                             ('sudo: interactive authentication is required','SUDO_PASSWORD_REQUIRED'),
                             ('sudo: 1 incorrect password attempt','SUDO_AUTH_FAILED'),
                             ('user is not in the sudoers file','SUDO_NOT_ALLOWED'),
                             ('Sorry, user is not allowed to execute python3 as root','SUDO_NOT_ALLOWED'),
                             ('sudo: sorry, you must have a tty to run sudo','SUDO_TTY_REQUIRED'),
                             ('AssertionError: serial lock exists','RESOURCE_BUSY_OR_UNKNOWN'),
                             ('AssertionError: serial missing','SERIAL_DEVICE_MISSING')]:
            with self.subTest(code=code),patch.object(m.subprocess,'run',return_value=result(1,err=message+' '+SECRET)):
                with self.assertRaises(m.Refused) as e:self.hardware().host()
                self.assertEqual(e.exception.code,code);self.assertNotIn(SECRET,str(e.exception))
    def test_pid_review_uses_same_authentication(self):
        with patch.object(m.subprocess,'run',return_value=result()) as run:
            self.hardware().runner_absent('123')
            self.assertEqual(run.call_args.kwargs['input'],SECRET+'\n')
    def test_live_pid_not_misreported_as_auth_failure(self):
        with patch.object(m.subprocess,'run',return_value=result(3)):
            with self.assertRaises(m.Refused) as e:self.hardware().runner_absent('123')
            self.assertEqual(e.exception.code,'RUNNER_ACTIVE')
    def test_board_sudo_error_is_not_network_retry(self):
        with patch.object(m.subprocess,'run',return_value=result(1,err='sudo: interactive authentication is required')) as run:
            with self.assertRaises(m.Refused) as e:self.hardware().wait_board(180)
            self.assertEqual(e.exception.stage,'board.preflight')
            self.assertIn('Board sudo',str(e.exception))
            self.assertNotIn('[jump]',str(e.exception))
            self.assertEqual(e.exception.code,'SUDO_PASSWORD_REQUIRED')
            self.assertEqual(run.call_count,1)
    def test_preflight_check_has_no_lease_or_power(self):
        with tempfile.TemporaryDirectory() as t:
            hw=self.hardware();path=Path(t)/'lease'
            with patch.object(hw,'host',return_value={'rootVisible':True}),patch.object(hw,'board',return_value={'model':'com260'}),patch.object(hw,'power',side_effect=AssertionError('power forbidden')):
                r=m.Controller(path,hw).run('check',None)
                self.assertFalse(r['leaseCreated']);self.assertFalse(r['powerExecuted'])
                self.assertFalse(path.exists())
    def test_cli_private_transport_and_preview(self):
        cfg=types.SimpleNamespace(jump_sudo_password=SECRET,serial_debug='/dev/debug',serial_relay='/dev/relay',board_target='user@board.example',power_sh='/test/power.sh')
        lab=ctl.Lab(cfg)
        with patch.object(lab,'_ensure_board_helper',return_value=''),patch.object(lab,'jump',return_value=types.SimpleNamespace(returncode=0)) as jump:
            lab.guarded('check',expected_model='com260')
            script=jump.call_args.args[0]
            self.assertIn(repr(SECRET),script)
        lab=ctl.Lab(cfg,dry_run=True);out=io.StringIO()
        with patch.object(lab,'jump',side_effect=AssertionError('no transport')),contextlib.redirect_stdout(out):
            lab.guarded('check',expected_model='com260')
        self.assertNotIn(SECRET,out.getvalue());self.assertNotIn(repr(SECRET),out.getvalue())
    def test_main_returns_structured_failure_without_secret(self):
        with tempfile.TemporaryDirectory() as t,patch.object(m,'GUARD',Path(t)/'guard'),patch.object(m.subprocess,'run',return_value=result(1,err='sudo: a password is required '+SECRET)):
            out=io.StringIO()
            with contextlib.redirect_stdout(out):
                rc=m.main({**self.hardware('').r,'operation':'check'})
            data=json.loads(out.getvalue())
            self.assertEqual(rc,2);self.assertEqual(data['code'],'SUDO_PASSWORD_REQUIRED')
            self.assertEqual(data['stage'],'jump.preflight');self.assertNotIn(SECRET,out.getvalue())
    def test_multiline_password_rejected_without_subprocess(self):
        with patch.object(m.subprocess,'run',side_effect=AssertionError('must not run')):
            with self.assertRaises(m.Refused) as e:self.hardware('bad\npassword').host()
            self.assertEqual(e.exception.code,'SUDO_CONFIG_INVALID')

if __name__=='__main__':unittest.main()
