#!/usr/bin/env python3
"""列出本週下一批需要撰寫中文重點的文章（指引、統合分析）（給每週 Claude 排程任務使用）。
用法：python scripts/zh_batch.py [篇數，預設 50] [略過前幾篇，預設 0]"""
import json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
done = json.loads((ROOT / "docs/zh.json").read_text(encoding="utf-8")) if (ROOT / "docs/zh.json").exists() else {}
q = json.loads((ROOT / "data/zh_queue.json").read_text(encoding="utf-8")).get("items", [])
todo = [it for it in q if it["pmid"] not in done]
n = int(sys.argv[1]) if len(sys.argv) > 1 else 50
skip = int(sys.argv[2]) if len(sys.argv) > 2 else 0
TYPE = {"guideline": "共識/指引", "meta": "統合分析", "review": "綜論", "original": "原著"}
print(f"# 待辦共 {len(todo)} 篇（已完成 {len(done)} 篇），以下第 {skip + 1}–{min(skip + n, len(todo))} 篇\n")
for it in todo[skip:skip + n]:
    print(f"PMID {it['pmid']} | {TYPE.get(it['type'], it['type'])} | {it['ta']}")
    print(f"TITLE: {it['title']}")
    if it.get("key"):
        print(f"KEY: {it['key']}")
    print()
