# -*- coding: utf-8 -*-
"""
kadai.html：スマホでも使えるようにする（2026-10-13）
  ・幅900px以下：1列（スライド一覧 → シート → トレイ）。前回足した「1100px以下は3列」の指定が1列の指定を上書きしていたのを直す
  ・幅700px以下：シートは最小640pxで横にスクロール（小さくしすぎると文字が読めないため）。上のバーと道具を詰める
  ・指で操作する端末（pointer: coarse）：トレイのカードに「置く」ボタン。押すと今のスライドの空いている所に置く（ドラッグしなくてよい）
  ・iPhone（Safari）は webm で録音できず mp4 になるので、送るファイル名の拡張子を実際の形式に合わせる
  ・録音中は画面が消えないようにする（Wake Lock。使えない端末では何もしない）。画面が消える・別アプリに切り替えると録音が止まる端末があるので、録音開始時に一度だけ知らせる
何度実行しても同じ結果になる。
"""
import sys

p = sys.argv[1] if len(sys.argv) > 1 else "static/kadai.html"
s = open(p, encoding="utf-8").read()
if "placeByTap" in s:
    print("already patched"); sys.exit(0)


def rep(old, new, n=1):
    global s
    c = s.count(old)
    assert c == n, (c, old[:90])
    s = s.replace(old, new)


# ---------- CSS ----------
rep("/* usage log */", r"""/* スマホ・タブレット */
@media (max-width:900px){
  .main{grid-template-columns:minmax(0,1fr);padding:10px;gap:12px}
  .thumbs{order:0}
  .sheet-wrap{order:1;padding:10px}
  .tray{order:2}
}
@media (max-width:700px){
  .bar{padding:8px 10px;gap:8px}
  .brand{font-size:16px}
  .rec-state{font-size:12px;white-space:normal}
  .interval{font-size:12px}
  .spacer{display:none}
  .bar-btn,.export{padding:6px 10px;font-size:12px}
  .tools .hint{display:none}
  .sheet-scroll{overflow-x:auto;-webkit-overflow-scrolling:touch;margin:0 -4px;padding:0 4px 6px}
  .sheet-scroll .sheet{min-width:640px}
  .banner{margin:8px 10px 0}
}
.card .place{display:none;margin-top:6px;border:1px solid var(--accent);background:var(--accent-soft);color:var(--accent);font-weight:700;border-radius:6px;padding:5px 12px;font-size:13px}
@media (pointer:coarse){
  .list .card .place{display:inline-block}
  .card .x{opacity:1}
  .tbox .rh{width:16px;height:16px;margin-top:-8px;right:-9px}
}

/* usage log */""")

# ---------- HTML：シートを横スクロールできる箱に入れる ----------
rep('''    <div class="sheet" id="sheet">''', '''    <div class="sheet-scroll">
    <div class="sheet" id="sheet">''')
rep('''      <div class="s-page" id="pageNo">1</div>
    </div>''', '''      <div class="s-page" id="pageNo">1</div>
    </div>
    </div>''')

# ---------- トレイのカードに「置く」ボタン（指で操作する端末だけ表示） ----------
rep('''  if (c.place === 'sheet'){ el.style.left = c.x + '%'; el.style.top = c.y + '%'; }''',
    '''  if (c.place === 'sheet'){ el.style.left = c.x + '%'; el.style.top = c.y + '%'; }
  if (c.place === 'tray'){
    const pb = document.createElement('button'); pb.type = 'button'; pb.className = 'place'; pb.textContent = 'シートに置く';
    pb.addEventListener('pointerdown', e => e.stopPropagation());
    pb.onclick = e => { e.stopPropagation(); placeByTap(c); };
    el.appendChild(pb);
  }''')
rep('''// ドラッグ（マウス・タッチ・ペン共通）''', '''// 指で操作する端末用：今のスライドの空いている所に置く
function placeByTap(c){
  const taken = S.cards.filter(x => x.place === 'sheet' && x.slide === S.cur);
  const xs = [2, 22, 42, 62, 81], ys = [3, 19, 35, 51, 67, 83];
  let spot = null;
  for (const y of ys){ for (const x of xs){
    if (!taken.some(t => Math.abs(t.x - x) < 17 && Math.abs(t.y - y) < 12)){ spot = {x, y}; break; }
  } if (spot) break; }
  if (!spot) spot = {x: 2 + (taken.length % 5) * 2, y: 3 + (taken.length % 5) * 2};
  c.x = spot.x; c.y = spot.y; c.place = 'sheet'; c.slide = S.cur;
  log('place', c, {slide: slideNo(S.cur), via: 'tap', secToPlace: Math.round((Date.now() - Date.parse(c.addedAt)) / 1000)});
  render(); save();
  toast(`スライド ${slideNo(S.cur)} に置きました（ドラッグで動かせます）`);
}

// ドラッグ（マウス・タッチ・ペン共通）''')

# ---------- iPhone の録音形式に合わせた拡張子 ----------
rep('''function newRecorder(){''', '''const extOf = type => /mp4|m4a|aac/.test(type || '') ? 'mp4' : /ogg/.test(type || '') ? 'ogg' : /wav/.test(type || '') ? 'wav' : 'webm';
function newRecorder(){''')
rep('''  fd.append('audio', blob, 'segment.webm');''', '''  fd.append('audio', blob, 'segment.' + extOf(blob.type));''')
rep('''    fd.append('audio', new Blob(chunks, {type: r.mimeType || 'audio/webm'}), 'voice.webm');''',
    '''    fd.append('audio', new Blob(chunks, {type: r.mimeType || 'audio/webm'}), 'voice.' + extOf(r.mimeType));''')

# ---------- 録音中は画面を消さない ----------
rep('''  log('rec_start'); meterLoop(); paintRec(); save();''', '''  log('rec_start', null, {ua_mobile: /iPhone|iPad|Android/.test(navigator.userAgent)}); meterLoop(); paintRec(); save();
  keepAwake(true);
  if (/iPhone|iPad|Android/.test(navigator.userAgent) && !warnedMobile){
    warnedMobile = true;
    banner('スマホ・タブレットで録音するときは、画面を消したり別のアプリに切り替えたりしないでください（録音が止まることがあります）');
  }''')
rep('''  log('rec_stop'); paintRec(); save();''', '''  log('rec_stop'); paintRec(); save();
  keepAwake(false);''')
rep('''$('recBtn').onclick = () => recording ? stopRecording() : startRecording();''', '''let wakeLock = null, warnedMobile = false;
async function keepAwake(on){
  try {
    if (on && 'wakeLock' in navigator){ wakeLock = await navigator.wakeLock.request('screen'); }
    else if (!on && wakeLock){ await wakeLock.release(); wakeLock = null; }
  } catch (_) {}
}
document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'visible' && recording) keepAwake(true);    // 画面に戻ったら取り直す
  if (recording) log(document.visibilityState === 'visible' ? 'page_visible' : 'page_hidden');
});
$('recBtn').onclick = () => recording ? stopRecording() : startRecording();''')

open(p, "w", encoding="utf-8").write(s)
print("patched")
