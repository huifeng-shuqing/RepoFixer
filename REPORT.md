# RepoFixer 实现记录

> 每完成一个实现块更新本文件。**借代码清单**必须保持诚实、可追溯——面试被问「哪些是你自己写的」时，这是你的诚信答案。

## 借代码 / 借思想清单

| 借用点 | 来源 | 许可证 | 处理方式 |
|---|---|---|---|
| agent loop 线性消息累积结构 | learn-claude-code s01（shareAI-lab/learn-claude-code） | MIT | 改写（结构化结果 + trace + done 工具 + 预算） |
| 工具 dispatch map（加工具=加 handler+schema） | learn-claude-code s02 | MIT | 模式复用，代码重写 |
| 「沙箱 = 把 subprocess 换成 docker exec」的环境抽象、每动作独立子进程执行 | mini-swe-agent（本地 vendor/mini-swe-agent 保留对照，不入库） | MIT | 参考实现，自写 Environment 接口 |
| ACI 四原则（分页查看器 / 搜索只列文件名 / 编辑严格匹配 / 空输出显式反馈） | SWE-agent 论文（arXiv:2405.15793） | 论文思想 | 仅思想，无代码搬运 |
| OpenAI 兼容客户端 | openai python SDK | Apache-2.0 | 直接声明依赖，不 vendoring |
| 评测任务结构（fail_to_pass / pass_to_pass 判定） | SWE-bench | MIT | 数据集与判定思想，M3 实现时引用 |

## 实现块记录

### Block 1 — 项目骨架 + 核心 agent 循环（2026-10-22）

- **做了什么**：初始化 uv 项目；LLM 客户端、执行环境抽象（LocalEnv）、工具层（6 工具）、agent 循环、结构化 trace、CLI。
- **架构**：`CLI → loop.py → llm.py / tools.py → environment.py`，trace 每步实时落盘。
- **验证状态**：✅ 依赖安装 + 导入 + CLI 通过（uv sync / import check / --help）。
- **参照**：learn-claude-code s01/s02；mini-swe-agent；SWE-agent ACI。

### Block 2 — Docker 沙箱 + 样例仓库 + 测试（2026-10-23）

- **做了什么**：包名对齐 GitHub 仓库（repofixer）；`DockerEnv`（一次性容器：`--network none`、内存 2g/CPU 2/pids 256、命令级 timeout、close 即删）+ `create_env` 工厂（auto 探测）；CLI `--env`；样例任务仓库 `samples/calc_bug`（真实 bug + unittest 零依赖）；4 组单元测试（环境/工具/循环/Docker）；冒烟脚本 `scripts/smoke_local.py`。
- **怎么实现的**：DockerEnv 把 workspace 挂到 `/workspace`，每条命令包成 `cd /workspace && timeout N sh -c <cmd>` 在新 shell 里执行——保持「每动作独立、不持有会话」的 mini 设计；循环用 FakeLLM 做无网络单测，trace 断言落盘内容。
- **验证状态**：✅ 16 个非 Docker 单测全绿；Docker 组因开发沙箱无法访问 docker 命名管道，由你在自己终端跑 `uv run pytest` / `uv run python scripts/smoke_local.py --env docker` 验证。
- **参照**：mini-swe-agent 环境抽象（MIT）；SWE-bench 任务结构（思想）。

### Block 3 — 评测体系核心（2026-10-23）

- **做了什么**：任务 schema（id/problem_statement/repo/verify_command/timeout/difficulty，JSONL）；评测 runner（每任务独立环境 + 逐条结果追加 + 按 task_id 断点续跑 + 每任务 trace 落盘）；指标汇总（成功率/平均步数/token/成本估算/延迟分位）；两个基线（step3=3 步截断、noedit=裁剪编辑工具）；CLI `repofixer eval`。
- **怎么实现的**：verify_command 的退出码即成功判据（SWE-bench 判定思想的零依赖简化版，F2P/P2P 留待接入真实 SWE-bench 数据时实现）；resume 通过读 results.jsonl 已有 task_id 跳过，防重复烧钱（SWE-bench run_id 缓存同思路）。
- **验证状态**：✅ 单测覆盖评测管道（假 LLM 真修 bug → resolved=True）、断点续跑、noedit 基线必失败；真实模型跑分待你终端执行。
- **参照**：SWE-bench 数据集结构/判定/缓存思想（MIT）；基线设计自定。
