# -*- coding: utf-8 -*-
"""カード選びのプロンプトに「話者1：」表記の説明を足す。何度実行しても同じ結果になる。"""
import sys
p = sys.argv[1] if len(sys.argv) > 1 else "app.py"
s = open(p, encoding="utf-8").read()
marker = "行頭の「話者1：」などは"
if marker not in s:
    old = '''        "・1回しか出ず誰も拾わなかったが、話し合いを広げられそうな視点があれば1枚入れ、lone を true にする\\n"'''
    new = '''        "・発話の行頭の「話者1：」などは話し手の区別（名前ではない）。誰か1人だけが言って他の人が反応していない視点や、\\n"
        "  話者によって意見が分かれている点を見つける手がかりにする。keyword や quote には話者番号を書かない\\n"
        "・1回しか出ず誰も拾わなかったが、話し合いを広げられそうな視点があれば1枚入れ、lone を true にする\\n"'''
    assert s.count(old) == 1, "anchor not found"
    s = s.replace(old, new)
    open(p, "w", encoding="utf-8").write(s)
print("patched", p)
