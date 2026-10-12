#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sim_balance.py — 「話のバランス」と偏りのお知らせの設定を総当たりで比べる（API不要・標準ライブラリのみ）

定義との対応
  #1 みんなの声が場に出ている … 本当に偏っている時間帯にお知らせが出るか（拾えた割合・出るまでの時間）
  #5 話し合いの邪魔をしない   … 偏っていないのに出る回数、1時間あたりの回数

入力（話者の時系列）
  ・合成シナリオ5種（正解が分かっている）：均等／1人が多い／途中から偏る／1人が黙る／2人だけ話す
  ・../interview_full.rttm（自分で録った会話をpyannoteで話者分離した推定結果。正解ではないが実際の話し方の参考）
システムの再現
  ・90秒区間ごとに話者別秒数が届く（区間が終わってから処理に20秒かかる想定）
  ・15秒ごとに判定し直す。偏りに変わったとき、または閉じてからスヌーズ時間が過ぎてもまだ偏っているときに表示
  ・表示されたお知らせは60秒後に閉じられる想定（実際の行動は不明なので仮定）
「本当の偏り」の決め方（分析用。判定の設定とは別）
  ・実際の発話時刻で直近10分の正規化ジニ係数 ≥ TRUTH_GINI、または誰かの割合が5%未満
  ・録音開始5分以降、2分以上続いた時間帯を「偏りの時間帯」とする
出力: out/balance_results.json, out/balance_results.csv
"""
import csv, itertools, json, os, random
from sim_common import ROOT, HERE, entropy_balance, gini_norm

OUT = os.path.join(HERE, "out"); os.makedirs(OUT, exist_ok=True)
SEG = 90; DELAY = 20; TICK = 15; DISMISS_AFTER = 60
TRUTH_GINI = 0.40; TRUTH_QUIET = 0.05; TRUTH_WIN = 600; TRUTH_MIN_EP = 120; WARM = 300
TRUTH_MERGE_GAP = 120   # 2分未満の切れ目は同じ偏りの時間帯とみなす（30秒だけ戻ってまた偏る、を2回と数えない）
# 定義#5「話し合いの邪魔をしない」の線（仮）。これを満たす設定の中で、拾えた割合が高いものを選ぶ
MAX_FALSE_PER_HOUR = 0.5   # 偏っていないのに出る：2時間の授業で1回未満
MAX_PER_HOUR = 4.0         # お知らせ全体：15分に1回まで
MAX_DELAY_MIN = 5.0        # 定義#1：偏り始めてから5分以内（中央値）に出ないと、役に立つ頃には場面が変わっている
MAX_WORST_DELAY_MIN = 10.0 # いちばん遅かった偏りでも10分以内には出る

# ---------- 話者の時系列 ----------
def synth(name, minutes, weights_fn, n=4, seed=0):
    """各時刻の話者の重みに沿って、2〜12秒の発話を順番に並べる"""
    rng = random.Random(seed); t = 0.0; segs = []
    while t < minutes * 60:
        w = weights_fn(t / 60)
        spk = rng.choices(range(n), weights=w)[0]
        d = rng.uniform(2, 12)
        segs.append((t, t + d, f"S{spk}"))
        t += d + rng.uniform(0, 1.5)
    return name, segs, [f"S{i}" for i in range(n)]

PATTERNS = [
    ("均等に話す", lambda m: [1, 1, 1, 1]),
    ("自然な差がある（偏りとまでは言えない）", lambda m: [1.6, 1.1, 0.9, 0.6]),
    ("ずっと1人が多い", lambda m: [5, 1.4, 1.3, 1.2]),
    ("20〜40分だけ1人が多い", lambda m: [6, 1, 1, 1] if 20 <= m < 40 else [1, 1, 1, 1]),
    ("15〜35分に1人が黙る", lambda m: [1, 1, 1, 0.0001] if 15 <= m < 35 else [1, 1, 1, 1]),
    ("25分以降は2人だけ話す", lambda m: [1, 1, 0.05, 0.05] if m >= 25 else [1, 1, 1, 1]),
]
SEEDS = [1, 2, 3]   # 同じパターンを乱数を変えて3回ずつ
SCEN = [synth(f"{name}#{sd}", 60, fn, seed=100 * i + sd) for i, (name, fn) in enumerate(PATTERNS) for sd in SEEDS]

def rttm(path):
    segs = []; spk = set()
    for line in open(path):
        p = line.split()
        if len(p) >= 8 and p[0] == "SPEAKER":
            s, d = float(p[3]), float(p[4]); segs.append((s, s + d, p[7])); spk.add(p[7])
    return segs, sorted(spk)

def seconds_in(segs, a, b, speakers):
    acc = {s: 0.0 for s in speakers}
    for s, e, k in segs:
        if e > a and s < b and k in acc:
            acc[k] += min(e, b) - max(s, a)
    return acc

# ---------- 本当の偏り ----------
def truth_flags(segs, speakers, end):
    flags = {}
    for t in range(0, int(end) + 1, TICK):
        if t < WARM: flags[t] = False; continue
        acc = seconds_in(segs, max(0, t - TRUTH_WIN), t, speakers); xs = list(acc.values()); tot = sum(xs)
        g = gini_norm(xs)
        flags[t] = bool(tot >= 60 and g is not None and (g >= TRUTH_GINI or min(xs) / tot < TRUTH_QUIET))
    # 偏っている区間を取り出し、2分未満の切れ目はつなげてから、2分以上続いたものだけを「偏りの時間帯」に
    runs, cur = [], None
    for t in sorted(flags):
        if flags[t] and cur is None: cur = t
        if not flags[t] and cur is not None: runs.append([cur, t]); cur = None
    if cur is not None: runs.append([cur, end])
    merged = []
    for a, b in runs:
        if merged and a - merged[-1][1] < TRUTH_MERGE_GAP: merged[-1][1] = b
        else: merged.append([a, b])
    eps = [(a, b) for a, b in merged if b - a >= TRUTH_MIN_EP]
    in_ep = {t: any(a <= t < b for a, b in eps) for t in flags}
    return eps, in_ep

# ---------- システムの再現 ----------
def make_arrivals(segs, speakers, end):
    """90秒区間ごとの話者別秒数（区間が終わって20秒後に届く）"""
    arrivals, t = [], 0
    while t < end:
        arrivals.append((t + SEG + DELAY, seconds_in(segs, t, t + SEG, speakers))); t += SEG
    return arrivals

def run_policy(arrivals, speakers, end, pol, registered):
    shown, snooze_until = [], -1
    for now in range(0, int(end) + 1, TICK):
        recent = [a for (ts, a) in arrivals if ts <= now and ts >= now - pol["window"] * 60]
        sums = {s: sum(a[s] for a in recent) for s in speakers}
        xs = [sums[s] for s in registered]; tot = sum(xs)
        ready = now >= WARM and tot >= 60 and len(registered) >= 2
        if not ready: state = "wait"
        else:
            if pol["metric"] == "entropy":
                v = entropy_balance(xs); off = v is not None and v < pol["thr"]
            else:
                v = gini_norm(xs); off = v is not None and v >= pol["thr"]
            if pol["quiet"] is not None and tot > 0 and min(xs) / tot < pol["quiet"]: off = True
            state = "off" if off else "calm"
        # 偏っていて、スヌーズ中でなければ表示（60秒後に閉じられ、そこからスヌーズ）
        if state == "off" and now >= snooze_until:
            shown.append(now)
            snooze_until = now + DISMISS_AFTER + pol["snooze"] * 60
    return shown

def evaluate(shown, eps, in_ep, end):
    hours = end / 3600
    false = [t for t in shown if not in_ep.get(t - t % TICK, False)]
    hit, delays = 0, []
    for a, b in eps:
        first = [t for t in shown if a <= t < b + 60]
        if first: hit += 1; delays.append(first[0] - a)
    return {"notices": len(shown), "per_hour": round(len(shown) / hours, 2),
            "false": len(false), "false_per_hour": round(len(false) / hours, 2),
            "episodes": len(eps), "hit": hit,
            "hit_rate": round(hit / len(eps), 3) if eps else None,
            "median_delay_min": round(sorted(delays)[len(delays) // 2] / 60, 1) if delays else None,
            "delays_min": [round(x / 60, 2) for x in delays]}

def main():
    timelines = [(n, s, sp, sp) for n, s, sp in SCEN]
    rp = os.path.join(ROOT, "interview_full.rttm")
    if os.path.exists(rp):
        s, sp = rttm(rp)
        timelines.append(("実録音（pyannote推定・4話者）", s, sp, sp))
        timelines.append(("実録音・1人未登録", s, sp, sp[:-1]))
    grid = []
    for metric, thrs in (("entropy", [0.75, 0.80, 0.85, 0.90]), ("gini", [0.35, 0.40, 0.45, 0.50])):
        for thr, win, quiet, snooze in itertools.product(thrs, [5, 10, 15], [None, 0.03, 0.05], [3, 5, 10]):
            grid.append({"metric": metric, "thr": thr, "window": win, "quiet": quiet, "snooze": snooze})
    results = []; truths = {}
    for name, segs, speakers, registered in timelines:
        end = max(e for _, e, _ in segs)
        eps, in_ep = truth_flags(segs, registered, end)
        arrivals = make_arrivals(segs, speakers, end)
        truths[name] = {"minutes": round(end / 60, 1), "episodes": [[round(a / 60, 1), round(b / 60, 1)] for a, b in eps]}
        for pol in grid:
            r = evaluate(run_policy(arrivals, speakers, end, pol, registered), eps, in_ep, end)
            results.append({"timeline": name, **pol, **r})
    # 設定ごとに全タイムラインをまとめる
    summ = {}
    for r in results:
        k = (r["metric"], r["thr"], r["window"], r["quiet"], r["snooze"])
        s = summ.setdefault(k, {"eps": 0, "hit": 0, "false": 0, "notices": 0, "hours": 0.0, "delays": []})
        s["eps"] += r["episodes"]; s["hit"] += r["hit"]; s["false"] += r["false"]; s["notices"] += r["notices"]
        s["hours"] += truths[r["timeline"]]["minutes"] / 60
        s["delays"].extend(r["delays_min"])
    table = []
    for (m, thr, win, q, sn), s in summ.items():
        table.append({"metric": m, "thr": thr, "window": win, "quiet": q, "snooze": sn,
                      "hit_rate": round(s["hit"] / s["eps"], 3) if s["eps"] else None,
                      "false_per_hour": round(s["false"] / s["hours"], 2),
                      "per_hour": round(s["notices"] / s["hours"], 2),
                      "median_delay_min": round(sorted(s["delays"])[len(s["delays"]) // 2], 1) if s["delays"] else None,
                      "max_delay_min": round(max(s["delays"]), 1) if s["delays"] else None,
                      "current": (m, thr, win, q, sn) == ("entropy", 0.85, 10, 0.03, 5)})
    # 並べ方：まず3本の線（誤報・回数・遅れ）を満たすか → 拾えた割合が高い → 回数が少ない → 早い → 誤報が少ない
    for r in table: r["quiet_enough"] = (r["false_per_hour"] <= MAX_FALSE_PER_HOUR and r["per_hour"] <= MAX_PER_HOUR
                                         and r["median_delay_min"] is not None and r["median_delay_min"] <= MAX_DELAY_MIN
                                         and r["max_delay_min"] <= MAX_WORST_DELAY_MIN)
    table.sort(key=lambda r: (not r["quiet_enough"], -(r["hit_rate"] or 0), r["per_hour"], r["median_delay_min"] or 99, r["false_per_hour"]))
    cur_pol = {"metric": "entropy", "thr": 0.85, "window": 10, "quiet": 0.03, "snooze": 5}
    # 候補：1位と「ほぼ同じ成績」（拾えた割合・誤報が同じで、回数が1位の1.1倍以内）の中から、今の設定から変える所がいちばん少ないもの
    KEYS = ("metric", "thr", "window", "quiet", "snooze")
    top = table[0]
    near = [r for r in table if r["quiet_enough"] and r["hit_rate"] == top["hit_rate"]
            and r["false_per_hour"] <= top["false_per_hour"] and r["per_hour"] <= top["per_hour"] * 1.1 + 0.05]
    changes = lambda r: sum(r[k] != cur_pol[k] for k in KEYS) if r["metric"] == cur_pol["metric"] else 1 + sum(r[k] != cur_pol[k] for k in KEYS[2:])
    best = min(near, key=lambda r: (changes(r) if not r["current"] else 99, r["per_hour"])) if near else top
    if best["current"]: best = next(r for r in table if not r["current"])
    for r in table: r["candidate"] = r is best; r["n_changes"] = changes(r)
    best_pol = {k: best[k] for k in KEYS}
    rank1_pol = {k: top[k] for k in KEYS}
    strips = []
    for name, segs, speakers, registered in timelines:
        end = max(e for _, e, _ in segs); arr = make_arrivals(segs, speakers, end)
        strips.append({"timeline": name, "minutes": truths[name]["minutes"], "truth": truths[name]["episodes"],
                       "current": [round(t / 60, 2) for t in run_policy(arr, speakers, end, cur_pol, registered)],
                       "best": [round(t / 60, 2) for t in run_policy(arr, speakers, end, best_pol, registered)]})
    json.dump({"assumptions": {"segment_sec": SEG, "delay_sec": DELAY, "tick_sec": TICK, "dismiss_after_sec": DISMISS_AFTER,
                               "truth": f"直近10分の正規化ジニ係数≥{TRUTH_GINI} または誰かが{int(TRUTH_QUIET*100)}%未満、2分未満の切れ目はつなげて2分以上継続、開始5分以降",
                               "max_false_per_hour": MAX_FALSE_PER_HOUR, "max_per_hour": MAX_PER_HOUR, "max_delay_min": MAX_DELAY_MIN, "max_worst_delay_min": MAX_WORST_DELAY_MIN},
               "timelines": truths, "summary": table, "best_policy": best_pol, "rank1_policy": rank1_pol, "current_policy": cur_pol,
               "strips": strips, "detail": results},
              open(os.path.join(OUT, "balance_results.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    with open(os.path.join(OUT, "balance_results.csv"), "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(table[0])); w.writeheader(); w.writerows(table)
    cur = [r for r in table if r["current"]][0]
    print("本当の偏りの時間帯:", json.dumps(truths, ensure_ascii=False))
    print("今の設定:", cur)
    print("上位10:"); [print(r) for r in table[:10]]

if __name__ == "__main__":
    main()
