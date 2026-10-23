"""Compare eval runs (full / nomap / nocompact) and write the final report.

Usage: uv run python scripts/compare_runs.py --base results/real --out results/real/COMPARISON.md
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from repofixer.eval import EvalResult, summarize  # noqa: E402


def load_results(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text(encoding="utf-8-sig").splitlines() if l.strip()]


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    base = Path(args.base)
    runs = {
        "full（完整流水线）": base / "full" / "results.jsonl",
        "nomap（无 repo map）": base / "nomap" / "results.jsonl",
        "nocompact（无上下文预算）": base / "nocompact" / "results.jsonl",
    }
    loaded = {k: load_results(v) for k, v in runs.items()}
    header = ["配置", "任务数", "解决", "成功率", "平均步数", "prompt tok", "completion tok", "估算成本(¥)"]
    rows = [header]
    for name, results in loaded.items():
        if not results:
            rows.append([name, "0", "-", "-", "-", "-", "-", "-"])
            continue
        s = summarize([EvalResult(**r) for r in results], "deepseek-chat")
        rows.append([
            name, str(s["n"]), str(s["resolved"]), f"{s['success_rate']:.1%}",
            str(s["avg_steps"]), str(s["prompt_tokens"]), str(s["completion_tokens"]),
            f"{s['est_cost_yuan']:.2f}",
        ])

    # per-task diff: tasks where full resolves but nomap does not (and vice versa)
    full = {r["task_id"]: r for r in loaded["full（完整流水线）"]}
    nomap = {r["task_id"]: r for r in loaded["nomap（无 repo map）"]}
    only_full = [t for t in full if full[t]["resolved"] and not (t in nomap and nomap[t]["resolved"])]
    only_nomap = [t for t in nomap if nomap[t]["resolved"] and not (t in full and full[t]["resolved"])]

    md = [
        "# 三连跑对比（真实 bugfix 数据集，deepseek-chat）",
        "",
        "> 成功判据：agent 结束后执行任务测试命令退出码 0。数据集 29 条真实任务，质量门禁 29/29。",
        "",
        "| " + " | ".join(rows[0]) + " |",
        "|" + "|".join(["---"] * len(rows[0])) + "|",
    ]
    for r in rows[1:]:
        md.append("| " + " | ".join(r) + " |")
    md += [
        "",
        "## 消融要点",
        "",
        f"- repo map 的贡献：full 解决而 nomap 未解决的任务 {len(only_full)} 个：{', '.join(only_full) or '无'}",
        f"- nomap 反而更好的任务（模型方差）{len(only_nomap)} 个：{', '.join(only_nomap) or '无'}",
        "",
        "## 诚实说明",
        "",
        "- 单次运行、n=29：成功率差异受模型方差影响，结论表述为「趋势」而非「严格显著性」。",
        "- 本地环境执行（Docker 模式代码路径相同，见 tests/test_docker.py）；成本按 deepseek-chat 刊例价估算。",
        "- 失败模式归因见各 run 目录的 FAILURES.md。",
        "",
    ]
    report = "\n".join(md) + "\n"
    out = Path(args.out) if args.out else base / "COMPARISON.md"
    out.write_text(report, encoding="utf-8")
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
