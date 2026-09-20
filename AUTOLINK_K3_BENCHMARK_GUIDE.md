# Autolink K3 与 Benchmark 测试手册

自动流水线使用 `k3-auto` 仓库的 `k3ctl`（`skills/k3-lab`）。
`<repo>` 指跳板机上的实验仓库路径，即 `config/jump.toml` 的 `[jump] repo`。

## 1. 硬件与网络前提

```bash
ls /dev/ttyUSB*
```

默认设备：

| 设备 | 用途 | 参数 |
| --- | --- | --- |
| `/dev/ttyUSB0` | USB 继电器上下电 | 9600 |
| `/dev/ttyUSB1` | 板卡调试串口 | 115200 |
| 主机有线网口 | 与板卡直连 | `192.168.137.1` |
| 板卡 U-Boot / Linux | SSH 与 TFTP | `192.168.137.200` |

串口设备号可能因 USB 插入顺序变化，执行前先确认。继电器配置在
`config.json`。

## 2. 上电、下电与 minicom

### 2.1 上下电

Codex 环境不要直接依赖需要交互输入密码的 `sudo python3` 或
`sudo minicom`。使用仓库里的免密包装脚本：

```bash
sudo <repo>/k3-power.sh off
sudo <repo>/k3-power.sh on
```

通常测试开始前先 `off`，确认继电器断开后再 `on`。

### 2.2 交互式 minicom

```bash
sudo <repo>/k3-minicom.sh
```

退出 minicom：

```text
Ctrl+A，然后 X
```

### 2.3 自动保存 minicom 日志

```bash
./minicom-log.sh <日志前缀>
```

默认等价于：

```bash
./minicom-log.sh <日志前缀> /dev/ttyUSB1 115200 logs
```

第 3 个参数既可以传波特率（纯数字），也可以直接传日志目录；
第 4 个参数固定为日志目录。也可以用环境变量 `K3_LOG_DIR` 指定：

```bash
./minicom-log.sh <日志前缀> /dev/ttyUSB1 logs-baseline
./minicom-log.sh <日志前缀> /dev/ttyUSB1 115200 logs-baseline
K3_LOG_DIR=logs-baseline ./minicom-log.sh <日志前缀>
```

日志保存到：

```text
<日志目录>/<前缀>-<年月日_时分秒>.log
```

自动化 runner 会启动 `minicom-log.sh`，因此不要在同一个串口上再手动打开
另一个 minicom。需要人工观察时，优先 `tail -f` 对应日志文件。

## 3. 通过 TFTP 启动指定 Image

### 3.1 准备 Image

将镜像放到 TFTP 根目录：

```bash
sudo cp /path/to/Image-xxx /srv/tftp/
ls -l /srv/tftp/Image-xxx
```

TFTP 服务根目录是 `/srv/tftp`。DTB 不从 TFTP 下载，而是使用板载存储中已有的
DTB。

### 3.2 手工 U-Boot 流程

1. 连接并保存串口日志。
2. 先下电：

   ```bash
   sudo <repo>/k3-power.sh off
   ```

3. 上电：

   ```bash
   sudo <repo>/k3-power.sh on
   ```

4. U-Boot 出现 `Autoboot in N seconds` 时，按 `s` 跳过自动启动。
5. 进入 U-Boot 命令行后执行：

   ```text
   setenv knl_name Image-xxx
   run boot_tftp
   ```

6. 等待 Linux 登录提示：

   ```text
   login: bianbu
   Password: `<板卡密码，见本地未入库配置>`
   ```

实际测试脚本会自动完成上述步骤。

### 3.3 只启动板子做检查

`tmp/k3_boot_setup.py` 可以启动指定 Image，登录后让板子保持约 15 分钟，
然后自动下电：

```bash
K3_IMAGE=Image-xxx \
K3_LOG_PREFIX=inspect-board \
/usr/bin/python3 tmp/k3_boot_setup.py
```

它适合登录后通过 SSH 检查文件、配置或残留结果。

## 4. UnixBench 自动化

UnixBench runner：

```text
tmp/k3_unixbench_evidence.py
```

### 4.1 单次启动、单次 UnixBench

每次调用只传一个 `RUN_ID`，确保“一次启动只跑一次 UnixBench”：

```bash
cd <repo>

K3_IMAGE=Image-xxx \
K3_RUN_IDS=<RUN_ID> \
K3_LOG_PREFIX=<RUN_ID> \
K3_STATUS_PATH="$PWD/logs-baseline/<campaign>.json" \
K3_LOG_DIR=logs-baseline \
K3_MONITOR_SECONDS=600 \
/usr/bin/python3 tmp/k3_unixbench_evidence.py
```

`K3_LOG_DIR` 同时决定控制台日志目录和 `K3_STATUS_PATH` 的默认目录
（默认仍是 `logs`）。

`K3_RUN_IDS` 支持逗号分隔多个 ID，但多个 ID 会在同一次启动中连续执行。
需要“一启动一测试”时，不要在同一个调用里放多个 ID。

runner 会执行：

1. 继电器下电。
2. 启动 `minicom-log.sh` 保存控制台日志。
3. 继电器上电。
4. 在 U-Boot 中按 `s`、设置 `knl_name`、执行 `run boot_tftp`。
5. 登录 `bianbu / <板卡密码>`（密码只放本地未入库配置）。
6. 检查内核配置。
7. 在板子执行：

   ```bash
   cd /home/bianbu && bash run_unixbench_evidence.sh <RUN_ID> <IMAGE>
   ```

8. 每 `K3_MONITOR_SECONDS` 秒检查一次控制台和异常标记。
9. 测试结束后下电。

### 4.2 多镜像、多轮测试

建议为每轮使用唯一 `RUN_ID`，例如：

```text
<campaign>-round1-baseline
<campaign>-round2-baseline
...
```

可直接顺序调用 runner：

```bash
run_one() {
  image="$1"
  run_id="$2"

  K3_IMAGE="$image" \
  K3_RUN_IDS="$run_id" \
  K3_LOG_PREFIX="$run_id" \
  K3_STATUS_PATH="$PWD/logs/<campaign>.json" \
  K3_MONITOR_SECONDS=600 \
  /usr/bin/python3 "$PWD/tmp/k3_unixbench_evidence.py"
}

run_one Image-baseline-0909  0909-round1-baseline
```

同一个 `K3_STATUS_PATH` 会保留已有 `run_id` 记录；追加轮次时使用新
`run_id` 即可。

### 4.3 启动后的配置检查

runner 默认执行：

```bash
zcat /proc/config.gz | \
grep -E 'CONFIG'
```

常见匹配：

```text
CONFIG=y
```

注意：

- 某些镜像没有独立的配置符号，不能仅凭单项缺失判定配置错误。

### 4.4 板端 UnixBench 结果位置

板端结果目录：

```text
/home/bianbu/unixbench-runs/<RUN_ID>/
```

关键文件：

| 文件 | 含义 |
| --- | --- |
| `controller.log` | 板端控制脚本全过程 |
| `unixbench/stdout.txt` | UnixBench 标准输出 |
| `unixbench/stderr.txt` | UnixBench 标准错误 |
| `unixbench/status.txt` | `success` 或 `partial` |
| `unixbench/exit-code.txt` | UnixBench 进程退出码 |
| `unixbench/anomaly-scan.txt` | 关键错误和分数摘要 |
| `unixbench/raw-results/` | 本次新生成的原始结果文件 |

如果 `raw-results/` 中没有文件，检查全局目录：

```text
/home/bianbu/unixbench/UnixBench/results/
```

UnixBench 原始结果通常包含：

```text
<host>-<date>-<n>
<host>-<date>-<n>.html
<host>-<date>-<n>.log
```

### 4.5 完成判定

不要只看控制台里的：

```text
__UNIXBENCH_DONE__ <RUN_ID> rc=0
```

`run_unixbench_evidence.sh` 可能在该标记返回 0 的同时，板端内部结果仍然是
`partial` 或缺少 `status.txt`。正确判定应同时满足：

```text
status.txt = success
exit-code.txt = 0
stdout.txt 中至少有一套完整 System Benchmarks Index Score
anomaly-scan.txt 中没有非预期异常
raw-results 或全局 results 中存在对应原始结果
```

### 4.6 异常监控

runner 监控这些关键词：

```text
Oops
Kernel panic
Unable to handle kernel
BUG:
WARNING:
page fault
access fault
RCU stall
hung task
```

常见现象处理：

| 现象 | 处理 |
| --- | --- |
| `Oops` / `Kernel panic` / `access fault` | 记录为 failed，保存 console log，继续下一轮 |
| 长时间无完成标记 | 先用 SSH 查进程、`stdout.txt`、`controller.log` |
| SSH 超时且控制台仍无日志 | 按板卡掉电/内核挂死处理，停止 runner 并下电 |
| `stack smashing detected` | 用户态栈保护失败，保存 core/backtrace，区分用户态内存破坏与内核影响 |
| 仅有 1-copy 分数 | 16-copy 未完成，不能当作完整 UnixBench |
| controller 无 `status.txt` | 到全局 `UnixBench/results/` 查找未复制的原始结果 |

正常 UnixBench 默认会同时包含 1-copy 和 16-copy 阶段。只有一段分数时，
整轮应记录为 `partial/failed`。

致命标记自动中止：`Kernel panic` / `Oops` / `Unable to handle kernel` /
`access fault` / `RCU stall` 会在下一个监控周期被判为 `failed`，随即尝试
板端 `poweroff`（等 5s）→ relay 下电，然后自动进入下一次，不会空等 8 小时。
`WARNING:` / `BUG:` / `page fault` 等只记录到 `anomalies`，不自动中止。

### 4.7 多轮驱动脚本

`tmp/run_baseline_unixbench.sh` 可按固定顺序连续跑多镜像多轮：每次启动只跑
一个 RUN_ID，日志写入 `logs-baseline/`，状态汇总到
`logs-baseline/baseline-unixbench.json`。适合无人值守批量测试。

## 5. LMbench 自动化

LMbench runner：

```text
tmp/k3_lmbench_0907.py
```

调用方式：

```bash
cd <repo>

K3_IMAGE=Image-xxx \
K3_RUN_ID=<RUN_ID> \
K3_LOG_PREFIX=<RUN_ID> \
K3_STATUS_PATH="$PWD/logs-baseline/<campaign>-lmbench.json" \
K3_LOG_DIR=logs-baseline \
K3_MONITOR_SECONDS=600 \
/usr/bin/python3 tmp/k3_lmbench_0907.py
```

runner 会：

1. 下电、启动 minicom 日志、上电。
2. TFTP 启动指定 Image。
3. 登录并检查基础内核配置。
4. 确认板端存在 `/home/bianbu/kernel-bench/lmbench-master`。
5. 执行：

   ```bash
   cd /home/bianbu/kernel-bench/lmbench-master && make rerun
   ```

6. 等待：

   ```text
   __LMBENCH_DONE__ rc=0
   ```

7. 完成后下电，并写入状态 JSON。

板端 LMbench 结果通常位于：

```text
/home/bianbu/kernel-bench/lmbench-master/results/
```

## 6. 主机侧日志与状态 JSON

主机侧控制台日志：

```text
logs/<K3_LOG_PREFIX>-<年月日_时分秒>.log
```

状态 JSON：

```text
logs/<campaign>.json
```

典型记录字段：

```json
{
  "run_id": "...",
  "image": "Image-xxx",
  "status": "complete",
  "anomalies": [],
  "exit_rc": 0,
  "collected": {
    "status": "success",
    "benchmark_exit_code": 0,
    "anomaly_scan": "..."
  },
  "console_log": "logs/....log"
}
```

建议追加字段：

```json
{
  "raw_result": "logs/.../result-file",
  "raw_log": "logs/.../result-file.log",
  "raw_parse": "1-copy complete; 16-copy incomplete"
}
```

## 7. SSH 检查开发板

板端用户：

```text
user: bianbu
password: `<板卡密码，见本地未入库配置>`
```

自动化脚本使用 pexpect 输入密码。人工检查时可直接：

```bash
ssh bianbu@192.168.137.200
```

常用检查：

```bash
hostname
uptime
zcat /proc/config.gz | grep -E 'CONFIG'
ps -eo pid,ppid,stat,etime,time,cmd | \
  grep -E 'UnixBench|multi.sh|tst.sh|run_unixbench|lmb'
```

## 8. 测试结束后的收尾

1. 检查状态 JSON。
2. 检查板端 `status.txt`、`exit-code.txt`、`anomaly-scan.txt`。
3. 检查 `raw-results/`，必要时检查全局 `UnixBench/results/`。
4. 保存控制台日志和原始结果。
5. 确认没有 runner 或 minicom 进程：

   ```bash
   pgrep -af "k3_unixbench_evidence.py|k3_lmbench_0907.py|minicom"
   ```

6. 确认板子已下电：

   ```bash
   sudo <repo>/k3-power.sh off
   ```

## 9. 最小操作清单

```text
1. ls /dev/ttyUSB*，确认 ttyUSB0 是继电器、ttyUSB1 是调试串口
2. 将 Image 放入 /srv/tftp
3. power off
4. minicom-log.sh <prefix>
5. power on
6. U-Boot 按 s
7. setenv knl_name <Image>
8. run boot_tftp
9. 登录 bianbu / <板卡密码>
10. 检查 /proc/config.gz
11. 执行 run_unixbench_evidence.sh 或 LMbench
12. 每 10 分钟检查 console 和参数异常
13. 完成后核对板端 status、exit-code、raw-results
14. 保存 JSON 和日志
15. power off
```
