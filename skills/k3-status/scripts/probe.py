#!/usr/bin/env python3
"""Portable client: stream the bundled read-only Bash probe without deploying it."""
import argparse
from pathlib import Path
import shlex
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', help='Experiment-host SSH target; omit for local Linux')
    parser.add_argument('--serial', required=True, help='Debug serial device on experiment host')
    parser.add_argument('--board', default='', help='Board SSH target on experiment host')
    parser.add_argument('--address', default='', help='Optional board hostname/address for ping')
    parser.add_argument('--log', default='', help='Optional existing serial log on experiment host')
    args = parser.parse_args()
    if any(v.startswith('-') or '\n' in v or '\r' in v for v in
           (args.host or '', args.board, args.address, args.serial, args.log)):
        parser.error('Targets/paths must not start with a dash or contain newlines')
    source = Path(__file__).with_name('probe.sh').read_text(encoding='utf-8')
    params = [args.address, args.board, args.serial, args.log]
    if args.host:
        remote = 'bash -s -- ' + ' '.join(shlex.quote(v) for v in params)
        command = ['ssh', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
                   '-o', 'ConnectTimeout=5', args.host, remote]
    else:
        command = ['bash', '-s', '--', *params]
    try:
        result = subprocess.run(command, input=source.encode('utf-8'),
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as exc:
        print('probe_failed: {}; remaining status unknown'.format(exc), file=sys.stderr)
        return 1
    print(result.stdout.decode('utf-8', errors='replace'), end='')
    print(result.stderr.decode('utf-8', errors='replace'), end='', file=sys.stderr)
    return result.returncode


if __name__ == '__main__':
    sys.exit(main())
