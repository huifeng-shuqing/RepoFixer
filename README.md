# RepoFixer — 可评测的代码仓库修复 Agent

[![CI](https://github.com/huifeng-shuqing/RepoFixer/actions/workflows/ci.yml/badge.svg)](https://github.com/huifeng-shuqing/RepoFixer/actions/workflows/ci.yml)

给一个任务描述 + 一个 git 仓库，RepoFixer 自主复现失败、读代码、定位、修改、跑测试、迭代修复，全程结构化留痕、可评测、可断点续跑。

GitHub: <https://github.com/huifeng-shuqing/RepoFixer>

## 为什么做这个项目

大多数 agent demo 只能「跑一次看看」；RepoFixer 围绕**评测闭环**设计：每一步都有结构化 trace、每一个任务都有客观成功判据（测试通过）、每一个机制都有消融数字。骨架与设计参考了开源世界的成熟做法（Plan-Act 双阶段、SWE-bench 评测方法论、OpenHands 式上下文压缩等），借用的每一项都在 [REPORT.md](REPORT.md) 里注明来源与许可证。

## 核心能力

| 能力 | 实现 |
|---|---|
| 评测闭环 | 29 条真实 bugfix 任务（8 仓库 × 真实修复提交）+ 质量门禁 29/29 + 断点续跑 runner + 指标面板 |
| 执行架构 | Plan-Act 双阶段：只读规划（工具白名单 + 计划文件 + 步数预算）→ 全工具执行，同一对话延续 |
| 上下文工程 | tree-sitter 符号图 repo map（相关性排序 + token 预算 + mtime 缓存）+ 观察分级落盘 + 选择性淘汰 + LLM 摘要压缩 |
| 停滞止损 | 三级熔断：重复动作（3 警示/6 强杀）、探测循环（输出大小判别）、只读停滞（10 提醒/15 强制） |
| 质量把关 | 验证驱动早停（编辑步骤后触发）、终验三跑多数决（防 flaky 测试）、独立上下文复审（长任务/出错时触发） |
| 长期记忆 | 仓库级笔记：会话开始注入、失败任务经 LLM 抽取持久事实并去重合并 |
| 检查点 | 每步影子快照（`git stash create`，不动 HEAD）+ `repofixer rollback` 一键回滚 |
| 安全 | 危险命令策略（auto 拒绝 / ask 交互审批）+ 可配置规则文件（`.repofixer/rules.json`，有序 allow/ask/deny）+ 工作区越界拦截 + Docker 一次性容器沙箱（--network none + 资源上限） |
| 容错 | 断点续跑（状态快照）、任务级失败隔离（API 波动记 llm_error 不中断评测）、崩溃安全 JSONL trace |
| 可观测 | 每步 token/耗时/工具结果/阶段（plan|act）JSONL trace + 回放查看器 + 失败自动归因 + 公开失败案例库 |
| 生态 | 工具集 MCP 标准化（`repofixer mcp`，可选依赖）+ OpenAI 兼容端点 + 次级模型路由（摘要/复审） |

## 架构

```
CLI (main.py)
  ├─ eval      # 评测 runner：数据集 → 每任务独立环境 → 指标汇总（可断点续跑）
  ├─ run       # 单任务修复（--no-plan / --snapshot / --review / --approve）
  ├─ map       # repo map 子进程（tree-sitter 隔离，见 REPORT.md）
  ├─ mcp       # 工具集以 MCP stdio server 暴露（uv sync --extra mcp）
  └─ rollback  # 恢复影子快照
       └─ agent loop (loop.py)      # 线性消息累积 + Plan-Act 阶段 + 预算/守卫 + done 终止
            ├─ LLM 客户端 (llm.py)   # OpenAI 兼容端点（DeepSeek/GLM/Qwen/GPT）+ 次级模型
            ├─ 工具注册表 (tools.py) # read/edit/write/bash/grep/run_test/plan/done
            │    └─ 执行环境 (environment.py)
            │         ├─ LocalEnv   # Git Bash（POSIX 兼容）+ 路径白名单 + 进程树级超时
            │         └─ DockerEnv  # 每任务一次性容器：无网络、资源上限、用完即删
            ├─ 上下文 (context.py)  # 分级落盘 / 选择性淘汰 / LLM 摘要压缩
            ├─ 复审 (review.py)     # done 后独立上下文审 diff（VERDICT 协议）
            ├─ 记忆 (notes.py)      # 仓库笔记 + 事实抽取合并
            ├─ 安全 (safety.py)     # 黑名单 + 越界拦截 + 规则文件
            ├─ gitutil.py           # 影子快照 / diff / 回滚
            └─ trace (trace.py)     # JSONL，每步 flush（崩溃安全）
```

## 快速开始（从零到跑通）

**0. 克隆与安装**（要求 Python ≥3.11）

```bash
git clone https://github.com/huifeng-shuqing/RepoFixer.git
cd RepoFixer
uv sync                                     # 安装依赖（uv 可从 astral.sh 安装）
cp .env.example .env                        # 填 DeepSeek API key（.env 已 gitignore）
```

**1. 验证安装**（离线，无需 API）

```bash
uv run pytest                               # 61 项单元测试（+1 项 Docker 需本机 Docker）
```

**2. 冒烟**（需 API key；修复一个自带的小样例仓库）

```bash
uv run python scripts/smoke_local.py        # 期望：5 步内修复 calc_bug 并 PASS
```

**3. 构建评测数据集**（需网络：克隆 8 个上游仓库、安装各自测试依赖、生成 29 条任务与金补丁；`dataset/repos/` 与 `work/venvs/` 被 gitignore，须本地构建）

```bash
uv run python scripts/build_dataset.py
uv run python scripts/validate_dataset.py   # 质量门禁，期望 29/29
```

**4. 跑评测**（需 API key）

```bash
uv run repofixer eval --dataset data/tasks_real.jsonl --env local --out-dir results/run1
uv run python scripts/show_trace.py results/run1/traces/<task_id>.jsonl   # 逐步回放
```

**5. 单任务修复 / 沙箱 / MCP / 回滚**

```bash
uv run repofixer run "任务描述" -w <仓库路径> --env docker   # 单任务修复（Docker 沙箱，可选 local）
uv run repofixer mcp --workspace <仓库路径>   # 工具集以 MCP stdio 暴露（需 uv sync --extra mcp）
uv run repofixer rollback <sha> -w <仓库路径> # 恢复到某步影子快照
```

可选环境变量（见 `.env.example`）：`DEEPSEEK_SMALL_MODEL` 配置次级模型用于摘要与复审（未设置时回落主模型）。

## 评测数据

- 数据集：`data/tasks_real.jsonl`（29 条，8 个开源仓库的真实修复提交；SWE-bench 式测试补丁分离，见 [scripts/build_dataset.py](scripts/build_dataset.py)）
- 质量门禁：`uv run python scripts/validate_dataset.py` → 29/29「修复前必失败、打上源码补丁必通过」
- **最终结果**：完整流水线 **24/29（82.8%）**，平均 14.9 步、¥6.1/轮；逐题对比与解读见 [results/real_final/FINAL_REPORT.md](results/real_final/FINAL_REPORT.md)；三组消融（±repo map、±上下文压缩）见 [results/real/COMPARISON.md](results/real/COMPARISON.md)；失败案例库（含逐步回放轨迹）见 [results/real/FAILURES.md](results/real/FAILURES.md)

## 文档

- [DEV_PLAN.md](DEV_PLAN.md) — 开发方案与里程碑
- [REPORT.md](REPORT.md) — 实现记录 + 借代码清单（来源/许可证/改动方式）
- [docs/resume.md](docs/resume.md) — 简历 bullets 与面试问答映射
- [docs/demo.md](docs/demo.md) — 3 分钟演示指南

## License

MIT（本仓库）；借用的开源代码/思想见 REPORT.md 清单。
