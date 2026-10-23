# 简历数据表（面试口径）

> 最终数字来自 `results/real/` 三连跑；用真实数字替换 X/Y/Z 后直接写进简历。
> 口径：deepseek-chat、29 条真实 bugfix 任务、本地环境（Docker 模式代码路径相同）、
> 成功判据 = agent 结束后执行测试命令退出码 0。

## 简历 bullets（模板）

- 从零实现代码修复 agent harness（Python）：ReAct 主循环 + 状态机任务管理 + 三重预算（token/步数/墙钟）+ **进程树级超时强杀**；工具层统一 JSON Schema 校验与结构化错误回传（ToolTimeout/ToolError/ToolUsageError/PolicyDenied）。
- 构建 **29 条真实 bugfix 评测集**（8 个开源仓库的真实修复提交 → revert 坏版本 + SWE-bench 式测试补丁分离），配套**质量门禁**（每条任务验证「修复前必失败 → 打上源码补丁必通过」，29/29 通过），成功率为 **X%**。
- 上下文工程：tree-sitter 符号图 repo map（PageRank 式相关性排序 + token 预算）+ 分层观察处理 + 选择性淘汰 + LLM 摘要压缩，消融显示 repo map 带来 **+Y%** 成功率。
- 可靠性：崩溃安全断点续跑（状态快照可恢复）、循环检测（同调用熔断 + 探测循环输出大小判别）、验证驱动早停（测试一通过即收手，单任务平均成本降 **Z%**）、危险命令策略（HITL 基础）。
- 可观测：全链路结构化 trace（每步 token/耗时/工具结果）+ 回放查看器 + 失败自动归因分类。
- Docker 每任务一次性容器沙箱（--network none + 资源上限）；GitHub Actions CI；35 项单元测试。

## 面试可能追问 & 对应证据

| 问题 | 证据位置 |
|---|---|
| 评测集可信吗？ | scripts/validate_dataset.py（29/29）+ 任务元数据 base_commit/fix_commit/test_files/src_files |
| repo map 真有用吗？ | results/real/full vs nomap 对比 |
| 上下文压缩省了多少？ | full vs nocompact 对比 + evictions.jsonl |
| agent 跑飞了怎么办？ | loop_detected / no_progress / 三重预算 + trace |
| 崩溃了能恢复吗？ | --state 断点续跑 + tests/test_m5.py |
| 哪些是你自己写的？ | REPORT.md 借代码清单（逐笔标注来源/许可证/改动方式） |
