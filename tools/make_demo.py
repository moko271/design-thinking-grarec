# -*- coding: utf-8 -*-
"""
録音データ（7/15の文字起こし）を使って、画面上で「どんなカードが・いつ・何枚届くか」を再生するデモを作る。
サーバーもAPIも使わない（カードは前もって作ったものを、時間どおりに届けるだけ）。

grarec_light フォルダで:
    python tools/make_demo.py                    # 課題シート（/kadai）・班31
    python tools/make_demo.py 6                  # 課題シート・班6
    python tools/make_demo.py 31 --sheet design  # デザイン思考ワークシート（今の画面＝/design）・班31
    python tools/make_demo.py 31 --sheet quantum # デザイン思考ワークシート（7/15当時の画面＝/quantum）・班31
→ sim/out/demo_<sheet>_班<g>.html（ダブルクリックでブラウザで開く）

カード
  kadai   … sim/sim_cards_live.py の結果（本番と同じタイミング）があればそれ、なければ sim/sim_cards.py の結果
  quantum … sim/sim_cards.py の結果（10/7のシミュレーション。今のカードの作り方・最初3分→以降10分・最大6枚、1回目）
            ※ 7/15当日にこのワークシートで出たカードとは別物（作り方が今のものに変わっている）
画面は static/kadai.html ／ static/index_ai.html そのもの。届いたカードは画面上で動かせる。
注意：文字起こしは学生の発話そのもの。sim/out は GitHub に上がらない場所なので、ファイルを配らないこと。
注意：班1・5・32 は正解（gold）を付ける前にカードを見ないことにしているので、既定は班31。
"""
import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "sim"))
from sim_common import load_transcript  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("group", nargs="?", type=int, default=31)
ap.add_argument("--sheet", choices=["kadai", "design", "quantum"], default="kadai")
a = ap.parse_args()
g = a.group
KEYS = ("keyword", "quote", "kind", "lone", "reason", "type", "thought", "importance")

live = os.path.join(ROOT, "sim", "out", "cards_live", f"班{g}_place1.json")
if a.sheet == "kadai" and os.path.exists(live):
    d = json.load(open(live, encoding="utf-8"))
    batches = [{"t": b["deliver_sec"], "cards": [{k: c.get(k) for k in KEYS} for c in b["cards"]]}
               for b in d["batches"] if b.get("cards")]
    note = "本番と同じタイミング（90秒ごとの区間・届くまでの遅れ・出ているカード全部をAIに渡す）で作ったカード"
else:
    src = os.path.join(ROOT, "sim", "out", "cards", "plan3-10_max6", f"班{g}_run1.json")
    d = json.load(open(src, encoding="utf-8"))
    batches = [{"t": b["t_sec"], "cards": [{k: c.get(k) for k in KEYS} for c in b["cards"]]}
               for b in d["batches"] if b.get("cards")]
    note = "10/7のシミュレーションで前もって作ったカード（今の作り方）。タイミングは本番と少し違う"
lines = [[t, x] for t, x in load_transcript(g)]
end = max(lines[-1][0] if lines else 0, batches[-1]["t"] if batches else 0)
data = {"group": g, "sheet": a.sheet, "note": note, "batches": batches, "lines": lines, "end": end}

DEMO_CSS = """
body{padding-bottom:150px}
.demo{position:fixed;left:0;right:0;bottom:0;z-index:99990;background:#13213A;color:#fff;box-shadow:0 -4px 16px rgba(0,0,0,.25);padding:10px 16px 12px;font-size:13px;font-family:"Hiragino Sans","Noto Sans JP",sans-serif}
.demo-row{display:flex;flex-wrap:wrap;align-items:center;gap:8px 14px}
.demo b{font-variant-numeric:tabular-nums}
.demo button{border:0;border-radius:8px;padding:7px 14px;font-weight:700;background:#fff;color:#13213A;cursor:pointer;font-size:13px}
.demo button.sub{background:transparent;color:#fff;border:1px solid rgba(255,255,255,.5);font-weight:400}
.demo select{border-radius:6px;padding:4px 6px;color:#13213A;background:#fff;border:0}
.demo .tag{background:#E8A33D;color:#13213A;border-radius:999px;padding:1px 10px;font-weight:700;font-size:12px}
.demo .prog{flex:1;min-width:160px;height:6px;background:rgba(255,255,255,.2);border-radius:3px;position:relative;overflow:hidden}
.demo .prog i{position:absolute;left:0;top:0;bottom:0;background:#7CE0A8;width:0}
.demo .prog s{position:absolute;top:-2px;bottom:-2px;width:2px;background:#E8A33D;text-decoration:none}
.demo .talk{margin-top:8px;color:#C9D4E5;font-size:12.5px;line-height:1.6;max-height:3.3em;overflow:hidden}
.demo .talk span{color:#fff}
.toast{bottom:130px!important}
"""

# 画面ごとの準備と「カードを届ける」処理
SETUP_KADAI = r"""
window.DEMO_SETUP = () => {
  const K = window.GraRecKadai, S = K.S, D = window.DEMO, $ = id => document.getElementById(id);
  try { localStorage.removeItem('grarec_kadai_current'); } catch (_) {}
  Object.assign(S, {group_number: String(D.group), kadai_no: '1-1', session_id: 'demo', created_at: new Date().toISOString()});
  $('startOverlay').hidden = true; $('voiceOverlay').hidden = true;
  $('groupBadge').textContent = D.group + '班（デモ）'; $('groupBadge').hidden = false;
  ['rec', 'newBtn', 'nowBtn'].forEach(id => { const e = $(id); if (e) e.style.display = 'none'; });
  const iv = document.querySelector('.interval'); if (iv) iv.style.display = 'none';
  K.render();
};
window.DEMO_DELIVER = cards => window.GraRecKadai.deliver(cards);
"""
SETUP_DESIGN = r"""
window.DEMO_SETUP = () => {
  const K = window.GraRecKadai, S = K.S, D = window.DEMO, $ = id => document.getElementById(id);
  try { localStorage.removeItem('grarec_design_current'); } catch (_) {}
  Object.assign(S, {group_number: String(D.group), session_id: 'demo', created_at: new Date().toISOString(),
    task_hint: '量子力学をわかりやすく伝えよう',
    slides: [{id: 's1', tpl: 'saguru'}, {id: 's2', tpl: 'kizuku'}, {id: 's3', tpl: 'hirameku'}], cur: 's1'});
  $('leaderId').value = S.task_hint;
  $('startOverlay').hidden = true; $('voiceOverlay').hidden = true;
  $('groupBadge').textContent = D.group + '班（デモ）'; $('groupBadge').hidden = false;
  ['rec', 'newBtn', 'nowBtn'].forEach(id => { const e = $(id); if (e) e.style.display = 'none'; });
  const iv = document.querySelector('.interval'); if (iv) iv.style.display = 'none';
  K.render();
};
window.DEMO_DELIVER = cards => window.GraRecKadai.deliver(cards);
"""
SETUP_QUANTUM = r"""
window.DEMO_SETUP = () => {
  const D = window.DEMO, $ = id => document.getElementById(id);
  ['onboardingModal', 'setupModal'].forEach(id => { const e = $(id); if (e) e.classList.add('hidden'); });
  // 量子モードのはじめの画面と同じ手順で始める（支援あり）
  $('quantum-group-input').value = String(D.group);
  const sb = $('quantum-start-btn'); sb.disabled = false; sb.click();
  setTimeout(() => { try { setRecordingControlsEnabled(false); } catch (_) {} }, 300);
};
window.DEMO_DELIVER = cards => {
  // 7/15のワークシートと同じ：新しいカードを出す前に、シートに置かれていないAIカードを下げる
  document.querySelectorAll('#canvas .note[data-area="unplaced"]').forEach(n => {
    if (n.dataset.source !== 'manual' && n.dataset.archived !== 'true'){ n.dataset.archived = 'true'; n.style.display = 'none'; }
  });
  cards.forEach(c => {
    const kw = c.keyword || ''; if (!kw) return;
    const imp = c.importance || ((c.kind === 'question' || c.kind === 'difference') ? 'high' : 'medium');
    addKeywordCard(kw, getEmojiForKeyword(kw), c.quote || '', c.type || '', c.thought || '', '', '', '', undefined, imp);
  });
  try { saveSession(); } catch (_) {}
};
"""

DEMO_JS = r"""
(() => {
  // デモはサーバーにつながない（kadai の PowerPoint 書き出しだけは、サーバーがあれば使う）
  const realFetch = window.fetch.bind(window);
  window.fetch = async (url, opt) => (String(url).includes('export_kadai_pptx') ? realFetch(url, opt)
    : new Response('{"ok":true}', {status: 200, headers: {'Content-Type': 'application/json'}}));
  const start = () => {
    const D = window.DEMO, $ = id => document.getElementById(id);
    window.DEMO_SETUP();
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
      <div class="talk" id="dTalk">7/15の授業の${D.group}班の話し合い（文字起こし）を再生します（${D.note}）。届いたカードは画面上で動かせます。</div>`;
    document.body.appendChild(bar);
    D.batches.forEach(b => { const m = document.createElement('s'); m.style.left = (b.t / D.end * 100) + '%'; $('dProg').appendChild(m); });
    let t = 0, playing = false, next = 0, last = performance.now();
    const deliverUpTo = () => { while (next < D.batches.length && D.batches[next].t <= t){ window.DEMO_DELIVER(D.batches[next].cards); next++; } };
    const esc = s => s.replace(/[&<>]/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;'}[c]));
    function paint(){
      $('dNow').textContent = fmt(t);
      $('dNext').textContent = next < D.batches.length ? fmt(D.batches[next].t) + '（あと ' + fmt(D.batches[next].t - t) + '）' : 'おわり';
      $('dBar').style.width = (t / D.end * 100) + '%';
      $('dPlay').textContent = playing ? '❚❚ 一時停止' : (t >= D.end ? '▶ もう一度' : '▶ 再生');
      if (t > 0){
        const recent = D.lines.filter(l => l[0] <= t && l[0] > t - 40).slice(-3);
        if (recent.length) $('dTalk').innerHTML = recent.map(l => `<span>${fmt(l[0])}</span> ` + esc(l[1])).join('　');
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
  };
  // 画面側の初期化（load）が終わってから始める
  if (document.readyState === 'complete') setTimeout(start, 50);
  else window.addEventListener('load', () => setTimeout(start, 50));
})();
"""

base = "index_ai.html" if a.sheet == "quantum" else "kadai.html"
html = open(os.path.join(ROOT, "static", base), encoding="utf-8").read()
payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
setup = {"kadai": SETUP_KADAI, "design": SETUP_DESIGN, "quantum": SETUP_QUANTUM}[a.sheet]
if a.sheet == "design":      # 画面のスクリプトより先に「デザイン思考ワークシート」と伝える
    assert html.count("<head>") == 1
    html = html.replace("<head>", "<head>\n<script>window.FORCE_SHEET = 'design';</script>")
inject = f"<style>{DEMO_CSS}</style>\n<script>window.DEMO = {payload};</script>\n<script>{setup}</script>\n<script>{DEMO_JS}</script>\n</body>"
assert html.count("</body>") == 1
out_dir = os.path.join(ROOT, "sim", "out")
os.makedirs(out_dir, exist_ok=True)
out = os.path.join(out_dir, f"demo_{a.sheet}_班{g}.html")
open(out, "w", encoding="utf-8").write(html.replace("</body>", inject))
n = sum(len(b["cards"]) for b in batches)
print(f"保存: {out}（カード {n} 枚 / {len(batches)} 回、話し合い {end / 60:.0f} 分）\n  使ったカード: {note}")
