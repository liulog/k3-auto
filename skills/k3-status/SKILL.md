---
name: k3-status
description: Query K3 kernel version, memory, board model, reachability and running tasks through the jump host. Use for read-only board checks; does not boot the board or start benchmarks.
---

# K3 只读查询

按任务 prompt 选择查询，在 k3-auto 根目录调用 `scripts/k3ctl.py`。使用任务指定的配置路径；JSON 与工具配置的衔接见 [配置说明](../../README.md#任务-json-与工具配置)。不在 skill 中保存地址、账号或密码。

```bash
python3 scripts/k3ctl.py --config /path/to/private.toml board -- uname -r
python3 scripts/k3ctl.py --config /path/to/private.toml board -- uname -m
python3 scripts/k3ctl.py --config /path/to/private.toml board -- cat /proc/meminfo
python3 scripts/k3ctl.py --config /path/to/private.toml board -- cat /proc/device-tree/model
```

只执行任务要求的项目。`board` 自动经跳板机连接板卡，并在跳板机 `/tmp/k3-auto/` 更新 SSH helper；不要在本机直接查询后冒充板端结果。内存 kB 除以 1048576 得到 GiB；型号去除末尾 NUL。验收值来自 prompt，不写死内核版本或内存容量。

需要综合快照时使用同一配置的 `status`。其摘要和顶层退出码不能替代每项查询的输出、退出码；SSH 失败报告未知，ping 成功不等于 Linux 可用。uname 不能单独证明指定 Image 身份；普通用户 fuser 空输出不能证明串口无人占用。

逐项记录命令、退出码、原始输出、判定及日志位置到任务指定目录。只读查询不启动 benchmark、不打开串口、不操作电源；连接失败停止，不自动重试。工具不可用时报告缺口，不绕过 workflow 的工具限制。
