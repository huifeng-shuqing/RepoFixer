# Demo 指南（3 分钟面试演示）

## 1. 修一个真实历史 bug（约 1 分钟）

```bash
uv run python scripts/smoke_local.py --env local        # 样例仓库 calc_bug（快）
uv run repofixer run "修复 failing 的测试" -w samples/calc_bug --env local
```

## 2. 看一条真实评测任务的全过程（约 1 分钟）

```bash
uv run repofixer eval --dataset data/tasks_real.jsonl --env local --out-dir results/demo --max-steps 40
# 跑完第一条任务后 Ctrl+C（断点续跑保证不重复花钱）
uv run python scripts/show_trace.py results/demo/traces/<task_id>.jsonl
```

`show_trace.py` 会逐步展示：每一步花了多少 token/毫秒、调了哪些工具、成功还是失败——这就是「可观测性」的现场证据。

## 3. 展示三件「机制」证据（约 1 分钟）

1. **断点续跑**：`repofixer run ... --state traces/demo.state.json` 跑到一半 Ctrl+C，重跑同命令从断点继续。
2. **安全策略**：让 agent 执行 `rm -rf` 会被自动拒绝（PolicyDenied 记录在 trace 里）。
3. **评测门禁**：`uv run python scripts/validate_dataset.py` 当场验证 29/29 条任务「修复前必失败、修复后必通过」。

## 录制 GIF 建议

Windows 终端 + ScreenToGif 录制上面第 1、2 步即可（约 60 秒），放 README 顶部。
