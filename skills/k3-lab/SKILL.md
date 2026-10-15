---
name: k3-lab
description: Operate the SpaceMiT K3 lab through its jump host, including authorized power control, serial logging, image upload and TFTP boot. Use for booting or inspecting the board; use k3-status for status-only requests and k3-benchmark for benchmark evidence.
---

# K3 实验台

在仓库根目录调用固定入口 `python3 scripts/k3ctl.py`。显式使用本轮工具配置；任务 JSON 的连接信息按 [配置说明](../../README.md#任务-json-与工具配置) 交给连接层，不另猜现场地址。密码不进入 prompt、命令参数、skill 或日志。操作细节见 [手册](../../AUTOLINK_K3_BENCHMARK_GUIDE.md)。

1. 按用户请求确定镜像和允许操作。下电、上电及自有遗留锁处理，按 [电源与 lease 流程](references/power-lease.md) 使用 `lease status/acquire/review/release` 和 `safe-power off/on/cycle`，不要临时拼接远程锁命令。
2. 只执行授权范围内的 `tftp-put`、`boot`、`safe-power` 或串口操作。`boot` 会调用远端 runner，可能上下电并在结束时下电；先核对现场脚本的行为与授权相符。
3. 观察已有日志，避免第二个 minicom 抢串口。只清理自己创建的资源；不能终止别人的 runner、抢 lease 或因 SSH 超时直接下电。
4. 报告实际完成的操作、镜像身份、日志位置、资源释放证据及未知项。

`lease`/`safe-power` 提供本轮电源操作的互斥与证据检查；旧 `power`、`boot` 和 benchmark 接口不因此自动获得完整 workflow 门禁。串口普通用户 `fuser` 无输出不代表空闲；SSH/ping 失败不代表已下电。未知状态停止推进，不自动重试。不得因权限问题修改 sudoers、安装软件或扩展构建/刷写范围。
