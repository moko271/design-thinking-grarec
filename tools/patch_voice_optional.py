# -*- coding: utf-8 -*-
"""
kadai.html：はじめに出る「声を登録する（1人5秒）」の画面を、ふだんは出さないようにする（2026-10-27）
  ・セッションを始めたら、そのまま「登録せずにはじめる」と同じ状態で始まる（記録：voice_off）
  ・声を登録したいときは、URLの最後に ?voice=on を付ける（例：/kadai?voice=on、/kadai?ai=hidden&voice=on）
  ・始めたあとでも、右の「話のバランス」の「声を登録しなおす」から登録できる
何度実行しても同じ結果になる。
"""
import sys

p = sys.argv[1] if len(sys.argv) > 1 else "static/kadai.html"
s = open(p, encoding="utf-8").read()
if "VOICE_AT_START" in s:
    print("already patched"); sys.exit(0)


def rep(old, new):
    global s
    assert s.count(old) == 1, old[:80]
    s = s.replace(old, new)


rep('''  applyHeader(); render(); paintBalance(); save();
  openVoice();
};''', '''  applyHeader(); render(); paintBalance(); save();
  if (VOICE_AT_START) openVoice();
  else { SLOTS.forEach(v => S.voice.slots[v] || (S.voice.slots[v] = {name: '', done: false})); finishVoice(false); }
};
// 声の登録は、URLに ?voice=on があるときだけ最初に出す（ふだんは出さない）
const VOICE_AT_START = new URLSearchParams(location.search).get('voice') === 'on';''')
open(p, "w", encoding="utf-8").write(s)
print("patched")
