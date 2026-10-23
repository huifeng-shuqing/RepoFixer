# RepoFixer 开发方案

> GitHub: https://github.com/huifeng-shuqing/RepoFixer
> 定位：可评测、可观测、可断点续跑的「代码仓库修复 Agent」——大厂实习简历项目。
> 原则：合理借用 MIT/Apache 开源代码并逐笔记录出处（REPORT.md）；每个实现块按 6 项汇报；每个里程碑有可执行验收标准。

## 最终验收标准（做完才算完成）

- [ ] 基线 + ≥30 条任务评测集（dev/test 划分），一条命令复现全部数字
- [ ] 指标：成功率（测试通过）、平均步数、平均 token/成本、P50/P95 延迟
- [ ] ≥3 组消融实验（±repo map、±重试恢复、±上下文压缩）
- [ ] 全链路 trace + 回放 + 失败归因报告（分类饼图）
- [ ] Docker 容器隔离执行 + 危险操作人工审批（HITL）
- [ ] 断点续跑（崩溃/中断后可从 trace 恢复）
- [ ] README（架构图 + 快速开始 + 结果表 + 已知局限）+ demo GIF
- [ ] GitHub Actions 评测回归（每次 push 出指标）

## 里程碑与执行步骤

### M1 核心闭环 ✅ 已交付
agent loop / LLM 客户端（DeepSeek 默认）/ 6 工具 / 结构化 trace / CLI。
验收：依赖安装 + 导入 + `repofixer --help` 通过（已验）。

### M2 沙箱与样例仓库（本轮）
步骤：
1. `DockerEnv`：每任务一个一次性容器（python:3.12-slim，`--network none`，
   内存 2g / CPU 2 / pids 256，每条命令内部 `timeout`，用完 `docker rm -f`）
2. `create_env` 工厂：`auto` 自动探测 docker，失败回退 local
3. CLI 加 `--env {auto,local,docker}`；用完自动清理容器
4. 样例任务仓库 `samples/calc_bug`：带真实 bug 的小型 Python 包（unittest 可验证，零依赖）
5. harness 单元测试 4 组：环境 / 工具 / 循环（假 LLM，无网络）/ Docker（无 daemon 自动跳过）
6. 冒烟脚本 `scripts/smoke_local.py`：真实 LLM 完整跑一遍样例任务，最终测试必须通过
验收：`uv run pytest` 全绿；`uv run python scripts/smoke_local.py --env docker` 修复 bug 且最终 `python tests/test_ops.py` 通过。

### M3 评测体系
步骤：
1. 任务 schema：`{id, repo, base_commit, problem_statement, fail_to_pass}`（抄 SWE-bench 结构，见 REPORT.md 借代码清单）
2. 数据集构建（方案 A）：真实开源仓库已合并 bugfix commit → `git revert` 造坏版本 → commit message 当任务描述，目标 ≥30 条；先用 10 条打通流程
3. runner：每任务一个容器、并行、超时/预算、结果按 run_id 缓存、断点续跑、`--dev/--test`
4. 指标面板：resolved / 平均步数 / token / 成本 / 延迟分位
5. 基线 3 个：naive 单轮直答、固定 3 步、无 repo map 变体
验收：基线结果表 + 一条命令复现。

### M4 上下文工程
步骤：
1. repo map：tree-sitter 符号图 + 引用度排序 + token 预算（参照 Aider 思路，自实现简化版）
2. 观察分级：短结果原样、长结果落盘+预览、测试输出只回失败摘要
3. 选择性保留：按信息密度淘汰低价值观察（记录淘汰明细供消融）
4. 上下文预算管理器：优先淘汰，再触发 LLM 摘要压缩
验收：消融 ±repo map 有显著差异；单任务平均 token 下降。

### M5 可靠性与安全
步骤：
1. 断点续跑：状态快照 + 从 trace 恢复（M2 的 flush 已铺路）
2. 循环检测：连续无进展熔断；同工具同参数重复调用熔断
3. HITL：危险操作（rm / 装包 / 网络 / 大范围改写）请求人工确认，CLI 交互式
4. 失败恢复策略机：测试失败局部重试、语法错误回传、容器失败重建
验收：故障注入测试（中断/循环/坏命令）+ 消融 ±retry。

### M6 成本优化
步骤：小模型做摘要/失败分类、大模型做决策（模型路由）；prompt 缓存；并行工具调用。
验收：同等成功率下成本对比表（↓X%）。

### M7 打磨交付
步骤：失败归因报告 + 分类饼图；README 终稿 + 架构图；demo GIF；GitHub Actions 评测回归；简历数据表。
验收：仓库完整、所有数字可一条命令复现。

## 技术决策（默认值，你随时可改）

| 项 | 决策 | 理由 |
|---|---|---|
| 语言/包管理 | Python 3.12+ / uv | 课程同栈，生态全 |
| LLM | OpenAI 兼容端点，DeepSeek 默认 | 便宜，答辩可切 GPT/Claude 对比 |
| 沙箱镜像 | python:3.12-slim + 无网络 | 安全基线 + 零依赖样例可跑 |
| 名称 | GitHub RepoFixer ↔ 包 repofixer ↔ CLI repofixer | 对齐仓库名 |
| 借代码 | mini-swe-agent / learn-claude-code（MIT）；Aider、SWE-agent（思想） | 全部记入 REPORT.md |
| 框架 | 不用 LangChain/LangGraph | 自己写 loop 是卖点 |

## 交付约定

- 每个实现块：6 项汇报（做了什么/架构/实现了什么/怎么实现/参照/借用）+ REPORT.md 记录 + git 提交
- 提交节奏：每个里程碑至少 1 个 commit；push 时机由 AI 决定（原则：能跑就推）
- 密钥纪律：key 只在 `.env`（gitignore），绝不进仓库、绝不进聊天
