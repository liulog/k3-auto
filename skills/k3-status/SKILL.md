---
name: k3-status
description: Read-only quick checks of K3 reachability, debug serial ownership, running kernel image and benchmark. Use when asked whether K3 is on, busy, or which image/test is running; does not boot or manage experiments.
---

# K3 状态探测

执行一次只读快照，不轮询、不写远端文件、不碰电源与串口。适合回答
「板子现在活着吗 / 在跑哪个镜像 / 有没有在跑测试」。

## 用哪种方式跑

两种等价入口，按认证条件选：

| 条件 | 入口 |
| --- | --- |
| 仓库里有 `config/k3-auto.toml`（推荐），跳板机可用密码或 key 登录 | `python3 scripts/k3ctl.py status` |
| 只有 SSH key/agent 可用，或需要自定义目标 | `python3 skills/k3-status/scripts/probe.py` |

`k3ctl status` 会把本 skill 的 `scripts/probe.sh` 流式送到跳板机执行，
参数全部来自配置；`probe.py` 是自带参数、无机器配置的便携版本。

## 执行

```bash
# 方式一：读配置（config/k3-auto.toml 或 $K3_AUTO_CONFIG）
python3 scripts/k3ctl.py status

# 方式二：显式参数（key 认证；Windows/macOS/Linux 客户端均可）
python3 skills/k3-status/scripts/probe.py \
  --host jump-user@jump-ip \
  --serial /dev/ttyUSB1 \
  --board bianbu@192.168.137.200 \
  --address 192.168.137.200
```

`probe.py` 参数均可省略说明：

- `--host` 省略表示本地就是实验主机（Linux）。
- `--board` 省略只探测主机串口和日志。
- `--address` 省略不做 ping。
- `--log <串口日志路径>` 显式指定日志；否则只从占用该串口的 minicom `-C` 参数推断。
- 不打开、不读取串口设备本身。

`probe.py` 使用 `BatchMode=yes` 与 `StrictHostKeyChecking=yes`，禁止交互式
密码提示；适合 key 认证场景。需要密码认证时用 `k3ctl status`，它会：

1. 把 `scripts/board_ssh.py` 写到跳板机 `/tmp/k3-auto/`；
2. 用环境变量 `K3_BOARD_SSH_HELPER` 把 `probe.sh` 的板卡 SSH 指向该 helper
   （helper 内按 `sshpass → pexpect → 裸 ssh` 选，密码来自 `K3_BOARD_PASSWORD`）。

未建立信任或认证失败时报告未知，不自动改配置、不携带 askpass。

## 输出解释

输出简短中文摘要：

- 开机/连接：SSH 成功表示 Linux 已启动；仅 ping 成功表示在线，可能仍在
  U-Boot；都失败只能说不可达，不能断言已下电。ICMP 失败不阻止 SSH 探测。
- 串口：PID、程序、日志路径。`fuser` 无权限或没输出不证明空闲（minicom 以 root
  运行，普通用户看不到 root 进程 fd），结合 ps；要确定结果用
  `k3ctl serial-owner`（带 sudo）或 `k3ctl serial-stop` 后再查。
- 镜像：当前采集日志最后一次 TFTP Filename/字节数与板端 uname。日志观察
  不等于 SHA256 身份验证；旧日志、重启或来源不明时不能宣称当前镜像已确认。
- 测试：板端实际进程为主，列 lmbench/UnixBench/lat_sig 等及可辨认的子测试；
  主机 runner 或日志标签仅辅助，不凭旧 campaign 状态判断正在运行。
- 无法取得的字段写未知；区分「没发现 benchmark」与「板卡安全空闲」。

严格只读：不 claim 串口、不 sudo、不 kill、不操作电源、不复制镜像、不启动
测试、不修改实验状态。探测不能替代启动前完整预检。

需要真正操作板卡时改用 `k3-lab` skill；需要跑基准测试改用 `k3-benchmark`。
