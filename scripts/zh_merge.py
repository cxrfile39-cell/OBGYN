#!/usr/bin/env python3
"""把新寫好的中文重點併入 docs/zh.json。
用法：python scripts/zh_merge.py 新檔.json   （內容為 {"PMID": "一句中文重點", ...}）
會檢查：PMID 必須在待辦清單中、內容不可空白、長度 8–90 字，並移除換行。"""
import json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
zh_path, q_path = ROOT / "docs/zh.json", ROOT / "data/zh_queue.json"
done = json.loads(zh_path.read_text(encoding="utf-8")) if zh_path.exists() else {}
queue = {it["pmid"] for it in json.loads(q_path.read_text(encoding="utf-8")).get("items", [])}
new = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
ok, bad = 0, []
for pmid, txt in new.items():
    pmid, txt = str(pmid).strip(), " ".join(str(txt).split())
    if pmid not in queue:
        bad.append((pmid, "不在待辦清單")); continue
    if not 8 <= len(txt) <= 90:
        bad.append((pmid, f"長度 {len(txt)} 字不符")); continue
    done[pmid] = txt; ok += 1
zh_path.write_text(json.dumps(dict(sorted(done.items(), key=lambda kv: int(kv[0]) if kv[0].isdigit() else 0, reverse=True)),
                              ensure_ascii=False, indent=0), encoding="utf-8")
print(f"併入 {ok} 篇，總共 {len(done)} 篇")
for p, why in bad:
    print(f"  略過 PMID {p}：{why}")
