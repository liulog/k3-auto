---
name: k3-benchmark
description: Execute authorized K3 benchmark tasks and assess their evidence, including image identity, requested copy counts, raw results, anomalies and cleanup. Use for UnixBench or LMbench tests and result review.
---

# K3 基准测试

从实验 JSON/用户请求取得 Image 身份、测试 prompt、copies、rounds 与频率要求，RUN_ID 使用执行器分配值。只查询内核或内存时使用 k3-status，不启动 benchmark。连接信息按 [配置说明](../../README.md#任务-json-与工具配置) 由工具读取本轮配置；工具及证据说明见 [手册](../../AUTOLINK_K3_BENCHMARK_GUIDE.md)。

1. 核对任务范围、现有工具支持和资源所有权。一次只运行一个任务；每次启动只给一个 RUN_ID，不为新镜像生成/修改执行脚本。
2. 使用已审查的固定执行器。`k3ctl unixbench/lmbench` 是旧 runner 接口，不能仅因能调用就认为支持 JSON 中的 copies、频率或 lease 门禁；不支持时报告缺口，不能改用 runner 默认值。
3. 运行时保留串口、板端原始日志和任务身份。panic、认证/身份/频率/证据失败、资源冲突或状态未知均停止推进；SSH 超时不授权停 runner、下电或重试。
4. 同时核验 status=success、exit-code=0、请求的全部 copy 数成绩、异常扫描、原始结果、Image/config/Build ID、要求的频率与恢复状态、自己占用资源的释放证据。不能仅看完成标记或 agent 总结。
5. 将每项结果、退出码与原始证据路径写入任务指定的输出目录，再报告成功/失败/未验证。无基线不声称性能提升；仅缺部分成绩应标为 partial/failed。

轮数与 copy 数以当前请求为准，不固定追加 1-copy/16-copy。禁止自动扩大构建、安装、刷写或重试范围；LMbench 的 `make rerun` 等可能触发构建，须有明确授权。只清理自己创建的资源，不把杀本地 SSH 当成板端取消。
