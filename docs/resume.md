# 简历数据表（面试口径）

> 最终数字来自 `results/real/` 三连跑（2026-10-23）。
> 口径：deepseek-chat、29 条真实 bugfix 任务（8 个开源仓库的真实修复提交）、本地环境（Docker 模式代码路径相同）、成功判据 = agent 结束后执行任务测试命令退出码 0。三组配置使用完全相同的最终版代码（版本公平性说明见 REPORT.md Block 6）。

## 最终数字

| 配置 | 解决 | 成功率 | 平均步数 | 成本 |
|---|---|---|---|---|
| 完整流水线（repo map + 上下文预算） | 21/29 | **72.4%** | 17.2 | ¥7.49 |
| 无 repo map | 21/29 | 72.4% | 17.4 | ¥7.16 |
| 无上下文压缩 | 21/29 | 72.4% | 17.1 | ¥6.21 |

- 失败归因（三组合计 24 个失败任务）：loop_detected 9、no_progress 7、max_steps 6、premature_done 1、policy_blocked 1
- 单任务成本约 ¥0.21–0.26（三组评测总计 ¥20.9；含开发期调试总计约 ¥40）

## 简历 bullets（直接用）

- 从零实现代码修复 agent harness（Python 3.12）：ReAct 主循环 + 三重预算（token/步数/墙钟）+ **进程树级超时强杀** + 工具层统一 JSON Schema 校验与结构化错误回传（ToolTimeout/ToolError/ToolUsageError/PolicyDenied），36 项单元测试。
- 构建 **29 条真实 bugfix 评测集**（8 个开源仓库的真实修复提交，SWE-bench 式测试补丁分离 + 任务元数据可追溯），配套**质量门禁**（每条任务验证「修复前必失败 → 打上源码补丁必通过」，29/29 通过）；完整流水线成功率 **72.4%**（21/29）。
- 上下文工程：tree-sitter 符号图 repo map（PageRank 式相关性排序 + token 预算，子进程隔离）+ 观察分级落盘 + 选择性淘汰 + LLM 摘要压缩；三组消融（±map、±压缩）成功率均为 72.4%，任务级差异 2+2（模型方差范围内）——**诚实结论：29 个单文件级任务上机制无显著增益，瓶颈在模型本身**（这也是可复现、可辩护的实验结果）。
- 可靠性：崩溃安全断点续跑（状态快照 + 恢复单测）、**两级循环熔断**（3 次重复警示介入、6 次强杀）、**验证驱动早停（仅编辑步骤后触发，降低验证频率与测试污染）**、任务级失败重试与隔离（API 波动零崩溃）、危险命令策略 + **工作区越界拦截**（观测到 agent 尝试 `cd` 出工作区扫描宿主磁盘，加策略层拦截并反馈纠正）。
- 可观测：全链路结构化 trace（每步 token/耗时/工具结果）+ 回放查看器 + 失败自动归因（6 类）。
- Docker 每任务一次性容器沙箱（--network none + 资源上限）；GitHub Actions CI。

## 面试可能追问 & 对应证据

| 问题 | 证据位置 |
|---|---|
| 评测集可信吗？ | scripts/validate_dataset.py（29/29）+ 任务元数据 base_commit/fix_commit/test_files/src_files |
| repo map 真有用吗？ | results/real/COMPARISON.md：成功率无差异 + 任务级 2+2 方差，诚实结论 |
| 上下文压缩省了多少？ | nocompact 对比 + obs/evictions.jsonl（成本 7.49→6.21 系方差，非压缩增益，如实说） |
| agent 跑飞了怎么办？ | loop_detected / no_progress / 三重预算 / trace 回放 |
| 崩溃了能恢复吗？ | --state 断点续跑 + tests/test_m5.py |
| 安全怎么做？ | 越界拦截（真实事件）+ DockerEnv 隔离 + 危险命令策略 |
| 哪些是你自己写的？ | REPORT.md 借代码清单（逐笔标注来源/许可证/改动方式） |
