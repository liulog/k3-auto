#!/usr/bin/env python3
"""板卡 SSH 包装器（在跳板机上运行）。

用法: board_ssh.py <user@host> <remote-command>

密码来源: 环境变量 K3_BOARD_PASSWORD（兼容 SSHPASS）。
认证优先级:
  1. 有密码且跳板机装了 sshpass  -> sshpass -e ssh
  2. 有密码且有 pexpect          -> pexpect 喂密码（与仓库自带 runner 同款）
  3. 否则                        -> 裸 ssh（走 key/agent）

由 k3ctl 上传到跳板机 /tmp/k3-auto/board_ssh.py 后调用；也可被
probe.sh 通过 K3_BOARD_SSH_HELPER 环境变量复用。
"""
import os
import shutil
import subprocess
import sys

SSH = "/usr/bin/ssh"
SSH_OPTS = ["-o", "StrictHostKeyChecking=accept-new",
            "-o", "ConnectTimeout=10", "-o", "ConnectionAttempts=1"]


def _run_with_pexpect(target: str, command: str, password: str) -> int:
    import pexpect  # 由调用方保证可用
    child = pexpect.spawn(SSH, SSH_OPTS + [target, command],
                          encoding="utf-8", timeout=900)
    idx = child.expect([r"(?i)password:", r"(?i)permission denied", pexpect.EOF],
                       timeout=180)
    if idx == 0:
        # idx==0 时 before 只是登录提示，不输出，避免污染调用方解析
        child.sendline(password)
        child.expect(pexpect.EOF, timeout=1800)
        out = child.before or ""
    else:
        out = child.before or ""
    child.close()
    sys.stdout.write(out.lstrip("\r\n"))
    return child.exitstatus if child.exitstatus is not None else 1


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print("usage: board_ssh.py <user@host> <command>", file=sys.stderr)
        return 2
    target, command = argv[1], argv[2]
    password = os.environ.get("K3_BOARD_PASSWORD") or os.environ.get("SSHPASS") or ""

    if password and shutil.which("sshpass"):
        env = dict(os.environ, SSHPASS=password)
        return subprocess.call(["sshpass", "-e", SSH, *SSH_OPTS, target, command],
                               env=env)
    if password:
        try:
            return _run_with_pexpect(target, command, password)
        except ImportError:
            print("board_ssh: 无 sshpass 也无 pexpect，回退 key 认证",
                  file=sys.stderr)
    return subprocess.call([SSH, *SSH_OPTS, target, command])


if __name__ == "__main__":
    sys.exit(main(sys.argv))
