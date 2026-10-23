# RepoFixer — 可评测的代码仓库修复 Agent

给一个任务描述（issue/需求）+ 一个 git 仓库，RepoFixer 自主读代码、定位、修改、跑测试、迭代修复，全程结构化留痕。

GitHub: <https://github.com/huifeng-shuqing/RepoFixer>

## 为什么做这个项目

大多数 agent demo 只能「跑一次看看」；RepoFixer 从第一天起围绕**评测闭环**设计：每一步都有结构化 trace、每一个任务都有客观成功判据（测试通过）、每一个机制都有消融数字。骨架与设计参考了开源世界的成熟做法，借用的每一项都在 [REPORT.md](REPORT.md) 里注明来源与许可证。

## 架构

```
CLI (main.py)
  └─ agent loop (loop.py)        # 线性消息累积 + 步数预算 + done 工具终止
       ├─ LLM 客户端 (llm.py)     # OpenAI 兼容端点（DeepSeek/GLM/Qwen/GPT）
       ├─ 工具注册表 (tools.py)   # read/write/edit/bash/grep/done，结构化 ToolResult
       │    └─ 执行环境 (environment.py)
       │         ├─ LocalEnv      # 开发模式：子进程 + 路径白名单（非安全边界）
       │         └─ DockerEnv     # 每任务一次性容器：--network none、内存/CPU/pids 上限、用完即删
       └─ 结构化 trace (trace.py) # 每步 token/耗时/工具结果 → JSONL（可回放，铺路断点续跑）
```

## 快速开始

```bash
uv sync                       # 安装依赖（含 dev 组）
cp .env.example .env          # 填入 DeepSeek API key（.env 已 gitignore）
uv run pytest                 # 单元测试（Docker 组在 daemon 可用时自动执行）
uv run python scripts/smoke_local.py --env docker   # 真实 LLM 跑通样例任务
uv run repofixer run --workspace samples/calc_bug "修复 failing 的测试" --env docker
```

trace 落在 `traces/*.jsonl`。

## 状态

| 模块 | 状态 |
|---|---|
| M1 核心循环 + 工具层 + trace | ✅ |
| M2 Docker 沙箱 + 样例仓库 + 单元测试 | ✅ 代码完成，Docker 联调待你终端验证 |
| M3 评测集 + runner + 指标 | ✅ 核心完成（1 条样例任务；真实模型跑分待执行） |
| M4 上下文管理（repo map / 压缩） | 下一步 |
| M5 断点续跑 / 循环检测 / HITL | 计划中 |
| M6 成本优化（路由/缓存/并行） | 计划中 |
| M7 失败归因 / demo / CI | 计划中 |

## 开发方案

完整里程碑、执行步骤与验收标准见 [DEV_PLAN.md](DEV_PLAN.md)。借用与出处见 [REPORT.md](REPORT.md)。
