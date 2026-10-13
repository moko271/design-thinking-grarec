# -*- coding: utf-8 -*-
"""
kadai.html：デザイン思考ワークシート（7/15の ①さぐる ②きづく ③ひらめく）を、今の課題シートと同じ画面で使えるようにする（2026-10-13）
  開き方：/design（app.py にルートを足す）または /kadai?sheet=design
  ・3つのフェーズを3枚のスライドにする（①さぐる ②きづく ③ひらめく）。各スライドにエリアの枠と説明が薄く出る
  ・カードやテキストボックスの置き方、トレイ、これまでのカード、話のバランスは課題シートと同じ
  ・見出しは「テーマ」と「チームメンバ」。テーマは開始画面で書いたもの（AIにも課題の内容として渡す）
  ・カードを置いたとき、どのエリアに置いたかを記録する（place / move の area）
  ・課題シートとは保存場所を分ける（続きから再開がまざらないように）
何度実行しても同じ結果になる。
"""
import sys

p = sys.argv[1] if len(sys.argv) > 1 else "static/kadai.html"
s = open(p, encoding="utf-8").read()
if "DT_TEMPLATES" in s:
    print("already patched"); sys.exit(0)


def rep(old, new, n=1):
    global s
    c = s.count(old)
    assert c == n, (c, old[:90])
    s = s.replace(old, new)


# ---------- CSS ----------
rep("/* usage log */", r"""/* デザイン思考ワークシートのエリア（枠と説明は背景。カードはその上に置く） */
.area{position:absolute;border:1.5px solid var(--ac);background:var(--ab);border-radius:.6cqw;padding:.6cqw .8cqw;pointer-events:none;z-index:0;overflow:hidden}
.area .al{font-size:1.45cqw;font-weight:700;color:#222}
.area .ah{font-size:1.02cqw;color:#555;line-height:1.45;margin-top:.3cqw}
.s-body.dt .s-hint{display:none}
.thumb .ph{grid-column:2;display:block;font-size:10.5px;color:var(--ink-2);margin-top:2px}
.thumb.on .ph{color:var(--accent);font-weight:700}
.s-th.wide{grid-column:span 1}
.s-td input.theme{text-align:left;font-weight:700}

/* usage log */""")

# ---------- どちらのシートか ----------
rep("""const STORE_KEY = 'grarec_kadai_current';""", r"""const SHEET = (window.FORCE_SHEET === 'design' || location.pathname.replace(/\/$/, '') === '/design' || new URLSearchParams(location.search).get('sheet') === 'design') ? 'design' : 'kadai';
const STORE_KEY = SHEET === 'design' ? 'grarec_design_current' : 'grarec_kadai_current';
// デザイン思考ワークシートのエリア（位置はシートの自由スペースに対する%）。7/15の /quantum と同じ見出しと説明
const DT_TEMPLATES = {
  saguru: {label: '①さぐる', areas: [
    {id: 'persona', label: '🧑 ペルソナを決めよう', hint: '伝える相手を1人、妄想で決める（例：中学2年生、理科は好きだけど数式は苦手）', x: 0, y: 0, w: 32, h: 100, c: '#9c27b0', b: 'rgba(243,229,245,.75)'},
    {id: 'current-image', label: '💭 今のイメージ・知識', hint: 'その人が今どんなイメージ・知識を持っていそうか（例：「聞いたことはあるけど難しそう」）', x: 34, y: 0, w: 32, h: 100, c: '#4caf50', b: 'rgba(232,245,233,.75)'},
    {id: 'scene', label: '🎬 どんな場面で触れそうか', hint: 'その人がどんな場面で触れそうか（例：理科の授業、SF映画、ニュース）', x: 68, y: 0, w: 32, h: 100, c: '#3f51b5', b: 'rgba(232,234,246,.75)'}]},
  kizuku: {label: '②きづく', areas: [
    {id: 'confusion', label: '🤔 迷いそうなポイント', hint: 'その人が何につまずきそうか（具体的な用語・考え方）', x: 0, y: 0, w: 32, h: 100, c: '#ff9800', b: 'rgba(255,243,224,.75)'},
    {id: 'why-but', label: '🧩 なぜなら／でも・じつは', hint: '「〇〇を知りたい　なぜなら〇〇」「でも／じつは〇〇」の形で深掘りする', x: 34, y: 0, w: 32, h: 100, c: '#e91e63', b: 'rgba(252,228,236,.75)'},
    {id: 'insight', label: '💡 インサイト（一行）', hint: 'その人の本当の困りごと・知りたいことを1文にまとめる（テキストボックスで書く）', x: 68, y: 0, w: 32, h: 100, c: '#2196f3', b: 'rgba(227,242,253,.75)'}]},
  hirameku: {label: '③ひらめく', areas: [
    {id: 'core', label: '💡 ペルソナのインサイト', hint: 'きづくで決めた一行インサイトをここに書く', x: 0, y: 0, w: 100, h: 20, c: '#3f51b5', b: 'rgba(232,234,246,.75)'},
    {id: 'idea-1', label: '💡 アイデア', hint: '思いついた工夫', x: 0, y: 23, w: 49, h: 37, c: '#2196f3', b: 'rgba(227,242,253,.6)'},
    {id: 'idea-2', label: '💡 アイデア', hint: '別の視点のアイデアも歓迎', x: 51, y: 23, w: 49, h: 37, c: '#2196f3', b: 'rgba(227,242,253,.6)'},
    {id: 'idea-3', label: '💡 アイデア', hint: '小さな一歩のアイデアもOK', x: 0, y: 63, w: 49, h: 37, c: '#2196f3', b: 'rgba(227,242,253,.6)'},
    {id: 'idea-4', label: '💡 アイデア', hint: '相手の気持ちに寄りそった案', x: 51, y: 63, w: 49, h: 37, c: '#2196f3', b: 'rgba(227,242,253,.6)'}]}
};
const DT_PHASES = ['saguru', 'kizuku', 'hirameku'];
const tplOf = id => { const sl = (S.slides || []).find(x => x.id === id); return sl && sl.tpl && DT_TEMPLATES[sl.tpl] ? DT_TEMPLATES[sl.tpl] : null; };
function areaEls(tpl){
  return tpl.areas.map(a => {
    const e = document.createElement('div'); e.className = 'area'; e.dataset.area = a.id;
    e.style.cssText = `left:${a.x}%;top:${a.y}%;width:${a.w}%;height:${a.h}%;--ac:${a.c};--ab:${a.b}`;
    e.innerHTML = '<div class="al"></div><div class="ah"></div>';
    e.querySelector('.al').textContent = a.label; e.querySelector('.ah').textContent = a.hint;
    return e;
  });
}
function areaAt(slideId, x, y){                     // カードの中心が入っているエリア
  const tpl = tplOf(slideId); if (!tpl) return null;
  const cx = x + 9 * 100 / 94.5, cy = y + 4;       // カード幅はシートの18%（自由スペースの約19%）
  const a = tpl.areas.find(a => cx >= a.x && cx <= a.x + a.w && cy >= a.y && cy <= a.y + a.h);
  return a ? a.id : null;
}""")

# ---------- 描画：エリアの枠 ----------
rep('''  body.querySelectorAll('.tbox').forEach(n => { if (n.dataset.id !== editingText) n.remove(); });''',
    '''  body.querySelectorAll('.tbox').forEach(n => { if (n.dataset.id !== editingText) n.remove(); });
  body.querySelectorAll('.area').forEach(n => n.remove());
  const tplNow = tplOf(S.cur);
  body.classList.toggle('dt', !!tplNow);
  if (tplNow) areaEls(tplNow).forEach(e => body.insertBefore(e, body.firstChild));''')
# サムネイルにもエリアとフェーズ名
rep('''    const mb = th.querySelector('.s-body');''', '''    const mb = th.querySelector('.s-body');
    const tplT = tplOf(sl.id);
    if (tplT){
      areaEls(tplT).forEach(e => mb.appendChild(e));
      const ph = document.createElement('span'); ph.className = 'ph'; ph.textContent = tplT.label;
      th.appendChild(ph);
    }''')

# ---------- どのエリアに置いたかを記録 ----------
rep('''      log(fromTray ? 'place' : 'move', c, Object.assign({slide: slideNo(S.cur)}, fromHist ? {via: 'history'} : {},''',
    '''      c.area = areaAt(S.cur, c.x, c.y);
      log(fromTray ? 'place' : 'move', c, Object.assign({slide: slideNo(S.cur), area: c.area}, fromHist ? {via: 'history'} : {},''')
rep('''  c.x = spot.x; c.y = spot.y; c.place = 'sheet'; c.slide = S.cur;
  log('place', c, {slide: slideNo(S.cur), via: 'tap',''', '''  c.x = spot.x; c.y = spot.y; c.place = 'sheet'; c.slide = S.cur; c.area = areaAt(S.cur, c.x, c.y);
  log('place', c, {slide: slideNo(S.cur), area: c.area, via: 'tap',''')

# ---------- 保存の形 ----------
rep('''    template: 'kadai', mode: 'kadai', aiSupport: true,
    title: '課題' + S.kadai_no,''', '''    template: SHEET, mode: SHEET, aiSupport: true, sheet: SHEET,
    title: SHEET === 'design' ? (S.task_hint || 'デザイン思考ワークシート') : '課題' + S.kadai_no,''')
rep('''      addedAt: c.addedAt, slide: c.place === 'sheet' ? slideNo(c.slide) : null, mentionCount:''',
    '''      addedAt: c.addedAt, slide: c.place === 'sheet' ? slideNo(c.slide) : null, sheetArea: c.place === 'sheet' ? (c.area || null) : null, mentionCount:''')

# ---------- 開始：デザイン思考なら3枚（さぐる・きづく・ひらめく）で始める ----------
rep('''    session_id: `kadai_${g}班_${k}_${stamp()}`, cards: [], events: [], transcript: [], cycle: 0, lastExtracted: '', pendingText: '',''',
    '''    session_id: SHEET === 'design' ? `design_${g}班_${stamp()}` : `kadai_${g}班_${k}_${stamp()}`, cards: [], events: [], transcript: [], cycle: 0, lastExtracted: '', pendingText: '',''')
rep('''    slides: [{id: 's1'}], cur: 's1', texts: []});
  selText = null; editingText = null;''', '''    slides: SHEET === 'design' ? DT_PHASES.map((t, i) => ({id: 's' + (i + 1), tpl: t})) : [{id: 's1'}], cur: 's1', texts: []});
  selText = null; editingText = null;''')
rep('''  log('session_start', null, {config: {quick: QUICK,''', '''  log('session_start', null, {sheet: SHEET, config: {quick: QUICK,''')

# ---------- 見出し：テーマとチームメンバ ----------
rep('''function applyHeader(){
  $('kadaiNo').value = S.kadai_no; $('leaderId').value = S.leader_id || '';''', '''function applyHeader(){
  $('kadaiNo').value = S.kadai_no; $('leaderId').value = S.leader_id || '';
  if (SHEET === 'design') $('leaderId').value = S.task_hint || '';''')
rep('''$('leaderId').oninput = () => { S.leader_id = $('leaderId').value.trim(); save(); };''',
    '''$('leaderId').oninput = () => { if (SHEET === 'design') S.task_hint = $('leaderId').value.trim(); else S.leader_id = $('leaderId').value.trim(); save(); };
if (SHEET === 'design'){
  document.title = 'GraRec デザイン思考ワークシート';
  const tt = document.querySelector('.s-title'); if (tt) tt.style.display = 'none';
  const tb = document.querySelector('.s-table'); tb.style.gridTemplateColumns = '38% 1fr';
  const ths = tb.querySelectorAll('.s-th'); ths[0].textContent = 'テーマ'; ths[1].textContent = 'チームメンバ';
  const li = $('leaderId'); li.placeholder = '例：量子力学をわかりやすく伝えよう'; li.classList.add('theme'); li.setAttribute('aria-label', 'テーマ');
  const sf = $('startForm');
  sf.querySelector('h1').textContent = 'デザイン思考ワークシートをはじめる';
  $('startKadai').closest('.field').style.display = 'none'; $('startKadai').required = false;
  const hl = $('startHint').closest('.field');
  hl.firstChild.textContent = 'テーマ（任意）';
  $('startHint').placeholder = '例：量子力学をわかりやすく伝えよう';
  hl.querySelector('small').textContent = '①さぐる ②きづく ③ひらめく の3枚のシートで始まります。テーマを書いておくと、関係するカードを選びやすくなります';
}''')
# 再開ボタンの表示
rep('''  b.textContent = `続きから再開：${saved.group_number}班・課題${saved.kadai_no}（カード${(saved.cards||[]).filter(c => c.place === 'sheet').length}枚）`;''',
    '''  b.textContent = `続きから再開：${saved.group_number}班・${SHEET === 'design' ? (saved.task_hint || 'デザイン思考') : '課題' + saved.kadai_no}（カード${(saved.cards||[]).filter(c => c.place === 'sheet').length}枚）`;''')

# ---------- 書き出し：シートの種類・テーマ・各スライドのエリアも送る ----------
rep('''        slides: S.slides.map(sl => { const o = onSlide(sl.id); return {''', '''        sheet: SHEET, theme: S.task_hint || '',
        slides: S.slides.map(sl => { const o = onSlide(sl.id); const tp = tplOf(sl.id); return {
          tpl: sl.tpl || null, label: tp ? tp.label : '', areas: tp ? tp.areas : [],''')
rep('''    a.download = `kadai${S.kadai_no}${S.group_number ? '_group' + S.group_number : ''}.pptx`.replace(/[^\\w.\\-]/g, '_');''',
    '''    a.download = (SHEET === 'design' ? `design${S.group_number ? '_group' + S.group_number : ''}.pptx`
      : `kadai${S.kadai_no}${S.group_number ? '_group' + S.group_number : ''}.pptx`).replace(/[^\\w.\\-]/g, '_');''')

open(p, "w", encoding="utf-8").write(s)
print("patched")
