# 三连跑对比（真实 bugfix 数据集，deepseek-chat）

> 成功判据：agent 结束后执行任务测试命令退出码 0。数据集 29 条真实任务，质量门禁 29/29。

| 配置 | 任务数 | 解决 | 成功率 | 平均步数 | prompt tok | completion tok | 估算成本(¥) |
|---|---|---|---|---|---|---|---|
| full（完整流水线） | 29 | 21 | 72.4% | 17.2 | 3571498 | 43693 | 7.49 |
| nomap（无 repo map） | 29 | 21 | 72.4% | 17.4 | 3397369 | 45282 | 7.16 |
| nocompact（无上下文预算） | 29 | 21 | 72.4% | 17.1 | 2932025 | 43458 | 6.21 |

## 消融要点

- repo map 的贡献：full 解决而 nomap 未解决的任务 2 个：tabulate-5373ebfa, inflection-e32443bb
- nomap 反而更好的任务（模型方差）2 个：humanize-e46732ed, tabulate-8014ec66

## 诚实说明

- 单次运行、n=29：成功率差异受模型方差影响，结论表述为「趋势」而非「严格显著性」。
- 本地环境执行（Docker 模式代码路径相同，见 tests/test_docker.py）；成本按 deepseek-chat 刊例价估算。
- 失败模式归因见各 run 目录的 FAILURES.md。

