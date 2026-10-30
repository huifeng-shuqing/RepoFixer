import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# 1) data files: rewrite ANY absolute venv path (original or <USER>-corrupted)
#    to portable "work/venvs/<repo>/Scripts/python.exe"
pat = re.compile(r'"([A-Za-z]:)?[^"]*?repair-agent[\\/]+work[\\/]+venvs[\\/]+([A-Za-z0-9_\-]+)[\\/]+Scripts[\\/]+python\.exe"')

for name in ("tasks_real.jsonl", "tasks_failed5.jsonl", "tasks_reasoner4.jsonl"):
    p = ROOT / "data" / name
    text = p.read_text(encoding="utf-8")
    new = pat.sub(r'"work/venvs/\2/Scripts/python.exe"', text)
    p.write_text(new, encoding="utf-8")
    n = len(pat.findall(text))
    print(f"{name}: {n} 处路径已重写")

# 2) report any remaining wuqiong occurrences with context
import sys
remaining = 0
for p in list(ROOT.glob("data/*.jsonl")) + list(ROOT.glob("results/**/obs/**/*.txt")) + list(ROOT.glob("results/**/transcripts/*.txt")) + list(ROOT.glob("results/**/*.md")):
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        continue
    if "wuqiong" in text:
        idx = text.find("wuqiong")
        print(f"REMAIN {p}: ...{text[max(0, idx - 60):idx + 20]!r}...")
        remaining += 1
print("remaining files:", remaining)
