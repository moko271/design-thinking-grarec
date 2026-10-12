#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
eval_gold.py — 正解（gold）と照らして、定義 #2〜#4 を測る（API不要）

正解ファイル: sim/gold/班<g>.csv（moko が文字起こしを読んで付ける。書き方は gold/README.md）
  列: start_min, end_min, kind, label
    kind = topic（話題の区切り）／ difference（意見が分かれた場面）／ question（答えが出ないまま残った問い）／ agreement（決まったこと）
指標
  #2 今なにを話しているか分かる … topic のうち、カードで拾えた割合（拾えた＝終わりから15分以内に、似た内容のカードが届いた）
                                   拾えたものの、話題が始まってから最初のカードまでの時間
  #3 違う見方が拾われる       … difference のうち、拾えた割合（種類が「ちがい」でなくても内容が似ていれば拾えたとする版も出す）
  #4 次に進むきっかけ         … question・agreement のうち、拾えた割合
「似た内容」の判定は、文字のバイグラム（2文字の並び）の重なり（Jaccard）≥ 0.2 という単純な基準。
粗い判定なので、拾えた・拾えなかった例を一覧に出して、目で確かめられるようにしている。
"""
import csv, glob, json, os
from collections import defaultdict
from sim_common import HERE, norm

GOLD = os.path.join(HERE, "gold"); OUT = os.path.join(HERE, "out")
SIM_T = 0.2; LAG_MIN = 15
KIND_OF = {"topic": None, "difference": "difference", "question": "question", "agreement": "agreement"}


def bigrams(s):
    s = norm(s); return {s[i:i + 2] for i in range(len(s) - 1)}


def sim(a, b):
    A, B = bigrams(a), bigrams(b)
    return len(A & B) / len(A | B) if A and B else 0.0


def main():
    golds = sorted(glob.glob(os.path.join(GOLD, "班*.csv")))
    if not golds:
        print("gold/ に正解ファイルがありません（gold/README.md を見て作成してください）"); return
    gold = {}
    for f in golds:
        tag = os.path.basename(f)[1:-4]
        if not tag.isdigit(): continue          # 班_example.csv などは読まない
        g = int(tag)
        gold[g] = [r for r in csv.DictReader(open(f, encoding="utf-8-sig")) if r.get("kind") in KIND_OF]
    res = defaultdict(lambda: defaultdict(list)); examples = []
    for f in sorted(glob.glob(os.path.join(OUT, "cards", "*", "*.json"))):
        d = json.load(open(f, encoding="utf-8"))
        if d["group"] not in gold: continue
        delivered = [(b["t_sec"] / 60, c) for b in d["batches"] for c in b["cards"]]
        for item in gold[d["group"]]:
            s, e = float(item["start_min"]), float(item["end_min"])
            cand = [(t, c) for t, c in delivered if s <= t <= e + LAG_MIN]
            def match(strict):
                return [(t, c) for t, c in cand
                        if max(sim(item["label"], c["keyword"]), sim(item["label"], c.get("quote", ""))) >= SIM_T
                        and (not strict or KIND_OF[item["kind"]] is None or c.get("kind") == KIND_OF[item["kind"]])]
            m_any, m_kind = match(False), match(True)
            res[d["variant"]][item["kind"] + "_any"].append(bool(m_any))
            res[d["variant"]][item["kind"] + "_kind"].append(bool(m_kind))
            if item["kind"] == "topic" and m_any:
                res[d["variant"]]["topic_delay_min"].append(min(t for t, _ in m_any) - s)
            examples.append({"variant": d["variant"], "group": d["group"], "run": d["run"], "kind": item["kind"],
                             "label": item["label"], "hit": bool(m_any),
                             "matched": [c["keyword"] for _, c in m_any][:3]})
    summary = []
    for v, m in sorted(res.items()):
        row = {"variant": v}
        for k, xs in m.items():
            row[k] = round(sum(xs) / len(xs), 3) if k != "topic_delay_min" else round(sorted(xs)[len(xs) // 2], 1)
        summary.append(row)
    json.dump({"similarity_threshold": SIM_T, "lag_min": LAG_MIN, "summary": summary, "examples": examples},
              open(os.path.join(OUT, "gold_results.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    for r in summary: print(r)


if __name__ == "__main__":
    main()
