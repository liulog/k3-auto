# 持有 lease 的电源操作

仅在用户已授权本轮下电/上电或电源恢复时执行。使用本机 `k3ctl`，不拼接远端锁文件操作。操作期间沿用同一个 owner；不能借换 RUN_ID 重置失败状态或重试授权。

## 可照着执行的流程

在 k3-auto 根目录，将配置、RUN_ID 和型号替换为本轮值：

```bash
K3_TASK_CONFIG=/path/to/private.toml
K3_TASK_OWNER=power-check-unique-run-id
K3_TASK_MODEL=com260
k3task() { python3 scripts/k3ctl.py --config "$K3_TASK_CONFIG" "$@"; }
```

先运行 `k3task lease check --expected-model "$K3_TASK_MODEL"` 做只读预检，不取得 lease、不操作电源。失败时 JSON 中的 `stage` 区分跳板机/板卡，`code` 区分 sudo 认证、权限与资源问题。

1. `k3task lease status`：查看既有锁。无锁才能取得新锁；锁属于其他任务或来源未知就停止。有本轮旧锁时先按下文复核。
2. `k3task lease acquire --owner "$K3_TASK_OWNER" --expected-model "$K3_TASK_MODEL"`：原子取得锁。工具在取得前后检查跳板机 root 可见的串口、串口锁、runner，以及板端 root 可见的任务和型号。任一检查失败停止；如果已取得锁，保留现场待复核。
3. 用户授权完整断电再上电时：

   ```bash
   k3task safe-power cycle --owner "$K3_TASK_OWNER" --expected-model "$K3_TASK_MODEL" --wait 180
   ```

   工具在 relay 命令之前持久记录预留，执行一次 off，至少等待 30 秒，再执行一次 on。上电后只读等待 SSH 恢复，核对型号、不同于下电前的 boot ID、任务及串口空闲。默认等待 180 秒（最后一次连接探测可能另耗最多 30 秒）。认证、型号或资源检查失败立即停止；不会再次操作 relay。
4. 只授权下电时使用 `safe-power off`，参数同上但无需 `--wait`。完成后保留 lease，状态为 `off-acknowledged`，表示 relay 脚本成功返回，**不是独立测量的物理断电证明**。稍后得到上电授权，使用同一 owner 执行 `safe-power on ... --wait 180`。没有本工具记录的本轮 off，不能执行 on；初始已断电或未知状态需另行取得可靠证据，不能用 SSH 不通代替。
5. 正常完成并确认板卡在线后：

   ```bash
   k3task lease release --owner "$K3_TASK_OWNER" --expected-model "$K3_TASK_MODEL"
   ```

   工具再次检查 owner、无 runner.pid、板卡及资源状态，归档 journal 后释放锁。按任务输出目录保存每条命令的 JSON 输出、退出码和日志位置；检查退出码再执行下一步，不把全部命令无条件串起来。

## 锁格式与位置

跳板机全局路径 `/tmp/k3-agent-workflow-board.lock/`，沿用现有 workflow 的目录锁：

- `owner`：一行精确 RUN_ID，权限 600；目录以原子 mkdir 创建，权限 700。
- `runner.pid`：操作中的跳板机进程 PID。文件存在即拒绝下一次电源操作，不能因 PID 看似过期就删除。
- `power.json`：本工具的阶段、动作预留/确认时间、前后 boot ID、型号和内核观察；不包含密码。

新工具另以 `/tmp/k3-agent-workflow-board.guard` 的 flock 串行化命令，旧执行器仍通过原目录锁协调。正常释放后的 journal 保存在跳板机 `/tmp/k3-agent-workflow-board.lock.released-<owner>.json`；应在归档日志时收集，`/tmp` 不保证跨跳板机重启保留。不要按时间自动回收锁，未知文件或符号链接一律拒绝。

## 失败与自己遗留的锁

- 先 `lease status`，保存错误、owner、runner.pid 和 power 阶段；不自动清理、不下第二次电源命令，不杀其他任务。
- 只有任务记录能证明 owner 属于本轮、原操作已结束且用户允许复核时，执行 `lease review --owner ... --expected-model ...`。它通过 root 确认 PID 已不存在，检查板卡在线且无任务/串口占用；涉及电源的不确定状态还须确认新 boot ID。复核只更新证据、移除自己的过期 runner.pid，不操作电源。
- 复核通过后运行 `lease release`。没有 runner.pid 的旧格式自有锁可直接走 `release` 的完整检查；旧格式锁不能直接用来执行 safe-power，释放后需在授权范围内另开一轮。
- 原 PID 仍存活、PID 被复用、板卡不可达、boot ID 未变化、锁属他人、证据缺失时保持锁并报告 `needs_attention`。仅停止本地 SSH 不代表远端结束。不要 `rm -rf`、手写 owner 或删除别人的 runner.pid。

## 前提与边界

跳板机和板卡需已有可用的 Python 3 与 root 检查权限，跳板机还需 fuser。跳板机预检及遗留 PID 复核复用 `[jump].sudo_password`（未单独设置时沿用现有配置的 SSH 密码回退）：有密码时通过 stdin 使用 `sudo -S -p ""`，无密码时使用 `sudo -n`。不会弹交互提示或输出密码。板卡预检仅执行 root 所有的 `/usr/local/libexec/k3-auto-board-probe.py`，校验其内容哈希与仓库 `scripts/board_root_probe.py` 一致；板端 NOPASSWD 仅授权 `/usr/bin/python3 -I /usr/local/libexec/k3-auto-board-probe.py`，不授权任意 `python3 -c`。探针不接受参数。首次配置须由管理员写入 root 所有、普通用户不可写的探针和 sudoers 规则，并经 `visudo -c` 检查；不能直接覆盖未知已有文件。relay 仍通过已配置的 NOPASSWD 电源脚本。`SUDO_PASSWORD_REQUIRED` 表示缺少可用 sudo 认证，`SUDO_AUTH_FAILED` 表示认证失败，`SUDO_NOT_ALLOWED` 表示 sudo 策略拒绝；密码不能替代命令授权。缺权限或依赖时报告缺项，不修改 sudoers 或安装软件。配置选择错误、忽略 lease 的外部操作者及非协作 runner 不由此工具消除，运行前仍应确认所选资源。

原 `power on/off` 是兼容旧冻结执行器的低层接口，不带这些保护，新 agent 电源流程使用 `safe-power`。本流程不刷写、不修改 U-Boot、不运行 benchmark；内核版本与型号观察也不能单独证明特定 Image 已验证。
