#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sim_cards.py — 7/15の文字起こしを、今の /kadai と同じ手順でカードにする（OpenAI API を使う。Macの .venv で実行）

  python sim/sim_cards.py --dry-run            # 何回APIを呼ぶか・おおよその費用だけ表示
  python sim/sim_cards.py                      # 全班 × 全設定 × 2回
  python sim/sim_cards.py --groups 1 5 --repeats 1 --variants plan3-10_max6

やっていること
  ・設定（variant）ごとに、カードを出す時刻（最初3分→以降10分 など）で文字起こしを区切る
  ・前回から今回までの発言を「今回の区間」、前回の区間の最後500字を「前の文脈」として、
    app.py の build_card_prompt / card_chat_json / _select_cards（本番と同じ処理）に通す
  ・「すでに出ているカード」は、直前に届いたカード（誰もシートに置かなかった場合のトレイ）とする
  ・結果は sim/out/cards/<variant>/班<g>_run<r>.json に保存。すでにあればスキップするので、途中で止めても続きから再開できる
注意
  ・7/15は録音に15〜20%の抜けがあり、その部分の発言はカードにならない
  ・実際のアプリは90秒区間の文字起こしを待ってから抽出するが、ここでは時刻で区切っている（最大90秒の差）
"""
import argparse, json, os, sys, time
from sim_common import HERE, ROOT, GROUPS, load_transcript, schedule

sys.path.insert(0, ROOT)
OUT = os.path.join(HERE, "out", "cards")

VARIANTS = {
    # 名前: (間隔プラン, 1回の上限枚数, 選び方のルール)
    "plan3-10_max6":         ("3-10", 6, "current"),   # 今の設定
    "plan3-10_max4":         ("3-10", 4, "current"),
    "plan3-10_max8":         ("3-10", 8, "current"),
    "plan3-10_max6_noprio":  ("3-10", 6, "no_priority"),
    "plan3_max6":            ("3", 6, "current"),
    "plan5_max6":            ("5", 6, "current"),
    "plan10_max6":           ("10", 6, "current"),
}
NO_PRIORITY_LINE = "・問い(question)とちがい(difference)が話し合いに出ていれば、優先して1枚ずつ入れる\n  （話し合いの次の一歩や、他の人の見方に気づくきっかけになるため）\n"


def windows(rows, plan):
    end = rows[-1][0] if rows else 0
    out, last = [], -1
    for t in schedule(plan, end):
        seg = [x for (s, x) in rows if last < s <= t]
        out.append((t, "\n".join(seg))); last = t
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--groups", type=int, nargs="*", default=GROUPS)
    ap.add_argument("--variants", nargs="*", default=list(VARIANTS))
    ap.add_argument("--repeats", type=int, default=2)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    calls = 0
    plan_jobs = []
    for v in a.variants:
        plan, mx, rule = VARIANTS[v]
        for g in a.groups:
            rows = load_transcript(g)
            ws = windows(rows, plan)
            for r in range(1, a.repeats + 1):
                path = os.path.join(OUT, v, f"班{g}_run{r}.json")
                if os.path.exists(path): continue
                calls += sum(1 for _, m in ws if len(m.strip()) >= 10)
                plan_jobs.append((v, plan, mx, rule, g, r, ws, path))
    print(f"API呼び出し予定: {calls}回（1回あたり約0.5〜1円の見込み → 約{calls * 0.5:.0f}〜{calls:.0f}円）")
    if a.dry_run or not plan_jobs: return

    import app as A   # .env の OPENAI_API_KEY と CARD_MODEL を使う
    for v, plan, mx, rule, g, r, ws, path in plan_jobs:
        prev, existing, batches, extracted_once = "", [], [], False
        for i, (t, memo) in enumerate(ws):
            if len(memo.strip()) < 10:
                batches.append({"t_sec": t, "cards": [], "skipped": "発言なし"}); continue
            first = not extracted_once          # アプリと同じく、最初の抽出だけ「最初のカード」用の指示になる
            sp, up = A.build_card_prompt(memo, prev[-500:], existing, first, mx)
            if rule == "no_priority" and not first:
                assert NO_PRIORITY_LINE in up, "プロンプトの文言が変わっています。NO_PRIORITY_LINE を更新してください"
                up = up.replace(NO_PRIORITY_LINE, "")
            extracted_once = True
            for attempt in range(3):
                try:
                    raw, model = A.card_chat_json(sp, up); break
                except Exception as e:
                    print("  再試行:", e); time.sleep(3)
            else:
                batches.append({"t_sec": t, "cards": [], "error": True}); continue
            try:
                cards = A._select_cards(json.loads(raw or "{}").get("cards", []), mx)
            except json.JSONDecodeError:
                cards = []
            batches.append({"t_sec": t, "model": model, "memo_chars": len(memo), "cards": cards, "window_text": memo})
            existing = [c["keyword"] for c in cards] or existing
            prev = memo
        os.makedirs(os.path.dirname(path), exist_ok=True)
        json.dump({"variant": v, "plan": plan, "max_cards": mx, "rule": rule, "group": g, "run": r,
                   "batches": batches}, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print(f"保存 {v} 班{g} run{r}: {sum(len(b['cards']) for b in batches)}枚 / {len(batches)}回")


if __name__ == "__main__":
    main()
