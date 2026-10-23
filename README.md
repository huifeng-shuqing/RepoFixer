# RepoFixer — 可评测的代码仓库修复 Agent

给一个任务描述（issue/需求）+ 一个 git 仓库，RepoFixer 自主读代码、定位、修改、跑测试、迭代修复，全程结构化留痕、可评测、可断点续跑。

GitHub: <https://github.com/huifeng-shuqing/RepoFixer>

## 为什么做这个项目

大多数 agent demo 只能「跑一次看看」；RepoFixer 围绕**评测闭环**设计：每一步都有结构化 trace、每一个任务都有客观成功判据（测试通过）、每一个机制都有消融数字。骨架与设计参考了开源世界的成熟做法，借用的每一项都在 [REPORT.md](REPORT.md) 里注明来源与许可证。

## 核心能力

| 能力 | 实现 |
|---|---|
| 评测闭环 | 29 条真实 bugfix 任务（8 仓库 × 真实修复提交）+ 质量门禁 29/29 + 断点续跑 runner + 指标面板 |
| 上下文工程 | tree-sitter 符号图 repo map（相关性排序 + token 预算）+ 观察分级 + 选择性淘汰 + LLM 摘要压缩 |
| 可靠性 | 断点续跑（状态快照）、循环检测（同调用熔断 + 探测循环判别）、验证驱动早停、进程树级超时强杀 |
| 安全 | 危险命令策略（auto 拒绝 / ask 交互审批）、Docker 一次性容器沙箱（--network none + 资源上限） |
| 可观测 | 每步 token/耗时/工具结果 JSONL trace + 回放查看器 + 失败自动归因 |

## 架构

```
CLI (main.py)
  ├─ eval      # 评测 runner：数据集 → 每任务独立环境 → 指标汇总（可断点续跑）
  ├─ map       # repo map 子进程（tree-sitter 隔离，见 REPORT.md）
  └─ run       # 单任务修复
       └─ agent loop (loop.py)      # 线性消息累积 + 三重预算 + done 终止 + 守卫
            ├─ LLM 客户端 (llm.py)   # OpenAI 兼容端点（DeepSeek/GLM/Qwen/GPT）
            ├─ 工具注册表 (tools.py) # read/write/edit/bash/grep/run_test/done
            │    └─ 执行环境 (environment.py)
            │         ├─ LocalEnv   # Git Bash（POSIX 兼容）+ 路径白名单 + 树级超时
            │         └─ DockerEnv  # 每任务一次性容器：无网络、资源上限、用完即删
            ├─ 上下文 (context.py)  # 分级落盘 / 选择性淘汰 / LLM 摘要压缩
            └─ trace (trace.py)     # JSONL，每步 flush（崩溃安全）
```

## 快速开始

```bash
uv sync                                     # 安装依赖
cp .env.example .env                        # 填 DeepSeek API key（.env 已 gitignore）
uv run pytest                               # 35 项单元测试
uv run python scripts/smoke_local.py        # 冒烟：修复样例仓库的 bug
uv run repofixer run "任务描述" -w <仓库路径> --env docker   # 单任务修复（Docker 沙箱）
uv run repofixer eval --dataset data/tasks_real.jsonl --env local --out-dir results/run1
uv run python scripts/show_trace.py results/run1/traces/<task_id>.jsonl   # 回放
```

## 评测数据

- 数据集：`data/tasks_real.jsonl`（29 条，8 个开源仓库的真实修复提交；SWE-bench 式测试补丁分离，见 [scripts/build_dataset.py](scripts/build_dataset.py)）
- 质量门禁：`uv run python scripts/validate_dataset.py` → 29/29「修复前必失败、打上源码补丁必通过」
- 结果：见 [results/real/](results/real/)（full / nomap / nocompact 三组对比 + 失败归因 FAILURES.md）

## 文档

- [DEV_PLAN.md](DEV_PLAN.md) — 开发方案与里程碑
- [REPORT.md](REPORT.md) — 实现记录 + 借代码清单（来源/许可证/改动方式）
- [docs/resume.md](docs/resume.md) — 简历 bullets 与面试问答映射
- [docs/demo.md](docs/demo.md) — 3 分钟演示指南

## License

MIT（本仓库）；借用的开源代码/思想见 REPORT.md 清单。
