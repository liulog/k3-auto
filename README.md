# k3-auto

SpaceMiT K3 实验台的固定工具与三个精简 skill。连接信息在一份本地 TOML 中；实验的镜像、copies、rounds、prompt 等由外层任务提供。

```text
Agent → k3ctl → SSH 跳板机 → 串口/继电器/K3
```

## 一份配置

复制 `config/k3-auto.example.toml` 为 `config/k3-auto.local.toml`，设置文件权限为 `600`，填写 `[jump]`、`[board]`、`[serial]`、`[lan]`、`[lab]`。IP、账号、密码、sudo 密码、密钥路径和跳板机仓库路径都只维护在这份文件中。模板使用空连接字段；工具不再猜测板卡 IP 或用户名。

默认查找：`--config` → `$K3_AUTO_CONFIG` → 当前目录及祖先目录的 `config/k3-auto.local.toml`（兼容旧 `k3-auto.toml`）→ 本工具仓库配置 → `~/.config/k3-auto/config.toml`。

包含 `[jump]` 的单文件配置不会自动叠加旧 `jump.toml` 或 `$K3_JUMP_CONFIG`，避免连错目标。只有主配置没有 `[jump]` 时，才使用 `$K3_JUMP_CONFIG` 或主配置旁的 `jump.toml`；显式 `--jump-config` 仍可覆盖，用于兼容旧调用。

```bash
# 仅本地读取，密码只显示 <set>/<empty>；不连接设备
python3 scripts/k3ctl.py show-config

# 旧双文件迁移：不联网、不覆盖已有目标、保留原文件
python3 scripts/migrate-config.py \
  --base config/k3-auto.toml --jump config/jump.toml \
  --output config/k3-auto.local.toml
```

迁移合并规则与原 Config 相同：jump.toml 中的 `[jump]`、`[board]` 覆盖主配置对应字段。旧文件暂留给冻结的历史执行器使用，新任务只维护 local.toml。需要迁移旧调用者时单独切换，不能热改活动实验。

本地配置均被忽略，仓库只跟踪 example 模板。移除当前 Git 跟踪不清除历史版本；历史内容不在本次改写范围。真实密码只允许保存在不入库的本地配置（TOML 或外层任务的私有 JSON）中，不写入公开示例、skill、模型 prompt 或日志。`show-config` 会显示现场地址和用户名，但遮盖密码，不应直接发布其输出。

## 任务 JSON 与工具配置

外层任务提供 agent/model、prompt、skills、board、tests 和 outputDir；本仓库只提供工具和操作指导，不启动额外 build agent。

- 外层提供 `board.jump`、`board.ssh` 时，以本轮 JSON 为配置来源；连接层映射为权限 600 的临时 TOML，并通过 `--config` 显式传入。`jump` → `[jump]`，`ssh` → `[board]`，`identityFile` → `identity_file`，跳板 `workingDirectory` → `[jump].repo`。不让旧配置覆盖本轮地址或凭据。
- 当前 `k3ctl` **只读取 TOML，不直接读取实验 JSON**；上述自动转换仍需 workflow 连接层实现。没有转换能力时报告缺项，不能仅靠 skill 声称已接通。任务直接提供有效 TOML 路径时可使用现有工具。
- 当前板卡 SSH helper 固定使用端口 22，未传递板卡 `identity_file`；非默认板卡端口或显式板卡密钥要求不能静默忽略。电源/串口操作还需要现场串口与实验台配置，简单 SSH 查询不因此扩大操作范围。
- 在本机调用 `k3ctl board -- ...`，工具经跳板机访问板卡；跳板工作目录属于跳板机。凭据只交给连接层，不回显配置或含认证信息的传输命令。私有配置、原始日志不提交。
- 一个执行 agent 按 prompt 选择 skill 和工具，顺序执行任务，将每项输出、退出码和证据位置交给外层汇总。现场失败记录留在本轮输出目录。

## 工具与 skills

|入口|用途|
|---|---|
|`scripts/k3ctl.py`|读取配置并调用固定现场工具|
|`scripts/board_ssh.py`|跳板机侧板卡 SSH helper|
|`skills/k3-status`|单次状态快照|
|`skills/k3-lab`|授权的电源、串口、上传与启动操作|
|`skills/k3-benchmark`|按请求执行测试并核验证据|

`.pi/settings.json` 已声明 `skills` 目录。操作细节集中在 [操作手册](AUTOLINK_K3_BENCHMARK_GUIDE.md)，skill 只保留入口与关键约束。

`k3ctl` 是低层 CLI，不是生产调度器；它尚未统一实现板卡 lease、所有权与证据门禁，也不把任意实验 JSON 自动转成支持的 benchmark 参数。远端 runner 支持范围必须先核实。不能用本次配置/skill 重构宣称真实 workflow adapter 已完成。

## 离线验证

```bash
PYTHONDONTWRITEBYTECODE=1 python3 tests/test_config.py
```

测试仅用临时配置和 mock，不调用 SSH、串口、电源、编译器或 benchmark。

感谢 [0bluewhale0](https://github.com/0bluewhale0) 提供原始 k3-status。
