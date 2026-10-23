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
- **验证状态**：✅ 单测覆盖评测管道（假 LLM 真修 bug → resolved=True）、断点续跑、noedit 基线必失败；✅ 真实模型 5 组基线已跑分（见 results/calc/COMPARISON.md，数据集扩充进行中）。
- **参照**：SWE-bench 数据集结构/判定/缓存思想（MIT）；基线设计自定。

### Block 4 — repo map + 上下文工程（2026-10-23）

- **做了什么**：`repomap`（tree-sitter 符号提取 → 文件级引用图 → PageRank 式排序（任务关键词做种子）→ token 预算渲染，注入 system prompt）；`ContextManager`（长输出落盘+预览、重要性标记、超预算选择性淘汰 + 淘汰日志 evictions.jsonl）；`run_test` 工具（测试输出只回失败摘要）；基线扩充 nomap / nocompact / noedit（纯观察者）。
- **怎么实现的**：repomap 借鉴 Aider 思路自实现简化版（文件级标识符共现近似引用边，仅 py/js 两种语言，代码内注释明示局限）；淘汰按 `value=(重要性×时间衰减) − 规模代价` 选最低者，保留最近 3 条不淘汰，全程可审计。
- **验证**：✅ 25 单测全绿（符号提取/相关性排序/预算截断/分级落盘/淘汰保重要）；✅ 真实模型冒烟（7 步修复、最终测试 PASS）；✅ 5 组基线真实跑分（results/calc/COMPARISON.md）。
- **发现（值得写进面试）**：noedit 第一版仍保留命令执行时，模型借 `run_test` 执行命令写文件绕过工具裁剪；改成纯读工具才形成真正下界。命令执行永远是可写向量，沙箱隔离比工具白名单更根本。
- **参照**：Aider repo map（Apache-2.0，思想）；learn-claude-code s08 分层压缩（MIT，改写）。

### Block 5 — 可靠性 + 真实 bugfix 数据集（2026-10-23）

- **做了什么**：
  1. **循环检测**：连续 4 次相同工具调用（名称+参数）→ `loop_detected` 终止
  2. **断点续跑**：每步持久化 `{step_next, messages, max_steps}` 状态文件，崩溃/中断后从断点继续；完成时自动清理
  3. **安全策略（HITL 基础）**：危险命令（rm/del/pip install/git push 等）按模式 auto 拒绝 / ask 交互审批，拒绝记录可报告；评测默认 auto
  4. **数据集构建器** `scripts/build_dataset.py`：真实仓库 → 扫描修复提交（改源码+测试）→ worktree 双态验证（父提交+新测试必失败、修复提交必通过）→ 产出任务（坏版本仓库 + commit message 任务描述 + verify_command + 外部 gold patch）；处理 src 布局（PYTHONPATH）与 setuptools_scm 缺失 `_version.py`（生成存根并提交进任务仓库）
- **验证**：✅ 30 单测全绿（含 M5 全部：循环熔断/状态恢复/策略拒绝）；✅ **首个真实任务 humanize-ca892b36 修复成功**（真实历史 bug `intword()` 舍入问题，40 步跑满但最终测试通过，成本 ¥0.73——效率问题留给 M6）；✅ 数据集最终产出 **31 条真实任务**（humanize 6 / tabulate 4 / shortuuid 1 / validators 4 / inflection 4 / more-itertools 4 / boltons 4 / pathspec 4；prettytable、wcwidth、tqdm 因测试环境不兼容被双态门禁自动过滤）；全量评测已在后台启动（results/real/full）
- **发现**：agent 在第 40 步仍在探索未调 done——「修对了但话痨」；M6 将加「测试通过即收手」提示与步数策略。
- **参照**：SWE-bench 实例构建流程（思想：测试改动先行、金标准分离）；无代码搬运。

### Block 5.1 — 稳定性修复 + 验证驱动早停（2026-10-23）

- **问题**：tree-sitter 0.26 原生绑定在 Python 3.14 下偶发「解释器退出期段错误」（0xC0000005，脚本跑完才崩、间歇性、组合触发），导致全量评测进程无产出。
- **修复**：把 repo map 计算隔离到子进程（`repofixer map` 子命令，eval/run 通过 subprocess 调用）——评测主进程保持纯 Python；子进程即使崩溃也只是降级为「无 map」，不伤评测。测试同步改走子进程路径，pytest 进程不再加载 tree-sitter。
- **新增「验证驱动早停」**：eval 每步结束后执行 verify_command，通过即 `solved_early` 停止——治好了 agent「修对了但话痨」（40 步 ¥0.73 → 早停后同样任务 20 步内、费用下降 70%+）。
- **验证**：✅ 30 单测全绿；✅ 单任务真实验收稳定（exit 0，resolved=1）；全量 31 任务评测已在后台重跑（results/real/full）。

### Block 5.2 — 数据集假阳性修复 + 质量门禁（2026-10-23）

- **问题**：全量评测首 3 个任务全部「1 步解决」——排查发现是**假阳性**：构建任务时只把仓库停在父提交，忘了应用修复提交里的**测试改动**（SWE-bench 的 test patch 概念），导致 verify 命令在坏代码上也无测试可失败。
- **修复**：任务仓库 = 父提交 + 测试补丁 + `_version` 存根（作为一次提交），源码修复留在外部 gold patch；任务 JSON 增加 `base_commit / fix_commit / test_files / src_files` 元数据（可追溯）。
- **质量门禁** `scripts/validate_dataset.py`（SWE-bench `--gold` 等价物）：对每条任务在全新 checkout 上验证「测试补丁下 verify 必失败 → 应用源码补丁后必通过」。**29/29 全部通过**（重建后数据集 29 条）。
- **教训（面试素材）**：评测管道本身的正确性必须被独立验证——「agent 全对」和「评测坏了」长得一模一样。
- **M6 首刀**：LLM 摘要压缩（预算超限且淘汰不足时，模型把最旧历史压缩为摘要，保留消息结构）；trace 查看器 `scripts/show_trace.py`；失败归因 `scripts/analyze_failures.py`；GitHub Actions CI。
- **当前状态**：三连跑后台执行中（完整 / nomap / nocompact，各 29 任务，带早停）。

### Block 6 — 最终评测结果（2026-10-23）

- **版本公平性**：full 最早开跑、其前 10 个任务在旧版守卫代码下完成（no_progress 阈值过严、无越界拦截），结果被压低（58.6%）；已用最终版代码重跑，三组数据同版本同协议。
- **最终数字**（deepseek-chat，29 任务，质量门禁 29/29）：

| 配置 | 解决 | 成功率 | 平均步数 | 成本(¥) |
|---|---|---|---|---|
| full（完整流水线） | 21 | 72.4% | 17.2 | 7.49 |
| nomap（无 repo map） | 21 | 72.4% | 17.4 | 7.16 |
| nocompact（无上下文预算） | 21 | 72.4% | 17.1 | 6.21 |

- **失败归因**（24 个失败任务）：loop_detected 9 / no_progress 7 / max_steps 6 / premature_done 1 / policy_blocked 1。
- **诚实结论**：三组成功率一致，任务级差异 2+2（方差范围）——在 29 个单文件级任务上 repo map 与上下文压缩无显著增益，瓶颈在模型能力本身。机制的价值体现在更大的代码库上，这是后续工作的方向，也是面试时如实表述的结论。
- **对比与归因报告**：results/real/COMPARISON.md + 各 run 目录 FAILURES.md；简历口径见 docs/resume.md。

### Block 7 — 七项升级（2026-10-24）

- **研读参考**（reference/ 浅克隆）：aider/repomap.py（tags 按 mtime 缓存、token 预算与无文件时动态扩展 map_mul_no_files=8）；gemini-cli policy.ts（ALLOW/ASK_USER/DENY 决策 + 非交互模式 ASK_USER 即拒绝）；opencode 的 agent markdown frontmatter（mode/model/tools 通配）与 permission.ts（`findLast` 实现「后匹配者胜」规则表）；khoj 的 UserMemory 模型。
- **① 任务清单驱动**：新增 `plan` 工具，模型开工前把目标/验证方式/步骤写入 `.repofixer/plan.json`，system prompt 要求持续更新——对应 Anthropic feature_list 模式。
- **② 独立上下文复审**：`review.py`（VERDICT: OK/ISSUES 协议）；模型声明 done 后，diff+任务以全新对话送复审（默认走小模型），发现关键问题则回注主循环，最多一轮纠正（max_review_rounds=1）。
- **③ 影子快照回滚**：每步 `git add -A && git stash create`（生成 commit 对象但不移动 HEAD、不改工作区），随后 `git reset` 还原 index——评测复位（checkout -- .）语义不受影响；快照 SHA 记录于 AgentRun.snapshots，回滚 = `git reset --hard <sha>`。
- **④ 仓库级长期记忆**：`notes.py`，会话开始注入 `.repofixer/notes.md`（≤4000 字符），结束追加一行结论；eval 与 CLI run 均已接入。
- **⑤ MCP 标准化**：`mcp_server.py` 用官方 python-sdk 把 7 个工具以 stdio server 暴露（`repofixer mcp`），依赖放在 optional extra `mcp`（`uv sync --extra mcp`）；schema 转换函数纯函数化可单测。
- **⑥ 分层模型路由**：`DEEPSEEK_SMALL_MODEL` 环境变量配置次级模型，用于上下文摘要与复审（Aider architect/editor 模式）；主循环决策仍用主模型。
- **⑦ 并行工具引导**：system prompt 明确「多段独立信息单次请求多工具调用」；循环本就支持一次响应多工具。
- **验证**：43 项单测全绿（新增 7 项：plan 读写、notes 回环与注入、快照不动 HEAD、复审重开循环/通过放行、MCP schema 转换）。前后对比评测：见 results/before_upgrade 与 results/after_upgrade（6 题子集）。
