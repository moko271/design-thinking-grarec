# -*- coding: utf-8 -*-
"""
kadai.html：AIのカードを画面に出さないモード（/kadai?ai=hidden、/design?ai=hidden）（2026-10-13）
  目的：同じ話し合いで「ファシリテーターが手で書いた付箋」と「AIが作ったカード」を比べる
  ・AIのカードは今までどおり同じ間隔・同じ作り方で作り、記録にも残す（入れ替えの動きも同じ）。画面には出さない
  ・トレイには、自分で書いたカード（ファシリテーターの付箋）だけが出る。書いた時刻は自動で残る
  ・「いまの話をカードにする」「これまでのカード」「カードの種類」「使われ方の数字」は隠す。カードが届いた知らせも出さない
  ・上のバーに「AI非表示（記録のみ）」と出す。モードはセッションに保存され、再開しても同じ
  ・記録：session_start の ai_mode
何度実行しても同じ結果になる。
"""
import sys

p = sys.argv[1] if len(sys.argv) > 1 else "static/kadai.html"
s = open(p, encoding="utf-8").read()
if "aiHidden()" in s:
    print("already patched"); sys.exit(0)


def rep(old, new):
    global s
    assert s.count(old) == 1, old[:80]
    s = s.replace(old, new)


rep("/* usage log */", """/* AIを出さないモード */
.ai-badge{background:#FDE68A;color:#78350F;border-radius:999px;padding:3px 10px;font-size:12px;font-weight:700;white-space:nowrap}
body.ai-hidden .ttabs,body.ai-hidden #nowBtn,body.ai-hidden .legend,body.ai-hidden .log,body.ai-hidden .tray-h p{display:none}

/* usage log */""")

rep('''  <span class="group" id="groupBadge" hidden></span>''',
    '''  <span class="group" id="groupBadge" hidden></span>
  <span class="ai-badge" id="aiBadge" hidden title="AIのカードは作って記録しますが、画面には出しません">AI非表示（記録のみ）</span>''')

# 表示：AIのカードはトレイ・これまでのカードに出さない
rep('''  else S.cards.filter(c => c.place === 'tray').slice().reverse().forEach(c => list.appendChild(cardEl(c)));''',
    '''  else S.cards.filter(c => c.place === 'tray' && !(aiHidden() && c.source === 'ai')).slice().reverse().forEach(c => list.appendChild(cardEl(c)));''')
rep('''    e.innerHTML = trayView === 'history' ? 'まだ振り返るカードはありません<br>カードが入れ替わると、ここに残ります\'''',
    '''    e.innerHTML = aiHidden() ? 'ここには自分で書いたカードが出ます<br>下の欄に書いて「追加」（AIのカードは記録だけしています）'
      : trayView === 'history' ? 'まだ振り返るカードはありません<br>カードが入れ替わると、ここに残ります\'''')
rep('''  toast(added ? `カードが ${added} 枚届きました` : '新しいカードはありませんでした（同じ話題は ×回数 が増えています）');''',
    '''  if (!aiHidden()) toast(added ? `カードが ${added} 枚届きました` : '新しいカードはありませんでした（同じ話題は ×回数 が増えています）');''')

# モードの保存と画面への反映
rep("""'voice','speech','balTrace','recStartedAt','slides','cur','texts'];""",
    """'voice','speech','balTrace','recStartedAt','slides','cur','texts','aiMode'];""")
rep('''function applyHeader(){''', '''const aiHidden = () => S.aiMode === 'hidden';
function applyAiMode(){
  document.body.classList.toggle('ai-hidden', aiHidden());
  $('aiBadge').hidden = !aiHidden();
}
if (new URLSearchParams(location.search).get('ai') === 'hidden') setTimeout(() => {   // 開始画面にも出す
  const h = document.querySelector('#startForm h1'); if (h) h.textContent += '（AIのカードは画面に出さない）';
}, 0);
function applyHeader(){
  applyAiMode();''')
rep('''    slides: SHEET === 'design' ? DT_PHASES.map((t, i) => ({id: 's' + (i + 1), tpl: t})) : [{id: 's1'}], cur: 's1', texts: []});''',
    '''    slides: SHEET === 'design' ? DT_PHASES.map((t, i) => ({id: 's' + (i + 1), tpl: t})) : [{id: 's1'}], cur: 's1', texts: [],
    aiMode: new URLSearchParams(location.search).get('ai') === 'hidden' ? 'hidden' : 'shown'});''')
rep('''  log('session_start', null, {sheet: SHEET, config: {quick: QUICK,''', '''  log('session_start', null, {sheet: SHEET, ai_mode: S.aiMode, config: {quick: QUICK,''')

open(p, "w", encoding="utf-8").write(s)
print("patched")
