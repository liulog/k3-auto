---
name: k3-benchmark
description: Run and judge UnixBench and LMbench campaigns on the SpaceMiT K3 board via the jump host - one image per boot, RUN_ID naming, result collection, completion criteria, anomaly handling and status JSON. Use when asked to benchmark K3, compare kernel images, or check whether a benchmark run really succeeded.
---

# K3 基准测试编排

负责 TFTP 起指定内核 → 板端跑 UnixBench / LMbench → 收集证据 → 判定成败。
上下电/串口底层动作由 `k3-lab` 提供，此处不重复。

## 1. 铁律：一次启动只跑一个 RUN_ID

runner 支持 `K3_RUN_IDS=a,b` 逗号分隔，但那是「同一次启动连续跑多个」。
要「一启动一测试」就**一次调用只给一个 RUN_ID**，否则中途一次异常会连坐
整段测试。多镜像多轮请顺序调用。

RUN_ID 命名建议：`<campaign>-round<N>-<配置组合>`，例如
`0909-round1-baseline`。

## 2. 跑一轮

```bash
# UnixBench
python3 scripts/k3ctl.py unixbench Image-xxx 0909-round1-baseline \
    --campaign baseline

# LMbench
python3 scripts/k3ctl.py lmbench Image-xxx 0909-round1-baseline-lmbench \
    --campaign baseline-lmbench
```

`k3ctl` 会补全并传入这些环境变量（跳板机上执行）：

| 变量 | 值 |
| --- | --- |
| `K3_IMAGE` | 镜像名（须已在 `/srv/tftp`） |
| `K3_RUN_IDS` / `K3_RUN_ID` | UnixBench 用前者，LMbench 用后者 |
| `K3_LOG_PREFIX` | 同 RUN_ID |
| `K3_STATUS_PATH` | `<log_dir>/<campaign>.json` |
| `K3_LOG_DIR` | `[lab] log_dir` |
| `K3_MONITOR_SECONDS` | 默认 600 |

runner 内部流程：继电器下电 → 起 `minicom-log.sh` → 上电 → U-Boot 按 `s`、
`setenv knl_name`、`run boot_tftp` → 登录 `bianbu/<板卡密码>` → 检查
`/proc/config.gz` → 板端执行 `run_unixbench_evidence.sh <RUN_ID> <IMAGE>`
（LMbench 为 `cd /home/bianbu/kernel-bench/lmbench-master && make rerun`）
→ 每 `K3_MONITOR_SECONDS` 秒扫异常 → 结束下电 → 写状态 JSON。

## 3. 多镜像多轮（无人值守）

顺序调用即可，同一 `--campaign` 共用状态 JSON，已有 `run_id` 记录会保留：

```bash
run_one() {   # $1=image  $2=run_id
  python3 scripts/k3ctl.py unixbench "$1" "$2" --campaign baseline
}
run_one Image-baseline-0909  0909-round1-baseline
```

跳板机仓库里已有等价脚本 `tmp/run_baseline_unixbench.sh`，可直接用；
用 `k3ctl` 的好处是镜像/TFTP/日志路径都来自一份配置。

跑起来后不要在同一串口开第二个 minicom，用：

```bash
python3 scripts/k3ctl.py tail <RUN_ID>
python3 scripts/k3ctl.py runners
```

## 4. 配置检查的正确读法

```bash
python3 scripts/k3ctl.py board -- 'zcat /proc/config.gz | grep -E "CONFIG"'
```

## 5. 完成判定（最容易出错的一步）

**不要**只看控制台里的 `__UNIXBENCH_DONE__ <RUN_ID> rc=0`——板端内部可能仍是
`partial` 或根本没有 `status.txt`。必须同时满足：

1. `status.txt` = `success`
2. `exit-code.txt` = `0`
3. `stdout.txt` 至少有一套完整 System Benchmarks Index Score
4. `anomaly-scan.txt` 无预期外异常
5. `raw-results/` 或全局 `results/` 里有对应原始结果

结果位置（板端）：

```
/home/bianbu/unixbench-runs/<RUN_ID>/controller.log
/home/bianbu/unixbench-runs/<RUN_ID>/unixbench/{stdout,stderr,status,exit-code,anomaly-scan}.txt
/home/bianbu/unixbench-runs/<RUN_ID>/unixbench/raw-results/
/home/bianbu/unixbench/UnixBench/results/          # 全局回退位置
/home/bianbu/kernel-bench/lmbench-master/results/  # LMbench
```

检查命令：

```bash
python3 scripts/k3ctl.py board -- 'cd /home/bianbu/unixbench-runs/<RUN_ID> && ls -R | head -40'
python3 scripts/k3ctl.py board -- 'cd /home/bianbu/unixbench-runs/<RUN_ID>/unixbench && cat status.txt exit-code.txt anomaly-scan.txt'
```

正常 UnixBench 同时有 1-copy 和 16-copy 两段。只有一段分数 → 记
`partial/failed`，不能当完整成绩。

## 6. 异常监控与停机策略

监控关键词：`Oops`、`Kernel panic`、`Unable to handle kernel`、`BUG:`、
`WARNING:`、`page fault`、`access fault`、`RCU stall`、`hung task`。

| 现象 | 处理 |
| --- | --- |
| `Kernel panic` / `Oops` / `Unable to handle kernel` / `access fault` / `RCU stall` | 致命：下一个监控周期判 `failed`，板端 `poweroff`（等 5s）→ 继电器下电 → 自动下一轮，不空等 |
| `WARNING:` / `BUG:` / `page fault` | 只记入 `anomalies`，不自动中止 |
| 长时间无完成标记 | 先 SSH 查进程、`stdout.txt`、`controller.log`，不要急着重启 |
| SSH 超时且控制台无输出 | 按内核挂死处理：停 runner 并下电 |
| `stack smashing detected` | 用户态栈保护失败，保存 core/backtrace，区分用户态内存破坏 vs 内核影响 |
| 只有 1-copy 分数 | 16-copy 未完成，记 `partial` |
| controller 没有 `status.txt` | 去全局 `UnixBench/results/` 找未复制的原始结果 |

## 7. 状态 JSON 与证据归档

主机侧产物：

```
<log_dir>/<RUN_ID>-<年月日_时分秒>.log   # 控制台日志
<log_dir>/<campaign>.json                # 汇总状态
```

`<campaign>.json` 每条记录包含 `run_id`、`image`、`status`、`anomalies`、
`exit_rc`、`collected`、`console_log`，建议补 `raw_result`、`raw_log`、
`raw_parse`（如 `1-copy complete; 16-copy incomplete`）。

## 8. 收尾清单

1. 检查状态 JSON 与每轮 `status/exit-code/anomaly-scan`。
2. 检查 `raw-results/`，必要时查全局 `results/`。
3. 保存控制台日志与原始结果，标注未验证项。
4. `python3 scripts/k3ctl.py runners` 确认无残留 runner/minicom。
5. `python3 scripts/k3ctl.py power off` 并确认已下电。

## 参考

- 完整手册：`AUTOLINK_K3_BENCHMARK_GUIDE.md`（第 4、5、6、8 节）
- 现场上下电/串口：`skills/k3-lab/SKILL.md`
- 只读状态语义：`skills/k3-status/SKILL.md`
