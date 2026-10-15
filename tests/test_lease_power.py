"""Offline state machine tests; no SSH, relay, subprocess or real sleep."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('lease_power',Path(__file__).resolve().parents[1]/'scripts/lease_power.py')
m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)

class Fake:
    def __init__(self):
        self.calls=[]; self.boot='boot-before'; self.busy=False; self.fail=None
    def host(self):
        self.calls.append('host')
        if self.busy: raise m.Refused('busy')
    def board(self):
        self.calls.append('board')
        return {'bootId':self.boot,'model':'com260','kernel':'test'}
    def runner_absent(self,pid):
        if self.fail=='alive': raise m.Refused('alive')
    def power(self,action):
        self.calls.append(action)
        if self.fail==action: raise m.Refused('relay unknown')
        if action=='on' and self.fail!='same-boot': self.boot='boot-after'
    def wait_board(self,seconds):
        if self.fail=='wait': raise m.Refused('not ready')
        return self.board()

class Tests(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup)
        self.path=Path(t.name)/'lease'; self.hw=Fake()
        self.c=m.Controller(self.path,self.hw,sleep=lambda _:None)
        p=patch.object(m.subprocess,'run',side_effect=AssertionError('No real subprocess permitted'))
        p.start(); self.addCleanup(p.stop)
    def acquire(self): self.c.run('acquire','mine')
    def test_cycle_and_release(self):
        self.acquire(); self.c.run('cycle','mine'); result=self.c.run('release','mine')
        self.assertTrue(result['released']); self.assertFalse(self.path.exists())
        self.assertTrue(self.path.with_name('lease.released-mine.json').is_file())
        self.assertEqual([x for x in self.hw.calls if x in ('off','on')],['off','on'])
        with self.assertRaises(m.Refused): self.acquire()
    def test_foreign_owner(self):
        self.acquire()
        for op in ['acquire','release','off','on','cycle','review']:
            with self.subTest(op=op),self.assertRaises(m.Refused): self.c.run(op,'other')
        self.assertEqual((self.path/'owner').read_text().strip(),'mine')
    def test_serial_busy_no_claim(self):
        self.hw.busy=True
        with self.assertRaises(m.Refused): self.acquire()
        self.assertFalse(self.path.exists()); self.assertNotIn('off',self.hw.calls)
    def test_existing_runner_no_power_even_dead(self):
        self.acquire(); (self.path/'runner.pid').write_text('999999')
        with self.assertRaises(m.Refused): self.c.run('cycle','mine')
        self.assertNotIn('off',self.hw.calls)
    def test_unknown_entry_no_delete(self):
        self.acquire(); (self.path/'evidence').write_text('keep')
        with self.assertRaises(m.Refused): self.c.run('release','mine')
        self.assertTrue((self.path/'evidence').exists())
    def test_symlink_refused(self):
        self.path.symlink_to(self.path.parent,target_is_directory=True)
        with self.assertRaises(m.Refused): self.c.run('status',None)
    def test_on_without_proven_off_refused(self):
        self.acquire()
        with self.assertRaises(m.Refused): self.c.run('on','mine')
        self.assertNotIn('on',self.hw.calls)
    def test_off_then_on_preserves_lease(self):
        self.acquire(); self.c.run('off','mine')
        self.assertTrue(self.path.exists())
        with self.assertRaises(m.Refused): self.c.run('release','mine')
        self.c.run('on','mine'); self.c.run('release','mine')
    def test_power_failure_keeps_reservation_and_marker(self):
        for action in ('off','on'):
            with self.subTest(action=action):
                with tempfile.TemporaryDirectory() as tmp:
                    c=m.Controller(Path(tmp)/'lease',self.hw,sleep=lambda _:None)
                    c.run('acquire','mine'); self.hw.fail=action
                    with self.assertRaises(m.Refused): c.run('cycle','mine')
                    self.assertTrue((c.path/'runner.pid').exists())
                    self.assertEqual(json.loads((c.path/'power.json').read_text())['phase'],action+'-reserved')
                    with self.assertRaises(m.Refused): c.run('cycle','mine')
                    self.hw.fail=None
    def test_wait_and_same_boot_failure_not_success(self):
        for failure in ('wait','same-boot'):
            with self.subTest(failure=failure),tempfile.TemporaryDirectory() as tmp:
                self.hw.boot='before';self.hw.fail=failure
                c=m.Controller(Path(tmp)/'lease',self.hw,sleep=lambda _:None);c.run('acquire','mine')
                with self.assertRaises(m.Refused): c.run('cycle','mine')
                self.assertTrue((c.path/'runner.pid').exists())
    def test_review_only_explicit_dead_owner_with_verified_boot(self):
        self.acquire();self.hw.fail='wait'
        with self.assertRaises(m.Refused):self.c.run('cycle','mine')
        self.hw.fail='alive'
        with self.assertRaises(m.Refused):self.c.run('review','mine')
        self.hw.fail=None; self.c.run('review','mine');self.c.run('release','mine')
    def test_legacy_owner_review_not_power(self):
        self.path.mkdir();(self.path/'owner').write_text('mine')
        with self.assertRaises(m.Refused):self.c.run('cycle','mine')
        self.c.run('release','mine');self.assertFalse(self.path.exists())
    def test_reservation_visible_before_relay(self):
        self.acquire()
        def power(action):
            j=json.loads((self.path/'power.json').read_text())
            self.assertEqual(j['phase'],action+'-reserved')
            raise m.Refused('interrupted')
        self.hw.power=power
        with self.assertRaises(m.Refused):self.c.run('cycle','mine')
    def test_probe_does_not_match_screensaver_substrings(self):
        # Evaluate the fixed selection against representative argv, no process I/O.
        start=m.PROBE.index(' names='); end=m.PROBE.index('\nassert not busy')
        code=m.PROBE[start:end]
        for comm,args,expected in [('gsd-screensaver',['/usr/bin/gsd-screensaver'],False),('screen',['screen','/dev/ttyUSB1'],True),('python3',['python3','/tmp/manual-unixbench-host.py'],True),('sudo',['sudo','python3','-c',m.PROBE,'[]'],False)]:
            env={'extra':[],'Path':Path,'comm':comm,'args':args,'busy':[],'p':Path('123')}
            exec('\n'.join(line[1:] for line in code.splitlines()),env)
            self.assertEqual(bool(env['busy']),expected)

    def test_second_cycle_and_on_are_not_replayed(self):
        self.acquire();self.c.run('cycle','mine')
        before=list(self.hw.calls)
        for op in ('cycle','on','off'):
            with self.assertRaises(m.Refused):self.c.run(op,'mine')
        self.assertEqual(self.hw.calls,before)
    def test_failure_after_claim_keeps_owned_lease(self):
        calls=0
        def host():
            nonlocal calls
            calls+=1
            if calls==2:raise m.Refused('changed after claim')
        self.hw.host=host
        with self.assertRaises(m.Refused):self.acquire()
        self.assertEqual((self.path/'owner').read_text().strip(),'mine')
        self.assertNotIn('off',self.hw.calls)
    def test_review_unknown_off_with_unchanged_boot_keeps_marker(self):
        self.acquire();self.hw.fail='off'
        with self.assertRaises(m.Refused):self.c.run('cycle','mine')
        self.hw.fail=None
        with self.assertRaises(m.Refused):self.c.run('review','mine')
        self.assertTrue((self.path/'runner.pid').exists())
    def test_wait_does_not_retry_authentication_or_identity(self):
        h=m.Hardware({})
        with patch.object(h,'board',side_effect=m.Refused('authentication')) as board:
            with self.assertRaises(m.Refused):h.wait_board(180)
            self.assertEqual(board.call_count,1)
    def test_wait_network_recovery_never_calls_relay(self):
        h=m.Hardware({})
        with patch.object(h,'board',side_effect=[m.Unavailable('network'),{'bootId':'new'}]), patch.object(m.time,'sleep'), patch.object(h,'power') as power:
            self.assertEqual(h.wait_board(180),{'bootId':'new'})
            power.assert_not_called()
    def test_command_guard_excludes_second_invocation(self):
        import os,fcntl,contextlib,io
        guard=self.path.parent/'guard'
        fd=os.open(guard,os.O_RDWR|os.O_CREAT,0o600)
        try:
            fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            with patch.object(m,'GUARD',guard),contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(m.main({'operation':'status'}),2)
        finally:os.close(fd)

    def test_cli_routes_only_fixed_helper_and_preserves_failure_code(self):
        import types
        loader=importlib.util.spec_from_file_location('k3ctl_test',Path(__file__).resolve().parents[1]/'scripts/k3ctl.py')
        ctl=importlib.util.module_from_spec(loader);loader.loader.exec_module(ctl)
        args=ctl.build_parser().parse_args(['safe-power','cycle','--owner','mine','--expected-model','com260'])
        self.assertEqual(args.wait,180)
        cfg=types.SimpleNamespace(serial_debug='/dev/debug',serial_relay='/dev/relay',
            board_target='user@board.example',power_sh='/test/k3-power.sh')
        lab=ctl.Lab(cfg)
        with patch.object(lab,'_ensure_board_helper',return_value=''),patch.object(lab,'jump',return_value=types.SimpleNamespace(returncode=2)) as jump:
            self.assertEqual(lab.guarded('cycle','mine','com260',180),2)
            script=jump.call_args.args[0]
            self.assertIn("'operation': 'cycle'",script)
            self.assertIn('class Controller:',script)
            self.assertFalse(jump.call_args.kwargs['check'])

if __name__=='__main__': unittest.main()
