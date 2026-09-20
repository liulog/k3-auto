#!/usr/bin/env python3
"""k3ctl — 用一份配置驱动 SpaceMiT K3 测试台。

所有远端动作都在「跳板机」上执行；板卡命令由跳板机再 SSH 到板卡
(192.168.137.200)。配置见 config/k3-auto.toml，解析顺序：

    --config  >  $K3_AUTO_CONFIG  >  ./config/k3-auto.toml(向上查找)
              >  ~/.config/k3-auto/config.toml

示例：
    python3 scripts/k3ctl.py status
    python3 scripts/k3ctl.py power off
    python3 scripts/k3ctl.py tftp-put /path/to/Image-xxx
    python3 scripts/k3ctl.py boot Image-xxx
    python3 scripts/k3ctl.py unixbench Image-xxx 0909-round1-baseline
    python3 scripts/k3ctl.py board -- zcat /proc/config.gz
"""
from __future__ import annotations

import argparse
import os
import shlex
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PROBE_SH = REPO_ROOT / "skills" / "k3-status" / "scripts" / "probe.sh"
BOARD_SSH_PY = REPO_ROOT / "scripts" / "board_ssh.py"
# 跳板机上的 helper 路径：绝对、无需展开、每次操作重写，避免依赖 $HOME 展开
BOARD_HELPER = "/tmp/k3-auto/board_ssh.py"


def die(msg: str, code: int = 2):
    print(f"k3ctl: {msg}", file=sys.stderr)
    sys.exit(code)


def resolve_config(explicit: str | None) -> Path:
    """K3/局域网配置（可入库）。"""
    if explicit:
        return Path(explicit).expanduser()
    env = os.environ.get("K3_AUTO_CONFIG")
    if env:
        return Path(env).expanduser()
    for base in (Path.cwd(), *Path.cwd().parents):
        candidate = base / "config" / "k3-auto.toml"
        if candidate.is_file():
            return candidate
    return Path.home() / ".config" / "k3-auto" / "config.toml"


def resolve_jump_config(explicit: str | None) -> Path:
    """跳板机连接配置（不入库）。"""
    if explicit:
        return Path(explicit).expanduser()
    env = os.environ.get("K3_JUMP_CONFIG")
    if env:
        return Path(env).expanduser()
    for base in (Path.cwd(), *Path.cwd().parents):
        candidate = base / "config" / "jump.toml"
        if candidate.is_file():
            return candidate
    return Path.home() / ".config" / "k3-auto" / "jump.toml"


def load_toml(path: Path) -> dict:
    if not path.is_file():
        return {}
    with open(path, "rb") as fh:
        return tomllib.load(fh)


class Config:
    def __init__(self, data: dict, path: Path, jump_data: dict | None = None,
                 jump_path: Path | None = None):
        self.path = path
        self.jump_path = jump_path
        # [jump] 优先取 jump.toml；为了兼容单文件写法，也接受 k3-auto.toml 里的 [jump]
        j = dict(data.get("jump", {}))
        if jump_data:
            j.update(jump_data.get("jump", {}))
        self.jump_host = str(j.get("host", ""))
        self.jump_port = int(j.get("port", 22))
        self.jump_user = str(j.get("user", ""))
        self.jump_password = str(j.get("password", ""))
        self.jump_sudo_password = str(j.get("sudo_password", "") or self.jump_password)
        self.jump_identity = str(j.get("identity_file", ""))
        self.strict_host_key = str(j.get("strict_host_key", "accept-new"))

        # 板卡密码也属于本地秘密：允许放在被忽略的 jump.toml [board] 中覆盖基础配置。
        b = dict(data.get("board", {}))
        if jump_data:
            b.update(jump_data.get("board", {}))
        self.board_host = str(b.get("host", "192.168.137.200"))
        self.board_user = str(b.get("user", "bianbu"))
        self.board_password = str(b.get("password", ""))
        self.board_identity = str(b.get("identity_file", ""))

        s = data.get("serial", {})
        self.serial_relay = str(s.get("relay", "/dev/ttyUSB0"))
        self.serial_debug = str(s.get("debug", "/dev/ttyUSB1"))
        self.serial_debug_baud = str(s.get("debug_baud", 115200))

        lab = data.get("lab", {})
        # 仓库绝对路径属于跳板机信息，优先从 jump.toml 读
        self.repo = str(j.get("repo", "") or lab.get("repo", ""))
        self.tftp_root = str(lab.get("tftp_root", "/srv/tftp"))
        self.log_dir = str(lab.get("log_dir", "logs"))
        self.monitor_seconds = str(lab.get("monitor_seconds", 600))

        sc = data.get("scripts", {})
        self.power_sh = str(sc.get("power", "./k3-power.sh"))
        self.minicom_sh = str(sc.get("minicom", "./k3-minicom.sh"))
        self.minicom_log_sh = str(sc.get("minicom_log", "./minicom-log.sh"))
        self.boot_py = str(sc.get("boot", "tmp/k3_boot_setup.py"))
        self.unixbench_py = str(sc.get("unixbench", "tmp/k3_unixbench_evidence.py"))
        self.lmbench_py = str(sc.get("lmbench", "tmp/k3_lmbench_0907.py"))
        self.python = str(sc.get("python", "/usr/bin/python3"))

        if not self.jump_host or not self.jump_user:
            die(f"缺少跳板机配置 {self.jump_path or 'config/jump.toml'}；"
                "复制 config/jump.example.toml 后填写")

    @property
    def board_target(self) -> str:
        return f"{self.board_user}@{self.board_host}"

    def masked(self) -> str:
        lines = [f"config_file   = {self.path}",
                 f"jump_config   = {self.jump_path or '<none>'}",
                 "[jump]",
                 f"host = {self.jump_host}:{self.jump_port}",
                 f"user = {self.jump_user}",
                 f"password = {'<set>' if self.jump_password else '<empty>'}",
                 f"identity_file = {self.jump_identity or '<unset>'}",
                 "[board]",
                 f"target = {self.board_target}",
                 f"password = {'<set>' if self.board_password else '<empty>'}",
                 "[serial]",
                 f"relay = {self.serial_relay}",
                 f"debug = {self.serial_debug} @ {self.serial_debug_baud}",
                 "[lab]",
                 f"repo = {self.repo}",
                 f"tftp_root = {self.tftp_root}",
                 f"log_dir = {self.log_dir}",
                 f"monitor_seconds = {self.monitor_seconds}"]
        return "\n".join(lines)


class Lab:
    """执行器：内置 ssh 传输，支持 sshpass 密码认证。"""

    def __init__(self, cfg: Config, dry_run: bool = False):
        self.cfg = cfg
        self.dry_run = dry_run

    # ── 传输层 ────────────────────────────────────────────────────────────
    def _ssh_prefix(self, host, user, password, identity, port=22, extra_opts=(),
                    tty=False):
        argv, env = [], {}
        if password:
            if shutil.which("sshpass"):
                argv += ["sshpass", "-e"]
                env["SSHPASS"] = password
            else:
                print("k3ctl: 警告: 配置了密码但本机没有 sshpass，"
                      "回退到密钥/agent 认证", file=sys.stderr)
        argv.append("ssh")
        argv += ["-o", f"StrictHostKeyChecking={self.cfg.strict_host_key}",
                 "-o", "BatchMode=yes" if not password else "BatchMode=no",
                 "-o", "ConnectTimeout=10"]
        if tty:
            argv.append("-tt")
        if port != 22:
            argv += ["-p", str(port)]
        if identity:
            argv += ["-i", os.path.expanduser(identity)]
        argv += list(extra_opts)
        argv.append(f"{user}@{host}" if user else host)
        return argv, env

    def _exec(self, argv, env=None, stdin: str | None = None, check=True):
        if self.dry_run:
            print("+ " + " ".join(shlex.quote(a) for a in argv))
            if stdin:
                print("  <<'EOF'\n" + stdin + "EOF")
            return subprocess.CompletedProcess(argv, 0, "", "")
        full_env = dict(os.environ)
        full_env.update(env or {})
        try:
            proc = subprocess.run(argv, input=stdin.encode() if stdin else None,
                                  env=full_env)
        except FileNotFoundError as exc:
            die(f"命令不可用: {exc}")
        if check and proc.returncode != 0:
            sys.exit(proc.returncode)
        return proc

    def jump(self, script: str, env=None, stdin: str | None = None, check=True):
        """在跳板机实验仓库目录里执行一段 bash（script 作为标准输入传过去）。

        默认注入板卡 helper 与板卡密码环境变量，便于脚本里直接调用
        board_ssh.py 访问板卡。
        """
        env = {**self._board_env(), **dict(env or {})}
        prefix = ""
        if self.cfg.repo:
            prefix = f"cd {shlex.quote(self.cfg.repo)} || exit 1\n"
        envpairs = " ".join(f"{k}={shlex.quote(str(v))}" for k, v in env.items())
        remote = f"env {envpairs} bash -s" if envpairs else "bash -s"
        argv, runenv = self._ssh_prefix(self.cfg.jump_host, self.cfg.jump_user,
                                        self.cfg.jump_password, self.cfg.jump_identity,
                                        self.cfg.jump_port)
        argv.append(remote)
        return self._exec(argv, runenv, stdin=prefix + script, check=check)

    def _ensure_board_helper(self) -> str:
        """把板卡 SSH helper 写到跳板机 /tmp，返回可直接拼入脚本的片段。"""
        src = BOARD_SSH_PY.read_text(encoding="utf-8")
        return (f"mkdir -p $(dirname {BOARD_HELPER})\n"
                f"cat > {BOARD_HELPER} <<'K3_BOARD_HELPER_PY'\n{src}K3_BOARD_HELPER_PY\n")

    def _board_env(self, extra=None) -> dict:
        env = dict(extra or {})
        env["K3_BOARD_SSH_HELPER"] = BOARD_HELPER
        if self.cfg.board_password:
            # 经跳板机进程环境传给 helper；不做交互式提示
            env["K3_BOARD_PASSWORD"] = self.cfg.board_password
        return env

    def status(self, log: str = ""):
        if not PROBE_SH.is_file():
            die(f"缺少 {PROBE_SH}")
        envpairs = " ".join(f"{k}={shlex.quote(str(v))}"
                            for k, v in self._board_env().items())
        # probe.sh 参数顺序: address board serial log
        params = [self.cfg.board_host, self.cfg.board_target,
                  self.cfg.serial_debug, log]
        prefix = f"cd {shlex.quote(self.cfg.repo)} && " if self.cfg.repo else ""
        remote = (f"{prefix}env {envpairs} bash -s -- "
                  + " ".join(shlex.quote(v) for v in params))
        argv, runenv = self._ssh_prefix(self.cfg.jump_host, self.cfg.jump_user,
                                        self.cfg.jump_password, self.cfg.jump_identity,
                                        self.cfg.jump_port)
        argv.append(remote)
        stdin = self._ensure_board_helper() + PROBE_SH.read_text(encoding="utf-8")
        return self._exec(argv, runenv, stdin=stdin)

    def board_script(self, cmd: str) -> str:
        """经跳板机上的 helper SSH 进板卡执行 cmd。"""
        return (f"python3 {BOARD_HELPER} {shlex.quote(self.cfg.board_target)} "
                f"{shlex.quote(cmd)}")

    def board(self, cmd: str, env=None, check=True):
        script = self._ensure_board_helper() + self.board_script(cmd)
        return self.jump(script, env=self._board_env(env), check=check)

    # ── 业务动作 ──────────────────────────────────────────────────────────
    def abs_script(self, attr: str) -> str:
        """脚本路径转成跳板机上的绝对路径（sudoers NOPASSWD 按绝对路径匹配）。"""
        path = getattr(self.cfg, attr)
        if path.startswith("/") or not self.cfg.repo:
            return path
        return f"{self.cfg.repo.rstrip('/')}/{path.lstrip('./')}"

    def jump_tty(self, cmd: str):
        """需要真正 TTY 的交互命令（minicom）。"""
        remote = f"cd {shlex.quote(self.cfg.repo)} && exec {cmd}" if self.cfg.repo else cmd
        argv, runenv = self._ssh_prefix(self.cfg.jump_host, self.cfg.jump_user,
                                        self.cfg.jump_password, self.cfg.jump_identity,
                                        self.cfg.jump_port, tty=True)
        argv.append(remote)
        return self._exec(argv, runenv)

    def _sudo(self, cmd: str) -> str:
        """需要普通 sudo 的场景（如搬文件）：配了密码就 sudo -S，否则 sudo -n。"""
        if self.cfg.jump_sudo_password:
            return (f"printf '%s\\n' {shlex.quote(self.cfg.jump_sudo_password)} | "
                    f"sudo -S -p '' {cmd}")
        return f"sudo -n {cmd}"

    def power(self, action: str):
        # k3-power.sh 在 sudoers 里有 NOPASSWD，但必须使用绝对路径才匹配
        self.jump(f"sudo -n {shlex.quote(self.abs_script('power_sh'))} {action}")

    def minicom(self):
        self.jump_tty(f"sudo -n {shlex.quote(self.abs_script('minicom_sh'))}")

    def _serial_unlock(self) -> str:
        """清掉 minicom 遗留的过期锁文件（需 sudo）；须在启动 minicom 前调用。"""
        lock = f"/var/lock/LCK..{Path(self.cfg.serial_debug).name}"
        return self._sudo(f"rm -f {shlex.quote(lock)}") + "; "

    def serial_log(self, prefix: str, logdir: str = "", background: bool = True):
        logdir = logdir or self.cfg.log_dir
        # minicom-log.sh 由普通用户启动，其内部 sudo k3-minicom.sh 命中 NOPASSWD
        inner = (f"{shlex.quote(self.abs_script('minicom_log_sh'))} {shlex.quote(prefix)} "
                 f"{shlex.quote(self.cfg.serial_debug)} "
                 f"{shlex.quote(self.cfg.serial_debug_baud)} {shlex.quote(logdir)}")
        if not background:
            self.jump(self._serial_unlock())
            return self.jump_tty(inner)
        out = f"/tmp/k3ctl-serial-{prefix}.log"
        # minicom 需要 TTY：用 script -qec 造一个 pty，再 setsid 脱离 ssh 会话
        runner = (f"TERM=xterm setsid script -qec {shlex.quote(inner)} /dev/null")
        script = (f"{runner} > {shlex.quote(out)} 2>&1 </dev/null &\n"
                  f"sleep 4\n"
                  f"cat {shlex.quote(out)} 2>/dev/null || true\n"
                  f"ps -eo args | grep -q '[m]inicom' && echo 'minicom: running' "
                  f"|| echo 'minicom: NOT running'\n"
                  f"ls -t {shlex.quote(logdir)}/{shlex.quote(prefix)}-*.log 2>/dev/null "
                  f"| head -n1 || true")
        self.jump(self._serial_unlock() + script)

    def serial_owner(self):
        """谁占着调试串口。minicom 以 root 运行，必须 sudo 才能看到。"""
        dev = shlex.quote(self.cfg.serial_debug)
        cmd = self._sudo(f"fuser -v {dev}") + "; " + \
              ("ps -eo pid,user,etime,args | "
               "grep -E '[m]inicom|[p]icocom|[[:space:]]screen[[:space:]]' "
               "| grep -v screensaver "
               "|| echo '无 minicom/picocom/screen 进程'")
        self.jump(cmd)

    def serial_stop(self, prefix: str = ""):
        """停掉后台采集的 minicom（仅限调试串口），避免抢占串口。"""
        pattern = f"minicom -D {self.cfg.serial_debug}"
        # 用 -x 按进程名精确匹配，避免 pkill -f 把承载它的 sudo 自己也匹配上
        # （minicom 会拦 SIGTERM，先 TERM 再 KILL，最后确认真的退出）
        kill = self._sudo("pkill -x minicom") + "; sleep 2; "
        kill += self._sudo("pkill -9 -x minicom") + "; sleep 1; "
        check = (f"if pgrep -f {shlex.quote(pattern)} >/dev/null; then "
                 f"echo '仍占用:'; pgrep -af {shlex.quote(pattern)}; "
                 f"else echo 'minicom 已停止'; fi")
        self.jump(kill + check)

    def tftp_put(self, local: str, name: str = ""):
        src = Path(local).expanduser().resolve()
        if not src.is_file():
            die(f"文件不存在: {src}")
        remote_name = name or src.name
        remote_dir = self.cfg.tftp_root
        # 走 ssh 流式上传，避免依赖本机 scp 的认证配置
        argv, runenv = self._ssh_prefix(self.cfg.jump_host, self.cfg.jump_user,
                                        self.cfg.jump_password, self.cfg.jump_identity,
                                        self.cfg.jump_port)
        argv.append(f"cat > /tmp/{shlex.quote(src.name)}.k3ctl")
        print(f"k3ctl: 上传 {src.name} → 跳板机 …", file=sys.stderr)
        with open(src, "rb") as fh:
            if self.dry_run:
                print("+ <upload> " + " ".join(argv))
            else:
                full_env = dict(os.environ); full_env.update(runenv)
                proc = subprocess.run(argv, stdin=fh, env=full_env)
                if proc.returncode != 0:
                    sys.exit(proc.returncode)
        self.jump(self._sudo(f"mv /tmp/{shlex.quote(src.name)}.k3ctl "
                             f"{shlex.quote(remote_dir + '/' + remote_name)}") + " && "
                  f"ls -l {shlex.quote(remote_dir + '/' + remote_name)}")

    def boot(self, image: str, prefix: str = "inspect-board"):
        self.jump(self._serial_unlock()
                  + f"{shlex.quote(self.cfg.python)} {shlex.quote(self.cfg.boot_py)}",
                  env={"K3_IMAGE": image, "K3_LOG_PREFIX": prefix,
                       "K3_LOG_DIR": self.cfg.log_dir, "TERM": "xterm"})

    def _benchmark(self, runner: str, image: str, run_id: str, campaign: str,
                   id_env: str, background: bool = False):
        status_path = f"{self.cfg.log_dir}/{campaign}.json"
        env = {id_env: run_id,
               "K3_IMAGE": image,
               "K3_LOG_PREFIX": run_id,
               "K3_STATUS_PATH": status_path,
               "K3_LOG_DIR": self.cfg.log_dir,
               "K3_MONITOR_SECONDS": self.cfg.monitor_seconds}
        cmd = f"{shlex.quote(self.cfg.python)} {shlex.quote(runner)}"
        # 非交互 ssh 的 TERM 是 dumb，minicom 会报「没有 termcap 条目」直接退出
        env.setdefault("TERM", "xterm")
        pre = self._serial_unlock()
        if not background:
            return self.jump(pre + cmd, env=env)
        # 一轮 1 小时量级，后台跑，不依赖调用方连接保持
        out = f"/tmp/k3ctl-run-{run_id}.log"
        script = (f"setsid nohup {cmd} > {shlex.quote(out)} 2>&1 </dev/null &\n"
                  f"sleep 5\n"
                  f"echo \"runner_pid=$(pgrep -f {shlex.quote(runner)} | head -n1)\"\n"
                  f"echo \"runner_log={out}\"\n"
                  f"echo \"status_json={status_path}\"\n"
                  f"tail -n 15 {shlex.quote(out)} 2>/dev/null || true")
        self.jump(pre + script, env=env)

    def unixbench(self, image, run_id, campaign, background=False):
        self._benchmark(self.cfg.unixbench_py, image, run_id, campaign,
                        "K3_RUN_IDS", background)

    def lmbench(self, image, run_id, campaign, background=False):
        self._benchmark(self.cfg.lmbench_py, image, run_id, campaign,
                        "K3_RUN_ID", background)

    def run_log(self, run_id: str, lines: int = 25):
        """看后台 runner 的日志尾部。"""
        out = f"/tmp/k3ctl-run-{run_id}.log"
        self.jump(f"test -f {shlex.quote(out)} || {{ echo '无 {out}'; exit 1; }}; "
                  f"wc -l {shlex.quote(out)}; tail -n {int(lines)} {shlex.quote(out)}")

    def runners(self):
        self.jump("ps -eo pid,ppid,etime,args | "
                  "grep -E 'minicom|k3_(unixbench|lmbench|boot)|make rerun' | "
                  "grep -v grep || echo '(无 runner/minicom 进程)'")

    def logs(self, limit: int = 40):
        self.jump(f"ls -t {shlex.quote(self.cfg.log_dir)}/*.log 2>/dev/null | "
                  f"head -n 3 | while read -r f; do echo \"=== $f\"; "
                  f"tail -n {int(limit)} \"$f\"; done || echo '(无日志)'")

    def tail(self, prefix: str):
        self.jump(f"f=$(ls -t {shlex.quote(self.cfg.log_dir)}/{shlex.quote(prefix)}-*.log "
                  f"2>/dev/null | head -n1); test -n \"$f\" || "
                  f"{{ echo '未找到 {prefix} 的日志'; exit 1; }}; echo \"== $f\"; tail -f \"$f\"")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="k3ctl", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("-c", "--config", help="K3/局域网配置文件路径")
    p.add_argument("--jump-config", help="跳板机配置文件路径（默认 config/jump.toml）")
    p.add_argument("-n", "--dry-run", action="store_true", help="只打印命令，不执行")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("show-config", help="显示生效的配置（密码打码）")
    sub.add_parser("status", help="只读状态快照（串口占用/镜像/测试）")
    sub.add_parser("runners", help="列出跳板机上的 runner/minicom 进程")
    sub.add_parser("logs", help="查看最近的控制台日志").add_argument(
        "-n", "--lines", type=int, default=40)
    sub.add_parser("tail", help="跟踪某前缀最新日志").add_argument("prefix")
    sub.add_parser("minicom", help="打开交互式 minicom（Ctrl+A 然后 X 退出）")

    sp = sub.add_parser("power", help="继电器上下电")
    sp.add_argument("action", choices=["on", "off"])
    sp = sub.add_parser("serial-log", help="启动 minicom 日志采集")
    sp.add_argument("prefix")
    sp.add_argument("--logdir", default="")
    sp.add_argument("--foreground", action="store_true",
                    help="前台运行（直接看串口）；默认后台采集并回显日志路径")
    sub.add_parser("serial-stop", help="停止后台 minicom 采集")
    sub.add_parser("serial-owner", help="查谁占着调试串口（带 sudo，可看到 root 进程）")
    sp = sub.add_parser("tftp-put", help="上传 Image 到跳板机 TFTP 根目录")
    sp.add_argument("file")
    sp.add_argument("--name", default="", help="远端文件名（默认用本地文件名）")
    sp = sub.add_parser("boot", help="启动指定 Image 并保持板卡在线以便检查")
    sp.add_argument("image")
    sp.add_argument("--prefix", default="inspect-board")

    for name, help_text in (("unixbench", "跑一轮 UnixBench"), ("lmbench", "跑一轮 LMbench")):
        sp = sub.add_parser(name, help=help_text)
        sp.add_argument("image")
        sp.add_argument("run_id")
        sp.add_argument("--campaign", default="baseline")
        sp.add_argument("--background", action="store_true",
                        help="后台跑（1 小时量级推荐），返回 pid 与日志路径")

    sp = sub.add_parser("run-log", help="看后台 runner 的日志尾部")
    sp.add_argument("run_id")
    sp.add_argument("-n", "--lines", type=int, default=25)

    sp = sub.add_parser("jump", help="在跳板机实验仓库里执行命令")
    sp.add_argument("argv", nargs=argparse.REMAINDER,
                    help="命令（建议用 -- 分隔），多段会拼成一行")
    sp = sub.add_parser("board", help="在板卡上执行命令（经跳板机）")
    sp.add_argument("argv", nargs=argparse.REMAINDER,
                    help="命令（建议用 -- 分隔），多段会拼成一行")
    return p


def main():
    args = build_parser().parse_args()
    path = resolve_config(args.config)
    if not path.is_file():
        die(f"找不到配置 {path}；复制 config/k3-auto.example.toml 后填写")
    data = load_toml(path)
    jump_path = resolve_jump_config(args.jump_config)
    cfg = Config(data, path, load_toml(jump_path), jump_path)
    lab = Lab(cfg, dry_run=args.dry_run)

    cmd = args.cmd
    if cmd == "show-config":
        print(cfg.masked())
    elif cmd == "status":
        return lab.status().returncode
    elif cmd == "runners":
        lab.runners()
    elif cmd == "logs":
        lab.logs(args.lines)
    elif cmd == "tail":
        lab.tail(args.prefix)
    elif cmd == "minicom":
        lab.minicom()
    elif cmd == "power":
        lab.power(args.action)
    elif cmd == "serial-log":
        lab.serial_log(args.prefix, args.logdir, background=not args.foreground)
    elif cmd == "serial-stop":
        lab.serial_stop()
    elif cmd == "serial-owner":
        lab.serial_owner()
    elif cmd == "tftp-put":
        lab.tftp_put(args.file, args.name)
    elif cmd == "boot":
        lab.boot(args.image, args.prefix)
    elif cmd == "unixbench":
        lab.unixbench(args.image, args.run_id, args.campaign, args.background)
    elif cmd == "lmbench":
        lab.lmbench(args.image, args.run_id, args.campaign, args.background)
    elif cmd == "run-log":
        lab.run_log(args.run_id, args.lines)
    elif cmd in ("jump", "board"):
        argv = list(args.argv)
        if argv and argv[0] == "--":
            argv = argv[1:]
        if not argv:
            die(f"{cmd}: 需要命令，例如 k3ctl {cmd} -- uname -a")
        # 单个参数按原样当 shell 片段；多个参数按 argv 拼成一条命令。
        script = argv[0] if len(argv) == 1 else shlex.join(argv)
        if cmd == "jump":
            lab.jump(script)
        else:
            lab.board(script)
    return 0


if __name__ == "__main__":
    sys.exit(main())
