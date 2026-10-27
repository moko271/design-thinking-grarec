# -*- coding: utf-8 -*-
"""
同じ話し合いで「人（ファシリテーター）が書いた付箋」と「AIが作ったカード」を比べる。APIは使わない。
/kadai?ai=hidden（または /design?ai=hidden）で記録したセッションを想定（AIカードは画面に出さず記録だけしている）。
ふつうのモードの記録でも、手で書いたカードがあれば使える。

grarec_light フォルダで:
    python tools/compare_human_ai.py <セッションのJSON> [--notes 紙のメモ.txt] [--with-texts] [--window 10]
      --notes      紙に書いたメモを足すとき。1行に「14:05 内容」（時刻は録音した日の時:分、秒もOK）
      --with-texts シートのテキストボックス（1行＝1項目）も「人」の側に入れる
      --window     人とAIの項目を組にするときの、時刻の差の上限（分、既定10）
→ 同じ場所に <セッション名>_human_ai.md と <セッション名>_human_ai.csv

見るもの
  1. 重なり：両方が拾った／人だけ／AIだけ（文字の近さ＝バイグラムJaccard 0.25以上、または同じ発言から作り文字も少し重なる）
  2. どこを切り取ったか：それぞれの項目のもとになった発言（文字起こしの中でいちばん近い発言）と、その時刻
  3. 遅れ：発言されてから付箋・カードになるまでの秒数
  4. 抽象度：もとの発言の言葉をどれだけそのまま使ったか（ほぼそのまま／言い換え／まとめ）
粗い自動判定なので、最後は表を目で確かめること。
"""
import argparse
import csv
import json
import os
import re
import unicodedata
from collections import Counter
from datetime import datetime

NEAR, WEAK = 0.25, 0.12        # session_report.py と同じ目安
SRC_MIN = 0.15                 # これ未満なら「もとの発言が見つからない」
SURE = 0.3                     # これ未満のもとの発言は「たぶん」（表では ? を付ける）
LOOKBACK_SEC = 6 * 60          # もとの発言は、項目ができた時刻の6分前まで探す


def norm(s):
    return re.sub(r"[\s、。，．,.！？!?「」『』・（）()\[\]：:…ー-]", "", unicodedata.normalize("NFKC", s or "")).lower()


def bigrams(s):
    s = norm(s)
    return {s[i:i + 2] for i in range(len(s) - 1)} if len(s) > 1 else ({s} if s else set())


def jac(a, b):
    A, B = bigrams(a), bigrams(b)
    return len(A & B) / len(A | B) if A and B else 0.0


def contain(item, src):
    """項目の言葉のうち、もとの発言に入っている割合（そのまま使った度合い）"""
    A, B = bigrams(item), bigrams(src)
    return len(A & B) / len(A) if A and B else 0.0


def ts(x):
    if x is None or x == "":
        return None
    if isinstance(x, (int, float)):
        return x / 1000 if x > 1e11 else float(x)
    try:
        return datetime.fromisoformat(str(x).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def utterances(d):
    """文字起こし → 発言のリスト [{t, end, spk, text}]（t は実時刻の秒）"""
    out = []
    for seg in d.get("transcript", []):
        s0, s1 = ts(seg.get("seg_start")), ts(seg.get("seg_end"))
        if s0 is None:
            continue
        sgs = [g for g in seg.get("segments") or [] if (g.get("text") or "").strip()]
        if sgs:
            for g in sgs:
                out.append({"t": s0 + float(g.get("start") or 0), "end": s0 + float(g.get("end") or 0),
                            "spk": g.get("speaker") or "?", "text": g["text"].strip()})
        else:   # 話者分離なし：行ごとに区間の中で均等に置く
            lines = [l.strip() for l in re.split(r"[\n。]", seg.get("text") or "") if l.strip()]
            span = max(1.0, (s1 or s0 + 90) - s0)
            for i, l in enumerate(lines):
                l = re.sub(r"^話者[\d?]+：", "", l)
                out.append({"t": s0 + span * i / max(1, len(lines)), "end": s0 + span * (i + 1) / max(1, len(lines)), "spk": "?", "text": l})
    out.sort(key=lambda u: u["t"])
    # 話者の呼び名を登場順に 話者1, 話者2 …（名前は出さない）
    order = {}
    for u in out:
        if u["spk"] != "?" and u["spk"] not in order:
            order[u["spk"]] = f"話者{len(order) + 1}"
    for u in out:
        u["spk"] = order.get(u["spk"], "話者?")
    return out


def find_source(text, t, utts):
    """項目ができた時刻より前（LOOKBACK_SEC以内）で、いちばん近い発言。
    1つの発言で足りないときだけ、すぐ後の発言とつなげた形も使う（つなげた方が0.15以上よいとき）"""
    cands = [i for i, u in enumerate(utts) if t - LOOKBACK_SEC <= u["t"] <= t + 5]
    score = lambda src: max(contain(text, src), jac(text, src))
    best, bsc = None, 0.0
    for i in cands:
        sc = score(utts[i]["text"])
        if sc > bsc + 1e-9:
            best, bsc = (i, utts[i]["text"]), sc
    if bsc < 0.5:
        for i in cands:
            j = i + 1
            if j < len(utts) and utts[j]["t"] <= t + 5:
                src = utts[i]["text"] + " / " + utts[j]["text"]
                sc = score(src)
                if sc >= bsc + 0.15:
                    best, bsc = (i, src), sc
    if not best or bsc < SRC_MIN:
        return None
    i, src = best
    return {"i": i, "t": utts[i]["t"], "spk": utts[i]["spk"], "text": src, "score": bsc}


def src_str(s, n=30, spk=True):
    if not s:
        return ""
    return (s["spk"] + "：" if spk else "") + s["text"][:n] + ("（?）" if s["score"] < SURE else "")


def abstraction(c):
    if c is None:
        return "もとの発言なし"
    return "ほぼそのまま" if c >= 0.6 else "言い換え" if c >= 0.3 else "まとめ・抽象"


def load_notes(path, base_day):
    items = []
    for line in open(path, encoding="utf-8"):
        m = re.match(r"\s*(\d{1,2}):(\d{2})(?::(\d{2}))?\s+(.+)", line)
        if not m:
            continue
        h, mi, se, txt = int(m[1]), int(m[2]), int(m[3] or 0), m[4].strip()
        t = base_day.replace(hour=h, minute=mi, second=se, microsecond=0).timestamp()
        items.append({"who": "人", "via": "紙のメモ", "text": txt, "t": t, "id": f"n{len(items) + 1}"})
    return items


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("session")
    ap.add_argument("--notes")
    ap.add_argument("--with-texts", action="store_true")
    ap.add_argument("--window", type=float, default=10)
    a = ap.parse_args()
    d = json.load(open(a.session, encoding="utf-8"))
    ev = d.get("events", [])
    utts = utterances(d)
    rs = [ts(e["t"]) for e in ev if e.get("ev") == "rec_start"]
    t0 = ts(d.get("recStartedAt")) or (rs[0] if rs else None) or (utts[0]["t"] if utts else 0)
    mm = lambda t: f"{(t - t0) / 60:5.1f}" if t is not None else "–"
    mode = next((e.get("ai_mode") for e in ev if e.get("ev") == "session_start"), None) or d.get("aiMode") or "shown"

    # 人の項目：手で書いたカード（書き直した最後の文字で）、紙のメモ、テキストボックス
    human = [{"who": "人", "via": "手書きカード", "text": c.get("keyword", ""), "t": ts(c.get("addedAt")), "id": c["id"]}
             for c in d.get("cards", []) if c.get("source") == "manual" and c.get("keyword")]
    if a.notes:
        base_day = datetime.fromtimestamp(t0 or datetime.now().timestamp())
        human += load_notes(a.notes, base_day)
    if a.with_texts:
        for tb in d.get("texts", []):
            for k, line in enumerate(l.strip() for l in (tb.get("text") or "").split("\n")):
                if line:
                    human.append({"who": "人", "via": "テキストボックス", "text": line, "t": ts(tb.get("createdAt")), "id": f"{tb['id']}_{k}"})
    # AIの項目：作られたAIカード全部（画面に出していなくても）
    ai = [{"who": "AI", "via": c.get("kind", ""), "text": c.get("keyword", ""), "quote": c.get("quote", ""),
           "t": ts(c.get("addedAt")), "id": c["id"], "mention": c.get("mention", 1)}
          for c in d.get("cards", []) if c.get("source") == "ai"]

    for it in human + ai:
        probe = it["text"] + ("" if it["who"] == "人" else it.get("quote", ""))
        it["src"] = find_source(probe, it["t"] or 0, utts) if it["t"] else None
        # 抽象度はカードの言葉（keyword）で測る。AIの引用はもともと発言の写し
        it["contain"] = contain(it["text"], it["src"]["text"]) if it["src"] else None
        it["abs"] = abstraction(it["contain"])
        it["lag"] = round(it["t"] - it["src"]["t"]) if it["src"] else None

    # 組にする：文字が近い、または同じ発言（前後15秒）から作り文字も少し重なる
    pairs = []
    for h in human:
        for x in ai:
            if h["t"] is None or x["t"] is None or abs(h["t"] - x["t"]) > a.window * 60:
                continue
            s = max(jac(h["text"], x["text"]), jac(h["text"], x["quote"]))
            same_src = bool(h["src"] and x["src"] and min(h["src"]["score"], x["src"]["score"]) >= SURE
                            and abs(h["src"]["t"] - x["src"]["t"]) <= 15)
            if s >= NEAR or (same_src and s >= 0.05):
                pairs.append((s + (0.1 if same_src else 0), s, same_src, h, x))
    pairs.sort(key=lambda p: -p[0])
    used_h, used_a, matched = set(), set(), []
    for _, s, same_src, h, x in pairs:
        if h["id"] in used_h or x["id"] in used_a:
            continue
        used_h.add(h["id"]); used_a.add(x["id"])
        matched.append((s, same_src, h, x))
    h_only = [h for h in human if h["id"] not in used_h]
    a_only = [x for x in ai if x["id"] not in used_a]

    out = []
    w = out.append
    name = os.path.splitext(os.path.basename(a.session))[0]
    w(f"# 人の付箋とAIカードの比べ：{name}\n")
    w(f"- 班：{d.get('group_number', '')}　シート：{d.get('sheet') or 'kadai'}　AIの表示：{'出さない（記録のみ）' if mode == 'hidden' else '出していた'}")
    if mode != "hidden":
        w("- ※ AIカードを画面に出していた記録です。人の付箋がAIカードを見て書かれた可能性があります")
    span = (utts[-1]["end"] - utts[0]["t"]) / 60 if utts else 0
    w(f"- 文字起こし：発言 {len(utts)} 件（約 {span:.0f} 分）、話者 {len({u['spk'] for u in utts} - {'話者?'})} 人")
    w(f"- 人：{len(human)} 項目（" + "、".join(f"{k} {v}" for k, v in Counter(h['via'] for h in human).items()) + f"）　AI：{len(ai)} 枚")
    w(f"- 組にする条件：文字の近さ {NEAR} 以上、または同じ発言（前後15秒）から作ったもの。時刻の差は {a.window:g} 分以内\n")

    w("## 1. 重なり\n")
    w("| | 件数 |\n|---|---|")
    w(f"| 両方が拾った | {len(matched)} |\n| 人だけ | {len(h_only)} |\n| AIだけ | {len(a_only)} |")
    if human:
        w(f"\n- 人の項目のうち、AIも拾っていた割合：{len(matched) / len(human):.0%}")
    if ai:
        w(f"- AIカードのうち、人も拾っていた割合：{len(matched) / len(ai):.0%}")
    kinds = Counter(x["via"] for x in ai)
    if kinds:
        w("- AIカードの種類ごと（人も拾った／全部）：" + "、".join(
            f"{k} {sum(1 for _, _, _, x in matched if x['via'] == k)}/{v}" for k, v in kinds.most_common()))
    w("")

    def stat(items, label):
        lags = sorted(i["lag"] for i in items if i["lag"] is not None)
        ab = Counter(i["abs"] for i in items)
        med = lags[len(lags) // 2] if lags else None
        return (f"| {label} | {len(items)} | {sum(1 for i in items if i['src'])} | {med if med is not None else '–'} | "
                f"{ab['ほぼそのまま']} | {ab['言い換え']} | {ab['まとめ・抽象']} | {ab['もとの発言なし']} |")
    w("## 2. どこを、どれくらいで、どう切り取ったか\n")
    w("| | 項目 | もとの発言が見つかった | 発言からの遅れ（中央値・秒） | ほぼそのまま | 言い換え | まとめ・抽象 | もとの発言なし |\n|---|---|---|---|---|---|---|---|")
    w(stat(human, "人"))
    w(stat(ai, "AI"))
    w("\n- 遅れ：AIは約90秒ごとの区間がそろってから作るので、もともと1〜3分ほど遅れる。人の遅れと比べるときは、この差を頭に入れる")
    w("- （?）の付いたもとの発言は、近さが弱く当て推量に近い。まとめ・抽象の付箋ほど、もとの発言は1か所に決まらない")
    w("- 抽象度：カードの言葉のうち、もとの発言に入っている割合（0.6以上＝ほぼそのまま、0.3〜0.6＝言い換え、0.3未満＝まとめ・抽象）\n")

    w("## 3. 組になったもの（同じところを人もAIも拾った）\n")
    w("| 人（分） | 人の付箋 | AI（分） | AIカード | 近さ | 同じ発言から | もとの発言（人） |\n|---|---|---|---|---|---|---|")
    for s, same_src, h, x in sorted(matched, key=lambda m: m[2]["t"] or 0):
        w(f"| {mm(h['t'])} | {h['text'][:30]} | {mm(x['t'])} | {x['text'][:30]} | {s:.2f} | {'○' if same_src else ''} | "
          f"{src_str(h['src'])} |")
    w("\n## 4. 人だけが拾ったもの\n")
    w("| 分 | 書き方 | 人の付箋 | もとの発言 | 遅れ（秒） | 抽象度 |\n|---|---|---|---|---|---|")
    for h in sorted(h_only, key=lambda i: i["t"] or 0):
        w(f"| {mm(h['t'])} | {h['via']} | {h['text'][:40]} | {src_str(h['src'])} | {h['lag'] if h['lag'] is not None else ''} | {h['abs']} |")
    w("\n## 5. AIだけが拾ったもの\n")
    w("| 分 | 種類 | AIカード | 引用 | ×回数 |\n|---|---|---|---|---|")
    for x in sorted(a_only, key=lambda i: i["t"] or 0):
        w(f"| {mm(x['t'])} | {x['via']} | {x['text'][:30]} | {x['quote'][:30]} | {x['mention']} |")

    w("\n## 6. 時間の流れ（発言のどこから、だれが付箋・カードにしたか）\n")
    w("分は録音を始めてから。▲＝人、◆＝AI。もとの発言の分と、付箋・カードができた分を並べる\n")
    w("| もとの発言（分） | 話者 | 発言 | | 付箋・カード | できた（分） |\n|---|---|---|---|---|---|")
    for it in sorted(human + ai, key=lambda i: (i["src"]["t"] if i["src"] else i["t"] or 0)):
        s = it["src"]
        w(f"| {mm(s['t']) if s else '–'} | {s['spk'] if s else ''} | {src_str(s, 34, False)} | {'▲' if it['who'] == '人' else '◆'} | {it['text'][:30]} | {mm(it['t'])} |")

    base = os.path.splitext(a.session)[0]
    open(base + "_human_ai.md", "w", encoding="utf-8").write("\n".join(out) + "\n")
    pair_of = {}
    for s, same_src, h, x in matched:
        pair_of[h["id"]] = (x["text"], s, same_src); pair_of[x["id"]] = (h["text"], s, same_src)
    with open(base + "_human_ai.csv", "w", newline="", encoding="utf-8-sig") as f:
        cw = csv.writer(f)
        cw.writerow(["だれ", "書き方・種類", "内容", "AIの引用", "できた分", "もとの発言の分", "話者", "もとの発言", "遅れ秒",
                     "そのまま度", "抽象度", "組になった相手", "近さ", "同じ発言から"])
        for it in sorted(human + ai, key=lambda i: i["t"] or 0):
            s = it["src"]; p = pair_of.get(it["id"])
            cw.writerow([it["who"], it["via"], it["text"], it.get("quote", ""), mm(it["t"]).strip(), mm(s["t"]).strip() if s else "",
                         s["spk"] if s else "", s["text"] if s else "", it["lag"] if it["lag"] is not None else "",
                         f"{it['contain']:.2f}" if it["contain"] is not None else "", it["abs"],
                         p[0] if p else "", f"{p[1]:.2f}" if p else "", "○" if p and p[2] else ""])
    print("保存:", base + "_human_ai.md", "/", base + "_human_ai.csv")
    print(f"両方 {len(matched)}　人だけ {len(h_only)}　AIだけ {len(a_only)}")


if __name__ == "__main__":
    main()
