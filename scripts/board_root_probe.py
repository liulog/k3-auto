"""Root-owned, read-only K3 board probe; accepts no commands or arguments."""

def main():
    import json,os,subprocess,sys
    from pathlib import Path
    assert os.geteuid()==0, 'root visibility required'
    assert len(sys.argv)==1, 'no arguments accepted'
    serials=[]; extra=[]; busy=[]
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
    import hashlib
    result['probeSHA256']=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    print(json.dumps(result))

if __name__ == '__main__':
    main()
