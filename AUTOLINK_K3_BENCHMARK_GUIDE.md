# K3 操作与证据参考

连接信息统一见 [配置说明](README.md)。以下命令是在用户授权对应操作后使用的接口示例，不是自动执行清单。均在仓库根目录运行；`--config`、`--dry-run` 等全局选项放在子命令之前。工具预览会遮盖密码，仍可能包含现场路径和地址。

## 状态与资源

- `python3 scripts/k3ctl.py status`：一次状态快照；会在跳板机临时目录更新 helper。
- `python3 scripts/k3ctl.py runners`：查看主机 runner/串口进程。
- `python3 scripts/k3ctl.py serial-owner`：带 sudo 的串口占用检查；普通用户 fuser 空输出不能证明空闲。
- `python3 scripts/k3ctl.py board -- uname -a`：板端检查；uname 不是镜像哈希证明。

启动/恢复之前，需要 root 可见串口、主机及板端任务、owned/absent lease 和 runner.pid 证据。k3ctl 没有完整 lease 管理：缺证据或发现占用就停止，不能接管他人资源。SSH/ping 失败仅代表不可达，不证明下电或任务结束。

## 上传、启动、电源与串口

下电、上电及自有遗留锁处理使用 [固定 lease / safe-power 流程](skills/k3-lab/references/power-lease.md)。原 `power` 命令保留给旧调用者，不具备该流程的保护。

```bash
python3 scripts/k3ctl.py tftp-put /path/to/Image
python3 scripts/k3ctl.py boot Image --prefix RUN_ID
python3 scripts/k3ctl.py serial-log RUN_ID
python3 scripts/k3ctl.py tail RUN_ID
```

上传目标由 `[lab] tftp_root` 定义；上传后还应校验实际文件哈希。boot 调用配置的远端脚本，可能执行下电、串口日志、上电、U-Boot TFTP 启动和结束下电。调用前确认现场脚本版本、身份检查与用户允许范围一致。手工 U-Boot 的 `knl_name`/`boot_tftp` 依赖现场环境；不自行修改持久环境或刷写。

`power on|off`、`minicom`、`serial-stop` 都会改变现场状态。只对自己拥有且已授权的资源操作；runner 正在使用串口时读已有日志，不另开 minicom。没有安全状态证据时不要下电。停止本地 SSH 不等于停止远端任务。

电源/串口 sudoers 通常匹配绝对脚本路径；k3ctl 会以 `[jump] repo` 展开相对脚本路径。权限失败就报告，不自动修改 sudoers 或安装依赖。后台串口需要 pty，现有工具用 `script` 创建，勿另写一套驱动。

## Benchmark 接口及限制

```bash
python3 scripts/k3ctl.py unixbench Image RUN_ID --campaign CAMPAIGN
python3 scripts/k3ctl.py lmbench Image RUN_ID --campaign CAMPAIGN
```

这是旧固定 runner 接口，不含通用 copies/rounds/frequency 参数。使用前检查 runner 是否满足外层任务：不支持请求的能力就报告，不把其默认 1+16-copy 当成用户要求。不得为单个 Image 派生/修改 runner。LMbench runner 可能调用 `make rerun`，未明确授权构建时不可执行。

一次启动只给一个 RUN_ID，镜像与轮次串行。不要同时启动多个后台任务争用板卡。传给 runner 的变量包括 `K3_IMAGE`、`K3_RUN_IDS`/`K3_RUN_ID`、`K3_LOG_PREFIX`、`K3_STATUS_PATH`、`K3_LOG_DIR`、`K3_MONITOR_SECONDS`；具体取值来自当前任务与本地配置。

## 完成证据

必须关联同一个 RUN_ID 与 Image，核对：

1. `status.txt=success` 与 `exit-code.txt=0`。
2. stdout 覆盖请求的全部 copies/轮次，而不是固定要求额外的 copy 数。
3. anomaly-scan 没有预期外异常；对应 raw-results 完整保存。
4. Image 哈希、配置、Build ID 与实际启动身份吻合。
5. 如任务要求锁频，保存锁定、保持及恢复证据。
6. 自己的 runner、串口及 lease 已正确释放；未知时标为 needs_attention。

板端目录以现场 runner 配置和回执为准，不在 skill 内写死用户名/home。主机日志位于 `[lab] log_dir`，记录控制台日志、状态文件及原始结果路径。完成标记、进程退出 0 或 agent 自述都不能单独证明成功。缺成绩标 partial/failed；无基线不声称性能提升。

## 异常与收尾

panic/Oops、认证失败、镜像身份不符、频率或资源恢复失败：保存证据并停止推进。SSH 超时不授权停止 runner、下电或重试；未知板卡状态禁止电源恢复。通信恢复必须有单独授权、资源安全证明和持久重试预算。

仅清理自己创建的资源，保留旧 attempt 证据，不覆盖失败记录。按实际完成范围报告成功、失败和未验证项；是否最终下电遵循当前授权与资源状态，不把固定下电当通用收尾动作。
