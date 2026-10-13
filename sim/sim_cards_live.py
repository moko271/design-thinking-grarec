#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sim_cards_live.py — 文字起こしをAIに渡すタイミングを、本番の /kadai と同じにしてカードを作り直す（APIを使う）

sim_cards.py との違い（本番の kadai.html の動きに合わせた所）
  1. 90秒ごとの区間：録音は90秒ごとに区切って文字起こしされ、カードを作る時刻にも区切られる。
     1区間の文字起こしは1つの文章（行の区切りなし）として届き、区間どうしは改行でつながる。
     → 文字起こしの行（PCで作ったもの）を区間ごとにまとめて、同じ形にする
  2. 「まだカードにしていない話」が20文字未満なら作らず、次の回に持ち越す（本番と同じ）
  3. 「最初のカード」用の指示は、最初にカードが届くまで続く（本番と同じ）
  4. AIに渡す「もう出ているカード」は、その時点でシートとトレイにあるカード全部（本番と同じ）。
     シートに何を置くかは人しだいなので、「毎回 n 枚置く」と仮定する（--place、既定1）
  5. カードが届く時刻 ＝ 予定の時刻 ＋ 最後の区間の文字起こしの時間（仮定 8秒）＋ 実際にAPIにかかった時間
  6. 届いたカードは本番と同じ規則で扱う（置かなかったAIカードは入れ替え、似たカードは ×回数、前に下げたカードは再登場）
変えていない所：文字起こしそのもの（PCで後から作ったもの）、話者の区別（なし＝声を登録しなかったときと同じ）

使い方（grarec_light フォルダで、.venv を有効にして）
    python sim/sim_cards_live.py --groups 31 6 --dry-run    # APIを何回呼ぶか
    python sim/sim_cards_live.py --groups 31 6              # → sim/out/cards_live/班31_place1.json
その後 python tools/make_demo.py 31 でデモを作ると、こちらの結果が使われる。
"""
import argparse
import json
import os
import time

from sim_common import HERE, ROOT, load_transcript, schedule, similar

OUT = os.path.join(HERE, "out", "cards_live")
SEG_SEC = 90            # kadai.html の SEGMENT_MS
MIN_CHARS = 20          # kadai.html：これ未満なら作らず持ち越す
MAX_CARDS = 6
TRANSCRIBE_DELAY = 8.0  # 仮定：カードを作る時刻に区切った最後の区間の文字起こしにかかる秒数


def segments(rows, card_times, end):
    """(区間の終わりの秒, その区間の文字起こし) の列。90秒ごと＋カードを作る時刻で区切る"""
    cuts = sorted(set([k * SEG_SEC for k in range(1, int(end // SEG_SEC) + 2)] + list(card_times)))
    out, last = [], -1
    for c in cuts:
        txt = " ".join(x for (s, x) in rows if last < s <= c).strip()
        if txt:
            out.append((c, txt))
        last = c
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--groups", type=int, nargs="+", required=True)
    ap.add_argument("--place", type=int, default=1, help="毎回トレイから何枚シートに置くと仮定するか")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    jobs = []
    for g in a.groups:
        rows = load_transcript(g)
        end = rows[-1][0] if rows else 0
        times = schedule("3-10", end)
        jobs.append((g, rows, times, segments(rows, times, end)))
    if a.dry_run:
        print(f"API呼び出し予定: 最大 {sum(len(t) for _, _, t, _ in jobs)} 回（数十円程度）")
        return

    import app as A   # .env の OPENAI_API_KEY と CARD_MODEL を使う
    os.makedirs(OUT, exist_ok=True)
    for g, rows, times, segs in jobs:
        cards, batches = [], []          # cards: {kw, kind, place: tray|sheet|archived, mention}
        pending, last_extracted, cycle, seg_i = [], "", 0, 0
        for t in times:
            while seg_i < len(segs) and segs[seg_i][0] <= t:
                pending.append(segs[seg_i][1]); seg_i += 1
            memo = "\n".join(pending).strip()
            if len(memo) < MIN_CHARS:
                batches.append({"t_sec": t, "cards": [], "skipped": "まだカードにできるほどの話がない（持ち越し）"}); continue
            existing = [c["kw"] for c in cards if c["place"] in ("sheet", "tray")]
            sp, up = A.build_card_prompt(memo, last_extracted[-500:], existing[:60], cycle == 0, MAX_CARDS)
            t0 = time.time()
            for attempt in range(3):
                try:
                    raw, model = A.card_chat_json(sp, up); break
                except Exception as e:
                    print("  再試行:", e); time.sleep(3)
            else:
                batches.append({"t_sec": t, "cards": [], "error": True}); continue   # 本番と同じく、話は持ち越し
            api_sec = round(time.time() - t0, 1)
            try:
                got = A._select_cards(json.loads(raw or "{}").get("cards", []), MAX_CARDS)
            except json.JSONDecodeError:
                got = []
            pending, last_extracted = [], memo
            # 本番の deliver() と同じ扱い
            for c in cards:
                if c["place"] == "tray":
                    c["place"] = "archived"
            cycle += 1
            new_now = []
            for n in got:
                live = next((c for c in cards if c["place"] in ("sheet", "tray") and similar(c["kw"], n["keyword"])), None)
                if live:
                    live["mention"] += 1; continue
                old = next((c for c in cards if c["place"] == "archived" and similar(c["kw"], n["keyword"])), None)
                if old:
                    old["place"] = "tray"; old["mention"] += 1; new_now.append(old); continue
                c = {"kw": n["keyword"], "kind": n.get("kind"), "place": "tray", "mention": 1}
                cards.append(c); new_now.append(c)
            for c in new_now[:a.place]:
                c["place"] = "sheet"
            batches.append({"t_sec": t, "deliver_sec": round(t + TRANSCRIBE_DELAY + api_sec, 1), "api_sec": api_sec,
                            "model": model, "memo_chars": len(memo), "existing_n": len(existing),
                            "cards": got, "window_text": memo})
        path = os.path.join(OUT, f"班{g}_place{a.place}.json")
        json.dump({"mode": "live", "group": g, "plan": "3-10", "max_cards": MAX_CARDS, "place_per_batch": a.place,
                   "assumptions": {"segment_sec": SEG_SEC, "min_chars": MIN_CHARS, "transcribe_delay_sec": TRANSCRIBE_DELAY,
                                   "transcript": "PCで後から作った文字起こし（話者なし）"},
                   "batches": batches}, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        n = sum(len(b["cards"]) for b in batches)
        delays = [b["deliver_sec"] - b["t_sec"] for b in batches if "deliver_sec" in b]
        print(f"保存 班{g}: {n}枚 / {len(batches)}回（届くまでの遅れ 平均 {sum(delays) / max(1, len(delays)):.0f}秒）→ {path}")


if __name__ == "__main__":
    main()
