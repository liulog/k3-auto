"""Fixed jump-host lease/power helper. Importing this module performs no I/O."""
import fcntl
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import time

LEASE = Path('/tmp/k3-agent-workflow-board.lock')
GUARD = Path('/tmp/k3-agent-workflow-board.guard')


class Refused(RuntimeError):
    pass


class Unavailable(Refused):
    pass


def save(path, value):
    temporary = path.with_name(path.name + '.tmp')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as f:
        json.dump(value, f); f.flush(); os.fsync(f.fileno())
    temporary.replace(path)
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def snapshot(path):
    if not path.exists() and not path.is_symlink():
        return {'exists': False}
    if path.is_symlink() or not path.is_dir() or path.stat().st_uid != os.getuid():
        raise Refused('Lease is not an ordinary directory')
    names = {p.name for p in path.iterdir()}
    if names - {'owner', 'runner.pid', 'power.json'}:
        raise Refused('Unknown lease entries; preserve for review')
    for p in path.iterdir():
        if p.is_symlink() or not p.is_file():
            raise Refused('Non-regular lease entry')
    owner = (path / 'owner').read_text().strip()
    return {'exists': True, 'owner': owner,
            'runnerPid': (path / 'runner.pid').read_text().strip() if 'runner.pid' in names else None,
            'power': json.loads((path / 'power.json').read_text()) if 'power.json' in names else None}


# Executed as root on the host or board. Match executable/argument basenames,
# never substrings like "screen" in "gsd-screensaver".
PROBE = r'''
import json,os,subprocess,sys
from pathlib import Path
assert os.geteuid()==0, 'root visibility required'
serials=json.loads(sys.argv[1]); extra=json.loads(sys.argv[2]) if len(sys.argv)>2 else []; busy=[]
for serial in serials:
 assert Path(serial).exists(), 'serial missing'
 assert not Path('/var/lock/LCK..'+Path(serial).name).exists(), 'serial lock exists'
 r=subprocess.run(['fuser',serial],capture_output=True,text=True,timeout=10)
 assert r.returncode==1 and not r.stdout.strip() and not r.stderr.strip(), 'serial busy or unknown'
for p in Path('/proc').glob('[0-9]*'):
 if int(p.name)==os.getpid(): continue
 try:
  comm=(p/'comm').read_text().strip()
  args=(p/'cmdline').read_bytes().decode(errors='replace').split('\0')
 except FileNotFoundError: continue
 names=[Path(a).name for a in args if a and not a.startswith('-') and '\n' not in a]
 serial=comm in ('minicom','picocom','screen')
 runner=any(n in ('k3-power.sh','k3-minicom.sh','manual-tftp-boot.py','manual-uboot-env.py','manual-unixbench-host.py','real-k3-remote.py','unixbench-matrix.py','Run','Run-c8','lmbench') or n in extra or n.startswith(('k3_unixbench','k3_lmbench','k3_boot_','lat_','bw_')) for n in names)
 benchmark=any('/UnixBench/pgms/' in a and '\n' not in a for a in args) or ('make' in names and 'rerun' in args)
 if serial or runner or benchmark: busy.append(int(p.name))
assert not busy, 'runner or terminal active'
result={'rootVisible':True,'resourcesFree':True}
if not serials:
 result.update(bootId=Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
  kernel=os.uname().release,model=Path('/proc/device-tree/model').read_text().rstrip('\0\n'))
print(json.dumps(result))
'''


class Hardware:
    def __init__(self, request):
        self.r = request

    def host(self):
        r = subprocess.run(['sudo', '-n', 'python3', '-c', PROBE,
                            json.dumps(self.r['serials']), json.dumps([Path(self.r['powerScript']).name])], capture_output=True, text=True, timeout=30)
        if r.returncode:
            raise Refused('Host root-visible serial/runner check failed')
        return json.loads(r.stdout)

    def board(self):
        import shlex
        command = shlex.join(['sudo', '-n', 'python3', '-c', PROBE, '[]'])
        r = subprocess.run(['python3', self.r['helper'], self.r['boardTarget'], command],
                           capture_output=True, text=True, timeout=30)
        if r.returncode:
            message=(r.stdout+r.stderr).lower()
            if r.returncode==255 and not any(x in message for x in ('permission denied','authentication','host key')) and any(x in message for x in ('connection refused','connection timed out','no route to host','network is unreachable')):
                raise Unavailable('Board network not ready')
            raise Refused('Board authentication, root visibility or resource check failed')
        result = json.loads(r.stdout)
        if self.r['expectedModel'].lower() not in result['model'].lower():
            raise Refused('Board model mismatch')
        return result

    def runner_absent(self, pid):
        if not str(pid).isdigit() or int(pid)<=1:
            raise Refused('Invalid runner PID')
        code="import os,sys\ntry: os.kill(int(sys.argv[1]),0)\nexcept ProcessLookupError: sys.exit(0)\nsys.exit(1)"
        r=subprocess.run(['sudo','-n','python3','-c',code,str(pid)],capture_output=True,timeout=15)
        if r.returncode:
            raise Refused('Runner still alive or cannot prove it absent')

    def power(self, action):
        r = subprocess.run(['sudo', '-n', self.r['powerScript'], action],
                           capture_output=True, text=True, timeout=30)
        if r.returncode:
            raise Refused('Relay command failed; physical state unknown')

    def wait_board(self, seconds):
        # Read-only connection waits; never repeat a relay command.
        deadline = time.monotonic() + seconds
        while True:
            try:
                return self.board()
            except (Unavailable, subprocess.TimeoutExpired):
                if time.monotonic() >= deadline:
                    raise Refused('Board did not become verifiably ready within wait budget') from None
                time.sleep(min(5, max(0, deadline-time.monotonic())))


class Controller:
    def __init__(self, path, hardware, sleep=time.sleep):
        self.path, self.hw, self.sleep = path, hardware, sleep

    def owned(self, owner):
        s = snapshot(self.path)
        if not s['exists'] or s['owner'] != owner or s['runnerPid'] is not None:
            raise Refused('Lease absent, foreign, or runner.pid present; no takeover')
        return s

    def run(self, operation, owner, wait=180):
        if operation == 'status':
            return snapshot(self.path)
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,95}', owner or ''):
            raise Refused('An explicit valid owner RUN_ID is required')
        if operation == 'acquire':
            if self.path.with_name(self.path.name+'.released-'+owner+'.json').exists():
                raise Refused('Owner already completed; use a new authorized RUN_ID')
            if snapshot(self.path)['exists']:
                raise Refused('Lease already exists; inspect instead of reacquiring')
            self.hw.host(); before = self.hw.board()
            self.path.mkdir(mode=0o700)  # Atomic against legacy mkdir users too.
            fd = os.open(self.path/'owner', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd,'w') as f:
                f.write(owner+'\n'); f.flush(); os.fsync(f.fileno())
            # Recheck after claim. Failure keeps the lease for explicit review.
            self.hw.host(); self.hw.board()
            save(self.path/'power.json', {'phase':'ready','before':before,'events':[]})
            return snapshot(self.path)
        if operation == 'review':
            s=snapshot(self.path)
            if not s['exists'] or s['owner']!=owner:
                raise Refused('Review requires your recorded owner, never another owner')
            if s['runnerPid'] is not None:
                self.hw.runner_absent(s['runnerPid'])
            self.hw.host(); after=self.hw.board()
            journal=s['power']
            if journal:
                if journal['phase'] != 'ready' and after['bootId']==journal['before']['bootId']:
                    raise Refused('No verified new boot; preserve uncertain power state')
                journal.update(phase='online' if journal['phase']!='ready' else 'ready',after=after)
                journal['events'].append({'event':'explicit-inactive-owner-review','at':time.time()})
                save(self.path/'power.json',journal)
            if s['runnerPid'] is not None:
                (self.path/'runner.pid').unlink()
            return snapshot(self.path)
        s = self.owned(owner)
        if operation == 'release':
            # Stale legacy leases may be released only after explicit owner match,
            # absent runner.pid and fresh live evidence. No age/PID-based force.
            self.hw.host(); self.hw.board()
            self.owned(owner)
            if s['power'] and s['power']['phase'] not in ('ready','online'):
                raise Refused('Uncertain/off power state; retain lease and evidence')
            # Retain the journal next to the removed lease, never erase it.
            if s['power']:
                if self.path.with_name(self.path.name+'.released-'+owner+'.json').exists():
                    raise Refused('Prior release evidence exists; never overwrite')
                save(self.path.with_name(self.path.name+'.released-'+owner+'.json'),s)
                (self.path/'power.json').unlink()
            (self.path/'owner').unlink(); self.path.rmdir()
            return {'released':True,'owner':owner}
        journal = s['power']
        if not journal:
            raise Refused('Legacy lease has no power journal; explicitly review/release first')
        if operation not in ('off','on','cycle'):
            raise Refused('Unknown operation')
        required = 'off-acknowledged' if operation == 'on' else 'ready'
        if journal['phase'] != required:
            raise Refused('Power step already reserved or wrong phase; never replay')
        self.hw.host()
        if operation != 'on':
            journal['before'] = self.hw.board()
        self.owned(owner)
        # runner.pid is jump-host PID, deliberately retained after interruption.
        (self.path/'runner.pid').write_text(str(os.getpid())+'\n')
        try:
            actions = ['off','on'] if operation == 'cycle' else [operation]
            for action in actions:
                if action == 'on':
                    off_time = next(e['at'] for e in reversed(journal['events']) if e.get('action')=='off' and e['event']=='acknowledged')
                    self.sleep(max(0, 30-(time.time()-off_time)))
                self.hw.host()
                journal['phase'] = action+'-reserved'
                journal['events'].append({'action':action,'event':'reserved','at':time.time()})
                save(self.path/'power.json',journal)  # Before touching relay.
                self.hw.power(action)
                journal['phase'] = action+'-acknowledged'
                journal['events'].append({'action':action,'event':'acknowledged','at':time.time()})
                save(self.path/'power.json',journal)
                if action == 'off':
                    self.sleep(30)
                else:
                    after = self.hw.wait_board(wait)
                    if after['bootId'] == journal['before']['bootId']:
                        raise Refused('Boot ID unchanged; power cycle not verified')
                    self.hw.host()
                    journal.update(phase='online',after=after)
                    save(self.path/'power.json',journal)
            return snapshot_without_pid(self.path)
        finally:
            # Only a completed known phase can remove our runner marker.
            # Failure leaves both journal and marker, even if this process exits.
            if journal['phase'] in ('off-acknowledged','online'):
                if (self.path/'runner.pid').read_text().strip()==str(os.getpid()):
                    (self.path/'runner.pid').unlink()


def snapshot_without_pid(path):
    result=snapshot(path); result['runnerPid']=None
    return result


def main(request):
    os.umask(0o077)
    # Serializes new helper invocations; legacy tools still coordinate via mkdir.
    fd=os.open(GUARD,os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW,0o600)
    try:
        st=os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_uid!=os.getuid():
            raise Refused('Invalid operation guard')
        fcntl.flock(fd,fcntl.LOCK_EX | fcntl.LOCK_NB)
        result=Controller(LEASE,Hardware(request)).run(request['operation'],request.get('owner'),request.get('wait',180))
        print(json.dumps({'ok':True,'operation':request['operation'],'result':result}),flush=True)
        return 0
    except Exception as error:
        # Don't echo subprocess argv, output or credentials.
        print(json.dumps({'ok':False,'status':'needs_attention','errorType':type(error).__name__,
                          'reason':str(error) if isinstance(error,Refused) else 'Operation failed; inspect lease without retrying power'}),flush=True)
        return 2
    finally:
        os.close(fd)
