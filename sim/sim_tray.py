#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sim_tray.py — sim_cards.py の結果を使って、トレイの動きと「出しすぎていないか」「AIが足していないか」を比べる（API不要）

定義との対応
  #3 違う見方が拾われる   … 「ちがい」「ひとことだけ出た」のカードの割合（正解との照合は eval_gold.py）
  #4 次に進むきっかけ     … 「問い」「まとまり」の割合
  #5 話し合いの邪魔をしない … 10分あたりの新しいカード枚数、トレイに同時に並ぶ最大枚数、
                              シートに置かれないまま入れ替わったカードの割合、同じ話題の重複
  #6 AIが勝手に足さない   … 引用文が、その回に渡した文字起こしに含まれる割合（空白・記号を除いて照合）、
                              キーワードの文字のうち文字起こしに出てくる文字の割合
トレイの動きは kadai.html の deliver() と同じ規則で再現する（入れ替え・×回数・再登場）。
人がシートに置くかどうかは分からないので、仮定を3通り用意した：
  none = 誰も置かない ／ place1 = 毎回1枚目を置く ／ place2 = 毎回2枚置く
出力: out/tray_results.json, out/tray_results.csv
"""
import csv, glob, json, os
from collections import Counter, defaultdict
from sim_common import HERE, norm, similar

OUT = os.path.join(HERE, "out")
PLACEMENT = {"none": 0, "place1": 1, "place2": 2}


def replay(batches, place_n):
    cards = []      # {kw, kind, place: tray|sheet|archived, mention}
    stats = Counter(); max_tray = 0
    for b in batches:
        for c in cards:
            if c["place"] == "tray": c["place"] = "archived"; stats["swapped"] += 1
        new_now = []
        for n in b["cards"]:
            live = next((c for c in cards if c["place"] in ("tray", "sheet") and similar(c["kw"], n["keyword"])), None)
            if live: live["mention"] += 1; stats["bump"] += 1; continue
            old = next((c for c in cards if c["place"] == "archived" and similar(c["kw"], n["keyword"])), None)
            if old: old["place"] = "tray"; old["mention"] += 1; stats["revive"] += 1; new_now.append(old); continue
            c = {"kw": n["keyword"], "kind": n.get("kind"), "lone": n.get("lone"), "place": "tray", "mention": 1}
            cards.append(c); new_now.append(c); stats["new"] += 1
        for c in new_now[:place_n]:
            c["place"] = "sheet"; stats["placed"] += 1
        max_tray = max(max_tray, sum(1 for c in cards if c["place"] == "tray"))
    return stats, max_tray


def grounding(batches):
    q_ok = q_n = 0; kw_ratio = []
    for b in batches:
        text = norm(b.get("window_text", ""))
        for c in b["cards"]:
            q = norm(c.get("quote", ""))
            if q: q_n += 1; q_ok += q in text
            k = norm(c["keyword"])
            if k: kw_ratio.append(sum(ch in text for ch in k) / len(k))
    return (q_ok / q_n if q_n else None), (sum(kw_ratio) / len(kw_ratio) if kw_ratio else None)


def main():
    files = sorted(glob.glob(os.path.join(OUT, "cards", "*", "*.json")))
    if not files:
        print("out/cards/ に結果がありません。先に sim_cards.py を実行してください"); return
    rows = []
    for f in files:
        d = json.load(open(f, encoding="utf-8")); bs = d["batches"]
        # 長さ＝発言が入っていた区間の合計（班4は途中に3時間以上の空白があり、全体の長さで割ると薄まるため）
        prev_t, active = 0, 0
        for b in bs:
            if b.get("memo_chars"): active += b["t_sec"] - prev_t
            prev_t = b["t_sec"]
        minutes = max(1, active / 60)
        kinds = Counter(c.get("kind") for b in bs for c in b["cards"]); total = sum(kinds.values())
        qg, kg = grounding(bs)
        base = {"variant": d["variant"], "group": d["group"], "run": d["run"], "minutes": round(minutes, 1),
                "delivered": total, "per_10min": round(total / minutes * 10, 2),
                "share_difference": round(kinds["difference"] / total, 3) if total else None,
                "share_question": round(kinds["question"] / total, 3) if total else None,
                "share_agreement": round(kinds["agreement"] / total, 3) if total else None,
                "share_lone": round(sum(1 for b in bs for c in b["cards"] if c.get("lone")) / total, 3) if total else None,
                "quote_grounded": round(qg, 3) if qg is not None else None,
                "keyword_char_grounded": round(kg, 3) if kg is not None else None}
        for pname, pn in PLACEMENT.items():
            st, mt = replay(bs, pn)
            rows.append({**base, "placement": pname, "new_cards": st["new"], "bump": st["bump"], "revive": st["revive"],
                         "swapped_unplaced": st["swapped"],
                         "swap_rate": round(st["swapped"] / (st["new"] + st["revive"]), 3) if st["new"] + st["revive"] else None,
                         "max_tray": mt})
    # 設定×置き方ごとの平均
    agg = defaultdict(list)
    for r in rows: agg[(r["variant"], r["placement"])].append(r)
    keys = ["per_10min", "max_tray", "swap_rate", "bump", "revive", "share_difference", "share_question",
            "share_agreement", "share_lone", "quote_grounded", "keyword_char_grounded"]
    summary = []
    for (v, p), rs in sorted(agg.items()):
        s = {"variant": v, "placement": p, "n_runs": len(rs)}
        for k in keys:
            xs = [r[k] for r in rs if r[k] is not None]
            s[k] = round(sum(xs) / len(xs), 3) if xs else None
        summary.append(s)
    json.dump({"summary": summary, "detail": rows}, open(os.path.join(OUT, "tray_results.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    with open(os.path.join(OUT, "tray_results.csv"), "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(summary[0])); w.writeheader(); w.writerows(summary)
    for s in summary: print(s)


if __name__ == "__main__":
    main()
