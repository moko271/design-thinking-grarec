# -*- coding: utf-8 -*-
"""
kadai.html：前に届いたカードを振り返れるようにする（2026-10-13）
  ・トレイの上にタブ「いま届いたカード」「これまでのカード」。ふだんは今と同じ（いま届いたカードだけ）
  ・「これまでのカード」：トレイ以外のAIカード（入れ替わって下がったもの・シートに置いたもの）を、届いた回ごとに新しい順で並べる
      シートに置いたもの → 「✓ シート○枚目にある」（動かせない）
      下がったもの       → シートに直接ドラッグできる／「トレイに戻す」で戻せる
  ・記録：history_open / history_close / history_restore、ここからシートに置いたときは place（via: history）
  ・自分で消したカードは出さない
何度実行しても同じ結果になる。
"""
import sys

p = sys.argv[1] if len(sys.argv) > 1 else "static/kadai.html"
s = open(p, encoding="utf-8").read()
if "renderHistory" in s:
    print("already patched"); sys.exit(0)


def rep(old, new, n=1):
    global s
    c = s.count(old)
    assert c == n, (c, old[:90])
    s = s.replace(old, new)


rep("/* usage log */", r"""/* これまでのカード */
.ttabs{display:flex;gap:4px;margin-top:10px;background:var(--panel-2);border-radius:8px;padding:3px}
.ttab{flex:1;border:0;background:transparent;border-radius:6px;padding:6px 4px;font-size:12.5px;color:var(--ink-2)}
.ttab.on{background:#fff;color:var(--ink);font-weight:700;box-shadow:0 1px 2px rgba(0,0,0,.15)}
.ttab b{display:inline-block;min-width:18px;border-radius:999px;background:var(--line);color:var(--ink);font-size:11px;padding:0 5px;margin-left:4px;font-weight:700}
.hbatch{font-size:12px;color:var(--ink-2);font-weight:700;display:flex;align-items:center;gap:8px;margin-top:4px}
.hbatch::after{content:"";flex:1;height:1px;background:var(--line)}
.list .card.old{background:#FAFAF7;border-style:dashed}
.list .card.old .x{display:none}
.list .card.old.fixed{cursor:default}
.card .hback{display:inline-block;margin-top:6px;border:1px solid var(--accent);background:#fff;color:var(--accent);border-radius:6px;font-size:12px;padding:3px 10px}
.card .hon{display:block;margin-top:5px;font-size:11.5px;color:var(--ok);font-weight:700}

/* usage log */""")

rep('''      <h2>話し合いから出たカード</h2>''', '''      <h2>話し合いから出たカード</h2>
      <div class="ttabs" role="tablist" aria-label="カードの表示">
        <button class="ttab on" id="tabNow" type="button" role="tab" aria-selected="true">いま届いたカード <b id="cntNow">0</b></button>
        <button class="ttab" id="tabHist" type="button" role="tab" aria-selected="false">これまでのカード <b id="cntHist">0</b></button>
      </div>''')

# cardEl：これまでのカード用の見た目
rep('''function cardEl(c){''', '''function cardEl(c, opt){
  opt = opt || {};''')
rep('''  enableDrag(el, c);
  return el;''', '''  if (opt.history){
    el.classList.add('old');
    el.querySelectorAll('.place').forEach(n => n.remove());
    if (c.place === 'sheet'){
      el.classList.add('fixed');
      const on = document.createElement('span'); on.className = 'hon'; on.textContent = `✓ シート ${slideNo(c.slide)}枚目にある`;
      el.appendChild(on);
      return el;                                     // シートにあるものはここからは動かさない
    }
    const bb = document.createElement('button'); bb.type = 'button'; bb.className = 'hback'; bb.textContent = 'トレイに戻す';
    bb.addEventListener('pointerdown', e => e.stopPropagation());
    bb.onclick = e => { e.stopPropagation(); c.place = 'tray'; log('history_restore', c); render(); save(); toast('トレイに戻しました'); };
    el.appendChild(bb);
  }
  enableDrag(el, c);
  return el;''')

# render：タブに応じてトレイの中身を出し分ける
rep('''  list.querySelectorAll('.card').forEach(n => n.remove());
  body.querySelectorAll('.card').forEach(n => n.remove());''', '''  list.querySelectorAll('.card,.hbatch').forEach(n => n.remove());
  body.querySelectorAll('.card').forEach(n => n.remove());''')
rep('''  S.cards.filter(c => c.place === 'tray').slice().reverse().forEach(c => list.appendChild(cardEl(c)));''',
    '''  const histCards = S.cards.filter(c => c.source === 'ai' && (c.place === 'archived' || c.place === 'sheet'));
  $('cntNow').textContent = S.cards.filter(c => c.place === 'tray').length; $('cntHist').textContent = histCards.length;
  if (trayView === 'history') renderHistory(list, histCards);
  else S.cards.filter(c => c.place === 'tray').slice().reverse().forEach(c => list.appendChild(cardEl(c)));''')
rep('''    e.innerHTML = S.cycle ? 'トレイのカードはすべてシートに置きました' : 'まだカードはありません<br>「録音をはじめる」を押すと、話し合いからカードが届きます';''',
    '''    e.innerHTML = trayView === 'history' ? 'まだ振り返るカードはありません<br>カードが入れ替わると、ここに残ります'
      : S.cycle ? 'トレイのカードはすべてシートに置きました' : 'まだカードはありません<br>「録音をはじめる」を押すと、話し合いからカードが届きます';''')
rep('''function paintBodyHint(){''', '''// ================= これまでのカード =================
let trayView = 'now';
function renderHistory(list, cards){
  // 何回目に届いたか（c.batch）ごとに、新しい回から。時刻は、その回のカードを作った記録から
  const madeAt = S.events.filter(e => e.ev === 'cards').map(e => Date.parse(e.t));
  const label = k => {
    const t = madeAt[k - 1];
    if (t && S.recStartedAt) return `${Math.max(0, Math.round((t - S.recStartedAt) / 60000))}分ごろに届いたカード`;
    return `${k}回目に届いたカード`;
  };
  const groups = {};
  cards.forEach(c => { const k = c.batch || 0; (groups[k] = groups[k] || []).push(c); });
  Object.keys(groups).map(Number).sort((a, b) => b - a).forEach(k => {
    const h = document.createElement('div'); h.className = 'hbatch'; h.textContent = k ? label(k) : 'そのほか';
    list.appendChild(h);
    groups[k].forEach(c => list.appendChild(cardEl(c, {history: true})));
  });
}
function setTrayView(v){
  if (v === trayView) return;
  trayView = v;
  $('tabNow').classList.toggle('on', v === 'now'); $('tabNow').setAttribute('aria-selected', String(v === 'now'));
  $('tabHist').classList.toggle('on', v === 'history'); $('tabHist').setAttribute('aria-selected', String(v === 'history'));
  log(v === 'history' ? 'history_open' : 'history_close');
  render(); save();
}
$('tabNow').onclick = () => setTrayView('now');
$('tabHist').onclick = () => setTrayView('history');

function paintBodyHint(){''')

# ここからシートに置いたときの記録
rep('''      const fromTray = c.place === 'tray';
      c.x = +x.toFixed(2); c.y = +y.toFixed(2); c.place = 'sheet'; c.slide = S.cur;
      log(fromTray ? 'place' : 'move', c, Object.assign({slide: slideNo(S.cur)}, fromTray ? {secToPlace: Math.round((Date.now() - Date.parse(c.addedAt)) / 1000)} : {}));''',
    '''      const fromHist = c.place === 'archived';
      const fromTray = c.place === 'tray' || fromHist;
      c.x = +x.toFixed(2); c.y = +y.toFixed(2); c.place = 'sheet'; c.slide = S.cur;
      log(fromTray ? 'place' : 'move', c, Object.assign({slide: slideNo(S.cur)}, fromHist ? {via: 'history'} : {},
        fromTray ? {secToPlace: Math.round((Date.now() - Date.parse(c.addedAt)) / 1000)} : {}));''')
rep('''        const fromTray0 = c.place === 'tray';''', '''        const fromTray0 = c.place === 'tray' || c.place === 'archived';''')

# 新しいカードが届いたら「いま届いたカード」に戻す（見逃さないように）
rep('''  S.cards.filter(c => c.place === 'tray' && c.source === 'ai').forEach(c => { c.place = 'archived'; log('swap', c); });''',
    '''  if (trayView === 'history') setTrayView('now', true);
  S.cards.filter(c => c.place === 'tray' && c.source === 'ai').forEach(c => { c.place = 'archived'; log('swap', c); });''')
rep('''function setTrayView(v){
  if (v === trayView) return;''', '''function setTrayView(v, auto){
  if (v === trayView) return;''')
rep('''  log(v === 'history' ? 'history_open' : 'history_close');''', '''  log(v === 'history' ? 'history_open' : 'history_close', null, auto ? {auto: true} : undefined);''')

open(p, "w", encoding="utf-8").write(s)
print("patched")
