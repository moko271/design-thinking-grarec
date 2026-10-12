# -*- coding: utf-8 -*-
"""
課題シート（/kadai）のカードの選び方を、7/15実験の文字起こしで試すスクリプト。
本番と同じ build_card_prompt / _select_cards を使い、「最初3分 → 以降10分」の区切りで
どんなカードが出るかを表示する（simulate_offline.py の課題シート版）。

使い方（grarec_light フォルダで）:
    python tools/try_cards.py 1          # 班1
    python tools/try_cards.py 5 --save   # 班5、結果を tools/try_cards_班5.txt にも保存
"""
import os
import re
import sys
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app as A  # noqa: E402  （.env の OPENAI_API_KEY を使う）

TRANSCRIPT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                              "grarec_transcribe_tool", "全班_文字起こし結果")
LINE = re.compile(r"^\[(\d\d):(\d\d):(\d\d)\]\s*(.+?)\s*(⚠️.*)?$")


def load(group):
    path = os.path.join(TRANSCRIPT_DIR, f"班{group}_文字起こし_時系列.txt")
    rows = []
    for line in open(path, encoding="utf-8"):
        m = LINE.match(line.strip())
        if m:
            h, mi, s, text = int(m[1]), int(m[2]), int(m[3]), m[4]
            rows.append((h * 3600 + mi * 60 + s, text))
    return rows


def windows(rows, first=180, later=600):
    if not rows:
        return []
    out, start, size, buf = [], rows[0][0], first, []
    for t, text in rows:
        while t >= start + size:
            out.append((start, buf))
            start, size, buf = start + size, later, []
        buf.append(text)
    out.append((start, buf))
    return [(s, b) for s, b in out if b]


def main():
    group = sys.argv[1] if len(sys.argv) > 1 else "1"
    save = "--save" in sys.argv
    rows = load(group)
    t0 = rows[0][0]
    prev, existing, lines = "", [], []
    for i, (start, buf) in enumerate(windows(rows)):
        memo = "\n".join(buf)
        sp, up = A.build_card_prompt(memo, prev[-500:], existing, i == 0, 6)
        raw, model = A.card_chat_json(sp, up)
        cards = A._select_cards(json.loads(raw or "{}").get("cards", []), 6)
        m = (start - t0) // 60
        lines.append(f"\n=== {m}分〜（{'最初3分' if i == 0 else '10分'}・{len(memo)}文字） ===")
        for c in cards:
            again = "（再登場）" if c["keyword"] in existing else ""
            lone = " ★ひとことだけ" if c["lone"] else ""
            lines.append(f"[{c['kind_label']}] {c['keyword']}{again}{lone}\n      「{c['quote']}」 ← {c['reason']}")
        existing += [c["keyword"] for c in cards if c["keyword"] not in existing]
        prev = memo
        print("\n".join(lines[-(len(cards) + 1):]), flush=True)
    if save:
        out = os.path.join(os.path.dirname(os.path.abspath(__file__)), f"try_cards_班{group}.txt")
        open(out, "w", encoding="utf-8").write("\n".join(lines))
        print("\n保存:", out)


if __name__ == "__main__":
    main()
