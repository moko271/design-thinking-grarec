# -*- coding: utf-8 -*-
"""
kadai.html：日本語入力（変換）の確定のEnterで、入力が終わってしまう問題を直す（2026-10-13）
  ・メンバー名の入力、カードの書き直し：変換中のEnter（isComposing / keyCode 229）は無視し、確定後のEnterだけで終える
  ・テキストボックス：変換中のEscで編集が終わらないようにする
  ・道具の説明に「メンバー名はダブルクリックで直せる」を足す
何度実行しても同じ結果になる。
"""
import sys
p = sys.argv[1] if len(sys.argv) > 1 else "static/kadai.html"
s = open(p, encoding="utf-8").read()
if "const composing" in s:
    print("already patched"); sys.exit(0)
def rep(old, new):
    global s
    assert s.count(old) == 1, old[:80]
    s = s.replace(old, new)
rep("function editMember(i, btn){", "const composing = e => e.isComposing || e.keyCode === 229;   // 日本語の変換中\nfunction editMember(i, btn){")
rep("  inp.addEventListener('keydown', e => { if (e.key === 'Enter') inp.blur(); });",
    "  inp.addEventListener('keydown', e => { if (e.key === 'Enter' && !composing(e)){ e.preventDefault(); inp.blur(); } });")
rep("  kw.addEventListener('keydown', e => { if (e.key === 'Enter'){ e.preventDefault(); kw.blur(); } });",
    "  kw.addEventListener('keydown', e => { if (e.key === 'Enter' && !composing(e)){ e.preventDefault(); kw.blur(); } });")
rep("  const key = e => { if (e.key === 'Escape'){ e.preventDefault(); tx.blur(); } };",
    "  const key = e => { if (e.key === 'Escape' && !composing(e)){ e.preventDefault(); tx.blur(); } };")
rep("const DEFAULT_HINT = 'カードは右のトレイからドラッグ（左のスライドに落とすとそのスライドへ）。ダブルクリックで書き直せる';",
    "const DEFAULT_HINT = 'カードは右のトレイからドラッグ（左のスライドに落とすとそのスライドへ）。カードやメンバー名はダブルクリックで書き直せる';")
open(p, "w", encoding="utf-8").write(s)
print("patched")
