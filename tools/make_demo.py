# -*- coding: utf-8 -*-
"""
録音データ（7/15の文字起こし）を使って、/kadai の画面で「どんなカードが・いつ・何枚届くか」を再生するデモを作る。
grarec_light フォルダで:
    python tools/make_demo.py            # 班31（既定）
    python tools/make_demo.py 6          # 班6
→ sim/out/demo_kadai_班<g>.html （ダブルクリックでブラウザで開く。サーバーもAPIも使わない）

カードは sim/sim_cards.py で作ったもの（今の設定：最初3分→以降10分・最大6枚、1回目の実行）。
画面は static/kadai.html そのものなので、届いたカードをシートにドラッグしたり、スライドやテキストボックスも試せる。
注意：文字起こしは学生の発話そのもの。sim/out は GitHub に上がらない場所なので、ファイルを配らないこと。
注意：班1・5・32 は正解（gold）を付ける前にカードを見ないことにしているので、既定は班31。
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "sim"))
from sim_common import load_transcript  # noqa: E402

g = int(sys.argv[1]) if len(sys.argv) > 1 else 31
# 本番と同じタイミングで作り直したカード（sim/sim_cards_live.py）があれば、そちらを使う
live = os.path.join(ROOT, "sim", "out", "cards_live", f"班{g}_place1.json")
if os.path.exists(live):
    src, variant = live, "live"
    d = json.load(open(src, encoding="utf-8"))
    batches = [{"t": b["deliver_sec"], "cards": [{k: c.get(k) for k in ("keyword", "quote", "kind", "lone", "reason")} for c in b["cards"]]}
               for b in d["batches"] if b.get("cards")]
    note = "本番と同じタイミング（90秒ごとの区間・届くまでの遅れ・出ているカード全部をAIに渡す）で作ったカード"
else:
    variant, run = "plan3-10_max6", 1
    src = os.path.join(ROOT, "sim", "out", "cards", variant, f"班{g}_run{run}.json")
    d = json.load(open(src, encoding="utf-8"))
    batches = [{"t": b["t_sec"], "cards": [{k: c.get(k) for k in ("keyword", "quote", "kind", "lone", "reason")} for c in b["cards"]]}
               for b in d["batches"] if b.get("cards")]
    note = "シミュレーション（10/7）で作ったカード。タイミングは本番と少し違う"
lines = [[t, x] for t, x in load_transcript(g)]
end = max(lines[-1][0] if lines else 0, batches[-1]["t"] if batches else 0)
data = {"group": g, "variant": variant, "note": note, "batches": batches, "lines": lines, "end": end}

DEMO_CSS = """
body{padding-bottom:150px}
.demo{position:fixed;left:0;right:0;bottom:0;z-index:90;background:#13213A;color:#fff;box-shadow:0 -4px 16px rgba(0,0,0,.25);padding:10px 16px 12px;font-size:13px}
.demo-row{display:flex;flex-wrap:wrap;align-items:center;gap:8px 14px}
.demo b{font-variant-numeric:tabular-nums}
.demo button{border:0;border-radius:8px;padding:7px 14px;font-weight:700;background:#fff;color:#13213A}
.demo button.sub{background:transparent;color:#fff;border:1px solid rgba(255,255,255,.5);font-weight:400}
.demo select{border-radius:6px;padding:4px 6px;color:#13213A;background:#fff;border:0}
.toast{bottom:130px!important}
.demo .tag{background:#E8A33D;color:#13213A;border-radius:999px;padding:1px 10px;font-weight:700;font-size:12px}
.demo .prog{flex:1;min-width:160px;height:6px;background:rgba(255,255,255,.2);border-radius:3px;position:relative;overflow:hidden}
.demo .prog i{position:absolute;left:0;top:0;bottom:0;background:#7CE0A8;width:0}
.demo .prog s{position:absolute;top:-2px;bottom:-2px;width:2px;background:#E8A33D;text-decoration:none}
.demo .talk{margin-top:8px;color:#C9D4E5;font-size:12.5px;line-height:1.6;max-height:3.3em;overflow:hidden}
.demo .talk span{color:#fff}
"""

DEMO_JS = r"""
(() => {
  const K = window.GraRecKadai, S = K.S, D = window.DEMO, $ = id => document.getElementById(id);
  // デモはサーバーにつながない（保存・書き出し以外の通信は何もしない）
  const realFetch = window.fetch.bind(window);
  window.fetch = async (url, opt) => (String(url).includes('export_kadai_pptx') ? realFetch(url, opt)
    : new Response('{"ok":true}', {status: 200, headers: {'Content-Type': 'application/json'}}));
  try { localStorage.removeItem('grarec_kadai_current'); } catch (_) {}
  Object.assign(S, {group_number: String(D.group), kadai_no: '1-1', session_id: 'demo', created_at: new Date().toISOString()});
  $('startOverlay').hidden = true; $('voiceOverlay').hidden = true;
  $('groupBadge').textContent = D.group + '班（デモ）'; $('groupBadge').hidden = false;
  ['rec', 'newBtn', 'nowBtn'].forEach(id => { const e = $(id); if (e) e.style.display = 'none'; });
  const iv = document.querySelector('.interval'); if (iv) iv.style.display = 'none';
  K.render();

  const fmt = s => { s = Math.max(0, Math.floor(s)); return Math.floor(s / 60) + ':' + String(s % 60).padStart(2, '0'); };
  const bar = document.createElement('div'); bar.className = 'demo';
  bar.innerHTML = `<div class="demo-row">
      <span class="tag">デモ</span>
      <button type="button" id="dPlay">▶ 再生</button>
      <label>速さ <select id="dSpeed"><option value="30">30倍</option><option value="60" selected>60倍（1分→1秒）</option><option value="120">120倍</option><option value="300">300倍</option></select></label>
      <button type="button" class="sub" id="dSkip">次のカードまで進む</button>
      <span>話し合い <b id="dNow">0:00</b> / ${fmt(D.end)}</span>
      <span>次のカード <b id="dNext">–</b></span>
      <div class="prog" id="dProg"><i id="dBar"></i></div>
      <button type="button" class="sub" id="dReset">最初から</button>
    </div>
    <div class="talk" id="dTalk">7/15の授業の${D.group}班の話し合い（文字起こし）を、今の設定（最初3分→以降10分・最大6枚）で再生します（${D.note}）。届いたカードはシートにドラッグできます。</div>`;
  document.body.appendChild(bar);
  D.batches.forEach(b => { const m = document.createElement('s'); m.style.left = (b.t / D.end * 100) + '%'; $('dProg').appendChild(m); });

  let t = 0, playing = false, next = 0, last = performance.now();
  const deliverUpTo = () => { while (next < D.batches.length && D.batches[next].t <= t){ K.deliver(D.batches[next].cards); next++; } };
  function paint(){
    $('dNow').textContent = fmt(t);
    $('dNext').textContent = next < D.batches.length ? fmt(D.batches[next].t) + '（あと ' + fmt(D.batches[next].t - t) + '）' : 'おわり';
    $('dBar').style.width = (t / D.end * 100) + '%';
    $('dPlay').textContent = playing ? '❚❚ 一時停止' : (t >= D.end ? '▶ もう一度' : '▶ 再生');
    if (t > 0){
      const recent = D.lines.filter(l => l[0] <= t && l[0] > t - 40).slice(-3);
      if (recent.length) $('dTalk').innerHTML = recent.map(l => `<span>${fmt(l[0])}</span> ` + l[1].replace(/[&<>]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]))).join('　');
    }
  }
  function loop(now){
    const dt = (now - last) / 1000; last = now;
    if (playing){
      t = Math.min(D.end, t + dt * +$('dSpeed').value);
      deliverUpTo();
      if (t >= D.end) playing = false;
      paint();
    }
    requestAnimationFrame(loop);
  }
  $('dPlay').onclick = () => { if (t >= D.end) return location.reload(); playing = !playing; paint(); };
  $('dSkip').onclick = () => { if (next < D.batches.length){ t = D.batches[next].t; deliverUpTo(); paint(); } };
  $('dReset').onclick = () => location.reload();
  paint(); requestAnimationFrame(loop);
})();
"""

html = open(os.path.join(ROOT, "static", "kadai.html"), encoding="utf-8").read()
payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
inject = f"<style>{DEMO_CSS}</style>\n<script>window.DEMO = {payload};</script>\n<script>{DEMO_JS}</script>\n</body>"
assert html.count("</body>") == 1
out_dir = os.path.join(ROOT, "sim", "out")
os.makedirs(out_dir, exist_ok=True)
out = os.path.join(out_dir, f"demo_kadai_班{g}.html")
open(out, "w", encoding="utf-8").write(html.replace("</body>", inject))
n = sum(len(b["cards"]) for b in batches)
print(f"保存: {out}（カード {n} 枚 / {len(batches)} 回、話し合い {end / 60:.0f} 分）\n  使ったカード: {note}")
