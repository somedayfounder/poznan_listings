#!/usr/bin/env python3
"""Re-score all districts via GPT-4o-mini, average with current score, update score.py."""

import json
import os
import re
import sys
import time
import urllib.request
from pathlib import Path

SCORE_PY = Path(__file__).parent / "score.py"
BATCH_SIZE = 15

GPT_TOKEN = os.environ.get("GPT_TOKEN", "")
if not GPT_TOKEN:
    print("ERROR: GPT_TOKEN not set")
    sys.exit(1)

# Parse current DISTRICT_SCORES and DISTRICT_SUMMARIES/PROS/CONS from score.py
src = SCORE_PY.read_text(encoding="utf-8")

def parse_scores(src):
    block = src.split("DISTRICT_SCORES = {", 1)[1].split("\n}", 1)[0]
    result = {}
    for m in re.finditer(r'"([^"]+)":\s*(\d+(?:\.\d+)?)', block):
        result[m.group(1)] = float(m.group(2))
    return result

def parse_strdict(src, name):
    if name + " = {" not in src:
        return {}
    block = src.split(name + " = {", 1)[1].split("\n}", 1)[0]
    result = {}
    for m in re.finditer(r'"([^"]+)":\s*"((?:[^"\\]|\\.)*)"', block):
        result[m.group(1)] = m.group(2).replace('\\"', '"')
    return result

def parse_listdict(src, name):
    if name + " = {" not in src:
        return {}
    block = src.split(name + " = {", 1)[1].split("\n}", 1)[0]
    result = {}
    for m in re.finditer(r'"([^"]+)":\s*(\[.*?\])', block, re.DOTALL):
        try:
            result[m.group(1)] = json.loads(m.group(2))
        except Exception:
            pass
    return result

scores = parse_scores(src)
summaries = parse_strdict(src, "DISTRICT_SUMMARIES")
pros = parse_listdict(src, "DISTRICT_PROS")
cons = parse_listdict(src, "DISTRICT_CONS")

districts = []
for name, score in scores.items():
    districts.append({
        "name": name,
        "score": score,
        "summary": summaries.get(name, ""),
        "pros": pros.get(name, []),
        "cons": cons.get(name, []),
    })

print(f"Загружено районов: {len(districts)}")

SYSTEM_PROMPT = """You are a real estate analyst scoring Poznań-area neighborhoods for Russian-speaking families looking for apartments (70-120m², 600k-1.2M PLN budget).

Score each district 1-10:
- 9-10: excellent (trams/frequent transit, quiet, green, full urban infrastructure in Poznań proper)
- 7-8: good (decent transport, most amenities, urban feel)
- 5-6: average (suburban, limited transit, car recommended)
- 3-4: poor (remote, industrial, very limited transit)
- 1-2: very poor (isolated village, no infrastructure)

Be critical. Inner Poznań with trams: 7-8. Good suburbs with rail: 6-7. Remote villages: 3-4. Pure countryside: 1-2.

Return ONLY a JSON array: [{"name": "...", "gpt_score": N}, ...]
No markdown, no explanation."""


def call_gpt(batch):
    lines = []
    for d in batch:
        ps = "; ".join(d["pros"])[:200]
        cs = "; ".join(d["cons"])[:200]
        sm = d["summary"][:300]
        lines.append(f"- {d['name']}: {sm} | pros: {ps} | cons: {cs}")
    payload = json.dumps({
        "model": "gpt-4o-mini",
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": "Score these districts:\n" + "\n".join(lines)},
        ],
        "temperature": 0.3,
        "max_tokens": 1000,
    }).encode()
    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=payload,
        headers={"Authorization": f"Bearer {GPT_TOKEN}", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        content = json.loads(r.read())["choices"][0]["message"]["content"].strip()
    content = re.sub(r"^```(?:json)?\n?", "", content)
    content = re.sub(r"\n?```$", "", content)
    return json.loads(content)


gpt_scores = {}
batches = [districts[i:i+BATCH_SIZE] for i in range(0, len(districts), BATCH_SIZE)]
print(f"Батчей: {len(batches)}")

for i, batch in enumerate(batches):
    print(f"  Батч {i+1}/{len(batches)}: {[d['name'] for d in batch[:2]]}...")
    for attempt in range(3):
        try:
            results = call_gpt(batch)
            for r in results:
                gpt_scores[r["name"]] = float(r["gpt_score"])
            print(f"    → {len(results)} оценок")
            break
        except Exception as e:
            print(f"    Попытка {attempt+1} ошибка: {e}")
            if attempt < 2:
                time.sleep(5)
    if i < len(batches) - 1:
        time.sleep(1)

print(f"\nGPT оценил: {len(gpt_scores)} из {len(districts)}")

# Update DISTRICT_SCORES in score.py
changes = []
new_src = src
for name, gpt in gpt_scores.items():
    old = scores.get(name)
    if old is None:
        continue
    new_score = round((old + gpt) / 2 * 2) / 2
    pattern = re.compile(r'("' + re.escape(name) + r'"\s*:\s*)(\d+(?:\.\d+)?)(,?)')

    def replacer(m, ns=new_score, nm=name, o=old, g=gpt):
        changes.append((nm, o, g, float(m.group(2)), ns))
        val = f"{int(ns)}.0" if ns == int(ns) else str(ns)
        return m.group(1) + val + m.group(3)

    new_src = pattern.sub(replacer, new_src)

tmp = SCORE_PY.with_suffix(".tmp")
tmp.write_text(new_src, encoding="utf-8")
tmp.replace(SCORE_PY)

# Также сохраняем GPT-оценки в rescore_results.json для отображения в HTML
RESCORE_FILE = Path(__file__).parent / "rescore_results.json"
rescore = json.loads(RESCORE_FILE.read_text()) if RESCORE_FILE.exists() else {}
for name, gpt in gpt_scores.items():
    if name not in rescore:
        rescore[name] = {}
    rescore[name]["gpt"] = gpt
RESCORE_FILE.write_text(json.dumps(rescore, ensure_ascii=False, indent=2))

print(f"Обновлено: {len(changes)} районов\n")
print(f"{'Район':<35} {'Было':>5} {'GPT':>5} {'Новое':>6}  {'Δ':>5}")
print("-" * 60)
for nm, old_j, gpt, old_py, new in sorted(changes, key=lambda x: abs(x[4]-x[3]), reverse=True)[:40]:
    print(f"{nm:<35} {old_py:>5.1f} {gpt:>5.1f} {new:>6.1f}  {new-old_py:>+5.1f}")
