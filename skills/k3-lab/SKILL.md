---
name: k3-lab
description: Operate the SpaceMiT K3 test bench through the Ubuntu jump host - preflight check, relay power on/off, serial console logging, TFTP-boot a chosen kernel Image, SSH-inspect the board, and shut down cleanly. Use when asked to boot, reboot, power cycle, connect to the K3 board or debug its serial console.
---

# K3 实验台现场操作

负责「把板子开起来 / 关掉 / 看串口 / 换镜像启动 / 进去看看」这一层，
不负责跑分（那是 `k3-benchmark`）。所有远端动作都经跳板机执行。

拓扑：

```
本地(agent) ──SSH──> 跳板机 Ubuntu Bastion Host ──UART+继电器──> SpaceMiT K3 板卡
                                                   └──有线网口 192.168.137.1 ──> 板卡 192.168.137.200
```

## 0. 配置（只做一次）

配置分两层，**分界就是 git**：

| 文件 | 内容 | 入 git |
| --- | --- | --- |
| `config/k3-auto.toml` | K3 板卡、串口、局域网、测试参数、脚本名 | ✅ |
| `config/jump.toml` | 跳板机 IP/用户/密码/sudo 密码/仓库绝对路径 | ❌ |

```bash
cp config/k3-auto.example.toml config/k3-auto.toml   # 通常不用改
cp config/jump.example.toml   config/jump.toml       # 填跳板机信息
$EDITOR config/jump.toml
```

必须先确认/填写：

- `[jump] host/user/password`：跳板机 SSH 目标。
- `[jump] repo`：跳板机上的实验仓库路径（内含 `k3-power.sh`、`minicom-log.sh`、
  `tmp/k3_*.py`）。
- `[serial] relay/debug`：设备号可能随插拔顺序变化。
- `config/k3-auto.toml` 里的板卡地址/局域网/`tftp_root`/`log_dir`/`monitor_seconds`。

查看生效配置（密码打码）：`python3 scripts/k3ctl.py show-config`

认证：本机有 `sshpass` 时用配置里的密码；没有则回退 SSH key/agent。
本仓库用户级安装 sshpass（无 root）：

```bash
tmp=$(mktemp -d) && cd "$tmp" && apt-get download sshpass \
  && dpkg-deb -x sshpass_*.deb ~/.local/opt/sshpass \
  && ln -sf ~/.local/opt/sshpass/usr/bin/sshpass ~/.local/bin/sshpass
```

### 权限模型（实测踩坑）

跳板机 sudoers 只对脚本的**绝对路径**免密：

```
(root) NOPASSWD: <repo>/k3-power.sh on
(root) NOPASSWD: <repo>/k3-power.sh off
(root) NOPASSWD: <repo>/k3-minicom.sh *
```

所以 `sudo -n ./k3-power.sh off` 会报「需要密码」，`sudo -n /abs/.../k3-power.sh off` 才行。`k3ctl` 会自动把 `[scripts]` 里的相对路径展成
`<jump.repo>/<script>` 绝对路径，上下电/串口因此不需要 sudo 密码。
其他 sudo（拷 Image 到 `/srv/tftp`）用 `[jump] sudo_password`，走 `sudo -S`。

### 板卡 SSH 密码认证

跳板机没有 sshpass，但 `k3ctl` 每次操作前会把 `scripts/board_ssh.py` 写
到跳板机 `/tmp/k3-auto/`，按 `sshpass → pexpect → 裸 ssh` 优先级嗂密码，
不修改跳板机环境（仓库自带 runner 也是用 pexpect 的）。

所有命令都支持 `-n/--dry-run` 先看要执行什么。

## 1. 启动前预检

```bash
python3 scripts/k3ctl.py status      # 只读快照，见 k3-status skill
python3 scripts/k3ctl.py jump -- ls -l /dev/ttyUSB*
python3 scripts/k3ctl.py runners     # 确认没有别人在跑
```

要点：

- `/dev/ttyUSB0` 继电器、`/dev/ttyUSB1` 调试串口（以实际枚举为准）。
- 串口被别的 minicom 占用时不要重复打开；用 `tail -f` 看已有日志。
- 确认没有残留 runner 再开始，避免两个流程抢串口/电源。

## 2. 上下电

```bash
python3 scripts/k3ctl.py power off
python3 scripts/k3ctl.py power on
```

跳板机上执行的是 `sudo -n <repo>/k3-power.sh on|off`。

- 用 `sudo -n`：跳板机需要免密 sudo；若报需要密码，先在跳板机配置 sudoers，
  不要改成交互式 `sudo`（agent 会卡住）。
- 标准节奏：先 `off`，确认继电器断开后再 `on`。
- 流程结束务必 `off`。

## 3. 串口日志

自动化 runner 自己会起 `minicom-log.sh`；**不要**在同一串口另开 minicom。

```bash
# 单独采集：<前缀> [--logdir logs-baseline]；默认后台，回显日志路径
python3 scripts/k3ctl.py serial-log <prefix>
python3 scripts/k3ctl.py serial-log <prefix> --foreground   # 直接看串口
python3 scripts/k3ctl.py serial-owner                        # 谁占着串口（带 sudo）
python3 scripts/k3ctl.py serial-stop                         # 停掉后台采集
python3 scripts/k3ctl.py tail <prefix>     # 跟踪最新日志
python3 scripts/k3ctl.py logs              # 看最近 3 个日志尾部
```

后台采集内部用 `TERM=xterm setsid script -qec ./minicom-log.sh ... /dev/null`：
minicom 必须有 pty 才能跑，`script` 负责造一个并脱离 ssh 会话。
不这样做会直接报 `没有 termcap 条目用于 unknown` 并退出。

关 minicom 用 `pkill -x minicom`（精确匹配进程名）：若用 `pkill -f 'minicom -D ...'`，
模式串会匹配到承载它的 `sudo` 自己的命令行，可能把自己杀掉；且 minicom 会拦
SIGTERM，所以 `serial-stop` 是 TERM → 2s → KILL → 复查。

**关于 `status` 里的 `[SERIAL_OWNER_FUSER]`**：跳板机上有 `fuser`（`psmisc` 已装），
但 minicom 以 **root** 运行，普通用户看不到 root 进程的 fd，所以该段经常是空的——
这**不代表串口空闲**。要准确结果用 `k3ctl serial-owner`（带 sudo），
或以 `[HOST_RUNNERS_AND_SERIAL]` 的 `ps` 输出为准。

日志落在跳板机 `[lab] log_dir` 下，命名 `<前缀>-<年月日_时分秒>.log`。
需要人工敲命令时用交互式 minicom（退出：`Ctrl+A` 然后 `X`）：

```bash
python3 scripts/k3ctl.py minicom
```

## 4. TFTP 启动指定 Image

```bash
python3 scripts/k3ctl.py tftp-put /path/to/Image-xxx
python3 scripts/k3ctl.py boot Image-xxx [--prefix inspect-board]
```

- `tftp-put` 流式上传到跳板机 `/tmp`，再 `sudo -n mv` 到 `[lab] tftp_root`
  （默认 `/srv/tftp`），最后 `ls -l` 回显确认。DTB 不从 TFTP 下载，用板载存储里的。
- `boot` 等价于 `K3_IMAGE=... K3_LOG_PREFIX=... K3_LOG_DIR=... tmp/k3_boot_setup.py`：
  下电 → 起串口日志 → 上电 → U-Boot 按 `s` → `setenv knl_name <Image>` →
  `run boot_tftp` → 登录 → 保持约 15 分钟便于人工/SSH 检查 → 自动下电。
- 手工 U-Boot 流程见 `AUTOLINK_K3_BENCHMARK_GUIDE.md` 第 3 节。

## 5. SSH 进板卡检查

```bash
python3 scripts/k3ctl.py board -- uname -a
python3 scripts/k3ctl.py board -- 'zcat /proc/config.gz | grep -E "CONFIG"'
python3 scripts/k3ctl.py board -- 'ps -eo pid,ppid,stat,etime,time,cmd | grep -E "UnixBench|multi.sh|tst.sh|lmb"'
python3 scripts/k3ctl.py board -- 'df -h; free -h'
```

- 板端账号为 `bianbu`；密码只放在未入库的 `config/jump.toml` `[board] password`。
  U-Boot 阶段没有 SSH，只能走串口。
- 密码由跳板机上的 `board_ssh.py` 处理（sshpass 或 pexpect）；
  探测脚本 `probe.sh` 也复用同一 helper（环境变量 `K3_BOARD_SSH_HELPER`）。

## 6. 收尾

```bash
python3 scripts/k3ctl.py runners            # 应无输出
python3 scripts/k3ctl.py serial-stop        # 停掉后台 minicom（若有）
python3 scripts/k3ctl.py serial-owner       # 确认串口不再被占
python3 scripts/k3ctl.py power off
python3 scripts/k3ctl.py status             # SSH 失败 + 日志显示关机 = 已下电
```

判定下电要谨慎：ping/SSH 失败本身不能证明已断电，需结合继电器状态或串口日志。

## 参考

- 全流程细节与逐条命令：`AUTOLINK_K3_BENCHMARK_GUIDE.md`
- 只读探测语义：`skills/k3-status/SKILL.md`
- 跑分编排：`skills/k3-benchmark/SKILL.md`
