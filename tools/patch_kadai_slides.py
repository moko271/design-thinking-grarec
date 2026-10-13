# -*- coding: utf-8 -*-
"""
kadai.html：PowerPointのようにスライドを増やせるようにし、テキストボックスを置けるようにする（2026-10-13）
  ・左にスライドの一覧（サムネイル）。「＋ スライドを追加」で今のスライドのすぐ後ろに増える。×で削除（中身があるときは2回押す）
  ・カードはトレイから今のスライドへドラッグ。左の一覧のスライドに落とすと、そのスライドへ移る
  ・「＋ テキストボックス」を押してシートをクリックすると、そこに文字を打てる（PowerPointと同じ）
      クリックで文字を打つ／ドラッグで動かす／右の□で幅を変える／×かDeleteで消す／文字の大きさ（pt）を選べる
  ・PowerPoint書き出しは全スライドを出す（app.py 側は tools/patch_app_slides.py）
  ・「新しいシート」ボタンは中身を全部消して最初からだったので、「別の班ではじめる」に名前を変えた
記録：slide_add / slide_delete / slide_switch / text_add / text_edit / text_move / text_resize / text_size / text_delete、
      place・move に slide（何枚目か）を付ける
何度実行しても同じ結果になる。
"""
import sys

p = sys.argv[1] if len(sys.argv) > 1 else "static/kadai.html"
s = open(p, encoding="utf-8").read()
if "renderThumbs" in s:
    print("already patched"); sys.exit(0)


def rep(old, new, n=1):
    global s
    c = s.count(old)
    assert c == n, (c, old[:90])
    s = s.replace(old, new)


# ---------- CSS ----------
rep("/* usage log */", r"""/* スライドの一覧（PowerPointの左側） */
.main{grid-template-columns:150px minmax(0,1fr) 300px}
@media (max-width:1100px){.main{grid-template-columns:120px minmax(0,1fr) 260px}}
.thumbs{display:flex;flex-direction:column;gap:10px;position:sticky;top:16px;max-height:calc(100vh - 110px);overflow:auto;padding:4px 6px 4px 0}
.thumb{position:relative;display:grid;grid-template-columns:16px 1fr;gap:4px;align-items:start;cursor:pointer;outline:none}
.thumb .no{font-size:12px;color:var(--ink-2);font-variant-numeric:tabular-nums;text-align:right;padding-top:2px}
.thumb .mini{pointer-events:none;border:2px solid transparent;border-radius:3px;box-shadow:0 1px 2px rgba(0,0,0,.22)}
.thumb:hover .mini{border-color:#B8C2CF}
.thumb.on .mini{border-color:var(--accent)}
.thumb.on .no{color:var(--accent);font-weight:700}
.thumb:focus-visible .mini{outline:2px solid var(--accent);outline-offset:2px}
.thumb.drop .mini{border-color:var(--accent);background:var(--accent-soft)}
.thumb .del{position:absolute;right:-4px;top:-6px;border:0;border-radius:999px;background:#fff;color:var(--ink-2);font-size:12px;line-height:1;padding:3px 6px;box-shadow:0 1px 3px rgba(0,0,0,.3);opacity:0}
.thumb:hover .del,.thumb:focus-within .del,.thumb .del.confirm{opacity:1}
.thumb .del.confirm{background:var(--rec);color:#fff;font-weight:700}
.add-slide{margin-left:20px;border:1px dashed #9AA6B4;background:var(--panel);border-radius:6px;padding:8px 4px;font-size:12px;color:var(--ink-2)}
.add-slide:hover{border-color:var(--accent);color:var(--accent)}
.mini .m-head{position:absolute;left:2.2%;right:3%;top:3.5%;height:11%;background:var(--paper-cell)}
.mini .m-head::before{content:"";position:absolute;left:0;right:0;top:0;height:38%;background:#000}
.mini .m-head::after{content:"";position:absolute;left:-1%;top:15%;width:13%;height:60%;background:#fff}
.mini .card{pointer-events:none}
.mini .tbox{border-color:transparent}
@media (max-width:900px){
  .thumbs{position:static;flex-direction:row;overflow-x:auto;max-height:none;padding-bottom:6px}
  .thumb{width:120px;flex:none}
  .add-slide{margin-left:0;flex:none}
}
/* 道具（テキストボックス） */
.tools{display:flex;flex-wrap:wrap;align-items:center;gap:8px 12px;margin-bottom:10px;font-size:12px;color:var(--ink-2)}
.tool{border:1px solid var(--line);background:var(--panel-2);color:var(--ink);border-radius:7px;padding:5px 11px;font-size:13px}
.tool:hover{border-color:var(--accent)}
.tool.on{background:var(--accent-soft);border-color:var(--accent);color:var(--accent);font-weight:700}
.tools label{display:flex;align-items:center;gap:6px}
.tools select{border:1px solid var(--line);background:var(--panel-2);border-radius:7px;padding:4px 6px;font-size:13px}
.tools .hint{flex:1;min-width:200px;text-align:right}
.s-body.texting{cursor:crosshair}
/* テキストボックス（PowerPointと同じく枠なし。選ぶと枠が出る） */
.tbox{position:absolute;padding:.25cqw .45cqw;line-height:1.3;color:#111;font-family:"BIZ UDPGothic","Hiragino Sans",sans-serif;border:1px solid transparent;cursor:move;touch-action:none;user-select:none;z-index:2}
.tbox .tx{white-space:pre-wrap;overflow-wrap:anywhere;min-height:1.3em;outline:none}
.tbox:hover{border-color:#B8C2CF}
.tbox.sel{border-color:var(--accent)}
.tbox.editing{cursor:text;user-select:text;border-color:var(--accent);background:rgba(255,255,255,.6)}
.tbox .rh{position:absolute;right:-6px;top:50%;width:10px;height:10px;margin-top:-5px;background:#fff;border:1px solid var(--accent);border-radius:2px;cursor:ew-resize;display:none}
.tbox.sel .rh,.tbox.editing .rh{display:block}
.tbox .x{position:absolute;right:-10px;top:-12px;width:20px;height:20px;border:0;border-radius:50%;background:#fff;color:#555;font-size:13px;line-height:20px;padding:0;box-shadow:0 1px 3px rgba(0,0,0,.3);display:none}
.tbox.sel .x,.tbox.editing .x{display:block}

/* usage log */""")

# ---------- HTML ----------
rep('''<main class="main">
  <section class="sheet-wrap" aria-label="課題シート">
    <div class="sheet-meta">
      <span>カードは右のトレイからシートにドラッグして置く。ダブルクリックで書き直せる</span>
      <span>メンバー名をクリック → リーダ（赤）／オブザーバ（青）／メンバ</span>
    </div>''', '''<main class="main">
  <nav class="thumbs" id="thumbs" aria-label="スライドの一覧"></nav>
  <section class="sheet-wrap" aria-label="課題シート">
    <div class="tools">
      <button class="tool" id="textBtn" type="button" title="押したあと、シートの文字を置きたい場所をクリック">＋ テキストボックス</button>
      <label for="textSize">文字の大きさ
        <select id="textSize">
          <option value="12">12</option><option value="14">14</option><option value="18" selected>18</option>
          <option value="24">24</option><option value="32">32</option><option value="44">44</option>
        </select>
      </label>
      <span class="hint" id="toolHint"></span>
    </div>''')
rep('''<div class="s-hint" id="bodyHint">ここはみんなで自由に使うスペース<br>話し合いから出たカードを置いて、書き出したあとPowerPointで線や囲みを足していける</div>''',
    '''<div class="s-hint" id="bodyHint">ここはみんなで自由に使うスペース<br>右のトレイのカードを置いたり、「＋ テキストボックス」で文字を書いたりできる</div>''')
rep('''<div class="s-page">1</div>''', '''<div class="s-page" id="pageNo">1</div>''')
rep('''<button class="bar-btn" id="newBtn" type="button">新しいシート</button>''',
    '''<button class="bar-btn" id="newBtn" type="button" title="今のシートを閉じて、別の班として最初からはじめる">別の班ではじめる</button>''')
rep('''声のサンプルは「新しいシート」を押したとき''', '''声のサンプルは「別の班ではじめる」を押したとき''')

# ---------- 状態 ----------
rep('''  cards: [],            // {id, keyword,''', '''  slides: [{id: 's1'}], cur: 's1',                 // スライド（PowerPointのページ）と、いま開いているスライド
  texts: [],            // テキストボックス {id, slide, x, y, w(%), size(pt), text}
  cards: [],            // {id, keyword,''')

# ---------- render ----------
rep('''function render(){
  const list = $('list'), body = $('body');
  list.querySelectorAll('.card').forEach(n => n.remove());
  body.querySelectorAll('.card').forEach(n => n.remove());''', '''function render(){
  ensureSlides();
  const list = $('list'), body = $('body');
  list.querySelectorAll('.card').forEach(n => n.remove());
  body.querySelectorAll('.card').forEach(n => n.remove());
  body.querySelectorAll('.tbox').forEach(n => { if (n.dataset.id !== editingText) n.remove(); });''')
rep('''  S.cards.filter(c => c.place === 'sheet').forEach(c => body.appendChild(cardEl(c)));''',
    '''  S.cards.filter(c => c.place === 'sheet' && c.slide === S.cur).forEach(c => body.appendChild(cardEl(c)));
  S.texts.filter(t => t.slide === S.cur && t.id !== editingText).forEach(t => body.appendChild(textEl(t)));''')
rep('''  $('bodyHint').hidden = !!body.querySelector('.card');
  paintStats();
}''', '''  $('pageNo').textContent = slideNo(S.cur);
  renderThumbs(); paintBodyHint();
  paintStats();
}
function paintBodyHint(){ $('bodyHint').hidden = !!$('body').querySelector('.card,.tbox'); }

// ================= スライド（PowerPointのように増やせる） =================
const PT2CQW = 12700 / 9906000 * 100;   // 書き出すPowerPointで 1pt がシートの幅の何%か
function ensureSlides(){
  if (!Array.isArray(S.slides) || !S.slides.length) S.slides = [{id: 's1'}];
  if (!S.slides.some(x => x.id === S.cur)) S.cur = S.slides[0].id;
  if (!Array.isArray(S.texts)) S.texts = [];
  const ok = id => S.slides.some(x => x.id === id);
  S.cards.forEach(c => { if (c.place === 'sheet' && !ok(c.slide)) c.slide = S.slides[0].id; });
  S.texts.forEach(t => { if (!ok(t.slide)) t.slide = S.slides[0].id; });
}
const slideNo = id => S.slides.findIndex(x => x.id === id) + 1;
const onSlide = id => ({cards: S.cards.filter(c => c.place === 'sheet' && c.slide === id), texts: S.texts.filter(t => t.slide === id)});
function renderThumbs(){
  const box = $('thumbs'); box.innerHTML = '';
  S.slides.forEach((sl, i) => {
    const th = document.createElement('div');
    th.className = 'thumb' + (sl.id === S.cur ? ' on' : ''); th.dataset.slide = sl.id;
    th.tabIndex = 0; th.setAttribute('role', 'button'); th.setAttribute('aria-label', `スライド ${i + 1}`);
    th.innerHTML = `<span class="no">${i + 1}</span><div class="sheet mini"><div class="m-head"></div><div class="s-rule"></div><div class="s-body"></div></div>`;
    const mb = th.querySelector('.s-body');
    const o = onSlide(sl.id);
    o.cards.forEach(c => {
      const e = document.createElement('div'); e.className = 'card'; e.dataset.kind = c.kind;
      e.style.left = c.x + '%'; e.style.top = c.y + '%';
      e.innerHTML = '<span class="kind"></span><div class="kw"></div>';
      e.querySelector('.kind').textContent = KIND_LABEL[c.kind] || ''; e.querySelector('.kw').textContent = c.keyword;
      mb.appendChild(e);
    });
    o.texts.forEach(t => {
      const e = document.createElement('div'); e.className = 'tbox'; placeText(e, t);
      const tx = document.createElement('div'); tx.className = 'tx'; tx.textContent = t.text; e.appendChild(tx);
      mb.appendChild(e);
    });
    if (S.slides.length > 1){
      const d = document.createElement('button'); d.type = 'button'; d.className = 'del'; d.textContent = '×';
      d.setAttribute('aria-label', `スライド ${i + 1} を削除`);
      // pointerdown で動かす（クリックの途中で一覧が作り直されても取りこぼさないように）
      d.onpointerdown = e => { if (e.button !== 0) return; e.preventDefault(); e.stopPropagation(); deleteSlide(sl.id, d); };
      d.onclick = e => { e.stopPropagation(); if (e.detail === 0) deleteSlide(sl.id, d); };   // キーボード
      th.appendChild(d);
    }
    th.onpointerdown = e => { if (e.button !== 0 || e.target.closest('.del')) return; e.preventDefault(); goSlide(sl.id); };
    th.onkeydown = e => { if (e.key === 'Enter' || e.key === ' '){ e.preventDefault(); goSlide(sl.id); } };
    box.appendChild(th);
  });
  const add = document.createElement('button'); add.type = 'button'; add.className = 'add-slide'; add.textContent = '＋ スライドを追加';
  add.onpointerdown = e => { if (e.button !== 0) return; e.preventDefault(); addSlide(); };
  add.onclick = e => { if (e.detail === 0) addSlide(); };   // キーボード
  box.appendChild(add);
}
function goSlide(id){
  if (id === S.cur) return;
  finishTextEdit(); S.cur = id; selText = null;
  log('slide_switch', null, {slide: slideNo(id)}); render(); save();
}
function addSlide(){
  finishTextEdit();
  const sl = {id: 's' + Date.now().toString(36)};
  S.slides.splice(slideNo(S.cur), 0, sl);          // 今のスライドのすぐ後ろに入れる（PowerPointと同じ）
  S.cur = sl.id; selText = null;
  log('slide_add', null, {slide: slideNo(sl.id), total: S.slides.length}); render(); save();
  toast(`スライド ${slideNo(sl.id)} を追加しました`);
}
function deleteSlide(id, btn){
  const o = onSlide(id);
  if ((o.cards.length || o.texts.length) && !btn.classList.contains('confirm')){
    btn.classList.add('confirm'); btn.textContent = 'もう一度押すと削除';
    setTimeout(() => { btn.classList.remove('confirm'); btn.textContent = '×'; }, 3000);
    return;
  }
  finishTextEdit();
  const no = slideNo(id);
  o.cards.forEach(c => { c.place = 'deleted'; log('delete', c, {via: 'slide_delete'}); });
  S.texts = S.texts.filter(t => t.slide !== id);
  S.slides = S.slides.filter(x => x.id !== id);
  if (S.cur === id) S.cur = S.slides[Math.min(no - 1, S.slides.length - 1)].id;
  log('slide_delete', null, {slide: no, cards: o.cards.length, texts: o.texts.length, total: S.slides.length});
  render(); save();
}

// ================= テキストボックス（PowerPointと同じ：置きたい所をクリックして文字を打つ） =================
let textMode = false, selText = null, editingText = null;
const DEFAULT_HINT = 'カードは右のトレイからドラッグ（左のスライドに落とすとそのスライドへ）。ダブルクリックで書き直せる';
function placeText(el, t){
  el.style.left = t.x + '%'; el.style.top = t.y + '%'; el.style.width = t.w + '%';
  el.style.fontSize = (t.size * PT2CQW).toFixed(3) + 'cqw';
}
function setTextMode(on){
  textMode = on; $('textBtn').classList.toggle('on', on); $('body').classList.toggle('texting', on);
  $('toolHint').textContent = on ? 'シートの文字を置きたい場所をクリック（Escでやめる）' : DEFAULT_HINT;
}
function finishTextEdit(){ if (editingText && document.activeElement && document.activeElement.blur) document.activeElement.blur(); }
function removeText(t, silent){
  S.texts = S.texts.filter(x => x.id !== t.id);
  if (!silent) log('text_delete', null, {tid: t.id, slide: slideNo(t.slide)});
  if (selText === t.id) selText = null;
  if (editingText === t.id) editingText = null;
  render(); save();
}
function selectText(t, el){
  selText = t.id; $('textSize').value = String(t.size);
  document.querySelectorAll('#body .tbox.sel').forEach(n => { if (n !== el) n.classList.remove('sel'); });
  if (el) el.classList.add('sel');
}
function textEl(t){
  const el = document.createElement('div');
  el.className = 'tbox' + (t.id === selText ? ' sel' : ''); el.dataset.id = t.id;
  placeText(el, t);
  el.innerHTML = '<div class="tx"></div><span class="rh" title="幅を変える"></span><button class="x" type="button" aria-label="テキストボックスを消す">×</button>';
  const tx = el.querySelector('.tx'); tx.textContent = t.text;
  const xb = el.querySelector('.x');
  xb.addEventListener('pointerdown', e => e.stopPropagation());
  xb.onclick = e => { e.stopPropagation(); removeText(t); };
  tx.addEventListener('input', () => { t.text = tx.innerText.replace(/\\u00a0/g, ' '); });
  tx.addEventListener('paste', e => {      // 書式を持ち込まない（文字だけ貼る）
    e.preventDefault();
    document.execCommand('insertText', false, (e.clipboardData || window.clipboardData).getData('text/plain'));
  });
  el.addEventListener('pointerdown', ev => {
    if (ev.button !== 0 || editingText === t.id || ev.target.closest('.x')) return;
    const resizing = !!ev.target.closest('.rh');
    ev.preventDefault(); ev.stopPropagation();
    selectText(t, el);
    const b = $('body').getBoundingClientRect(), sx = ev.clientX, sy = ev.clientY, x0 = t.x, y0 = t.y, w0 = t.w;
    let moved = false;
    const move = e => {
      if (!moved && Math.hypot(e.clientX - sx, e.clientY - sy) < 4) return;
      moved = true;
      const dx = (e.clientX - sx) / b.width * 100, dy = (e.clientY - sy) / b.height * 100;
      if (resizing) t.w = +Math.max(5, Math.min(w0 + dx, 100 - t.x)).toFixed(2);
      else { t.x = +Math.max(0, Math.min(x0 + dx, 100 - t.w)).toFixed(2); t.y = +Math.max(0, Math.min(y0 + dy, 95)).toFixed(2); }
      placeText(el, t);
    };
    const up = () => {
      document.removeEventListener('pointermove', move); document.removeEventListener('pointerup', up);
      if (moved){ log(resizing ? 'text_resize' : 'text_move', null, {tid: t.id, slide: slideNo(t.slide)}); renderThumbs(); save(); }
      else if (!resizing) startTextEdit(t, el);
    };
    document.addEventListener('pointermove', move); document.addEventListener('pointerup', up);
  });
  return el;
}
function startTextEdit(t, el){
  el = el || document.querySelector(`#body .tbox[data-id="${t.id}"]`); if (!el) return;
  const tx = el.querySelector('.tx');
  selectText(t, el);
  editingText = t.id; el.classList.add('editing');
  tx.contentEditable = 'plaintext-only';
  if (tx.contentEditable !== 'plaintext-only') tx.contentEditable = 'true';
  tx.focus();
  const range = document.createRange(); range.selectNodeContents(tx); range.collapse(false);
  const sel = getSelection(); sel.removeAllRanges(); sel.addRange(range);
  const before = t.created ? '' : t.text;
  delete t.created;
  const key = e => { if (e.key === 'Escape'){ e.preventDefault(); tx.blur(); } };
  tx.addEventListener('keydown', key);
  tx.addEventListener('blur', () => {
    tx.removeEventListener('keydown', key);
    tx.contentEditable = 'false'; el.classList.remove('editing');
    if (editingText === t.id) editingText = null;
    const v = tx.innerText.replace(/\\u00a0/g, ' ').replace(/\\n$/, '');
    if (!v.trim()){ removeText(t, !before); return; }
    t.text = v;
    if (v !== before) log(before ? 'text_edit' : 'text_add', null, {tid: t.id, slide: slideNo(t.slide), chars: v.length});
    renderThumbs(); paintBodyHint(); save();
  }, {once: true});
}
$('textBtn').onclick = () => { finishTextEdit(); setTextMode(!textMode); };
$('textSize').onchange = () => {
  const t = S.texts.find(x => x.id === selText); if (!t) return;
  t.size = +$('textSize').value;
  const el = document.querySelector(`#body .tbox[data-id="${t.id}"]`); if (el) placeText(el, t);
  log('text_size', null, {tid: t.id, size: t.size}); renderThumbs(); save();
};
$('body').addEventListener('pointerdown', e => {
  if (e.target.closest('.card,.tbox')) return;
  if (!textMode){
    if (selText){ selText = null; document.querySelectorAll('#body .tbox.sel').forEach(n => n.classList.remove('sel')); }
    return;
  }
  e.preventDefault();
  const b = $('body').getBoundingClientRect();
  const x = Math.max(0, Math.min((e.clientX - b.left) / b.width * 100, 92));
  const y = Math.max(0, Math.min((e.clientY - b.top) / b.height * 100, 92));
  const t = {id: 't' + Date.now().toString(36), slide: S.cur, x: +x.toFixed(2), y: +y.toFixed(2),
             w: +Math.min(35, 100 - x).toFixed(2), size: +$('textSize').value, text: '', created: true, createdAt: nowISO()};
  S.texts.push(t); setTextMode(false);
  const el = textEl(t); $('body').appendChild(el); paintBodyHint();
  startTextEdit(t, el);
});
document.addEventListener('keydown', e => {
  if (e.key === 'Escape' && textMode) setTextMode(false);
  if ((e.key === 'Delete' || e.key === 'Backspace') && selText && !editingText &&
      !(e.target.closest && e.target.closest('input,textarea,select,[contenteditable="true"],[contenteditable="plaintext-only"]'))){
    const t = S.texts.find(x => x.id === selText);
    if (t){ e.preventDefault(); removeText(t); }
  }
});
setTextMode(false);''')

# ---------- カードのドラッグ：今のスライドへ／左の一覧のスライドへ ----------
rep('''      ghost.style.left = (e.clientX - offX) + 'px'; ghost.style.top = (e.clientY - offY) + 'px';
      body.classList.toggle('drop', inBody(e.clientX, e.clientY));''', '''      ghost.style.left = (e.clientX - offX) + 'px'; ghost.style.top = (e.clientY - offY) + 'px';
      body.classList.toggle('drop', inBody(e.clientX, e.clientY));
      const over = document.elementFromPoint(e.clientX, e.clientY);
      const th = over && over.closest ? over.closest('.thumb') : null;
      document.querySelectorAll('.thumb.drop').forEach(n => { if (n !== th) n.classList.remove('drop'); });
      if (th) th.classList.add('drop');''')
rep('''      ghost.remove(); el.style.opacity = ''; body.classList.remove('drop');
      if (!inBody(e.clientX, e.clientY)) return;''', '''      ghost.remove(); el.style.opacity = ''; body.classList.remove('drop');
      document.querySelectorAll('.thumb.drop').forEach(n => n.classList.remove('drop'));
      const over = document.elementFromPoint(e.clientX, e.clientY);
      const th = over && over.closest ? over.closest('.thumb') : null;
      if (th && th.dataset.slide){                       // 左の一覧のスライドに落とした
        const fromTray0 = c.place === 'tray';
        if (fromTray0 || th.dataset.slide !== c.slide){
          if (fromTray0){ c.x = 4; c.y = 4; }
          c.place = 'sheet'; c.slide = th.dataset.slide;
          log(fromTray0 ? 'place' : 'move', c, Object.assign({slide: slideNo(c.slide), via: 'thumb'},
            fromTray0 ? {secToPlace: Math.round((Date.now() - Date.parse(c.addedAt)) / 1000)} : {}));
          render(); save(); toast(`スライド ${slideNo(c.slide)} に移しました`);
        }
        return;
      }
      if (!inBody(e.clientX, e.clientY)) return;''')
rep('''      c.x = +x.toFixed(2); c.y = +y.toFixed(2); c.place = 'sheet';
      log(fromTray ? 'place' : 'move', c, fromTray ? {secToPlace: Math.round((Date.now() - Date.parse(c.addedAt)) / 1000)} : undefined);''',
    '''      c.x = +x.toFixed(2); c.y = +y.toFixed(2); c.place = 'sheet'; c.slide = S.cur;
      log(fromTray ? 'place' : 'move', c, Object.assign({slide: slideNo(S.cur)}, fromTray ? {secToPlace: Math.round((Date.now() - Date.parse(c.addedAt)) / 1000)} : {}));''')

# ---------- 保存・復元・新しい班 ----------
rep('''      addedAt: c.addedAt, mentionCount:''', '''      addedAt: c.addedAt, slide: c.place === 'sheet' ? slideNo(c.slide) : null, mentionCount:''')
rep("""'voice','speech','balTrace','recStartedAt'];
  keys.forEach(k => { if (d[k] !== undefined) S[k] = d[k]; });""", """'voice','speech','balTrace','recStartedAt','slides','cur','texts'];
  S.slides = [{id: 's1'}]; S.cur = 's1'; S.texts = [];
  keys.forEach(k => { if (d[k] !== undefined) S[k] = d[k]; });""")
rep('''    voice: {enabled: false, slots: {}}, speech: [], balTrace: [], recStartedAt: 0});''',
    '''    voice: {enabled: false, slots: {}}, speech: [], balTrace: [], recStartedAt: 0,
    slides: [{id: 's1'}], cur: 's1', texts: []});
  selText = null; editingText = null;''')

# ---------- 書き出し：全スライド ----------
rep('''$('exportBtn').onclick = async () => {
  const btn = $('exportBtn'); btn.disabled = true; btn.textContent = '書き出し中…';''', '''$('exportBtn').onclick = async () => {
  finishTextEdit(); ensureSlides();
  const btn = $('exportBtn'); btn.disabled = true; btn.textContent = '書き出し中…';''')
rep('''members: S.members.filter(m => m.name), cards: S.cards.filter(c => c.place === 'sheet').map(c => ({keyword: c.keyword, x: c.x, y: c.y}))})});''',
    '''members: S.members.filter(m => m.name),
        slides: S.slides.map(sl => { const o = onSlide(sl.id); return {
          cards: o.cards.map(c => ({keyword: c.keyword, x: c.x, y: c.y})),
          texts: o.texts.map(t => ({text: t.text, x: t.x, y: t.y, w: t.w, size: t.size}))}; })})});''')
rep('''log('export', null, {cards: S.cards.filter(c => c.place === 'sheet').length}); save();''',
    '''log('export', null, {cards: S.cards.filter(c => c.place === 'sheet').length, slides: S.slides.length, texts: S.texts.length}); save();''')
rep('''window.GraRecKadai = {S, deliver, makeCards, rotate, render, paintBalance, balanceNow, openVoice};''',
    '''window.GraRecKadai = {S, deliver, makeCards, rotate, render, paintBalance, balanceNow, openVoice, addSlide, goSlide};''')

open(p, "w", encoding="utf-8").write(s)
print("patched")
