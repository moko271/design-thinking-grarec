# -*- coding: utf-8 -*-
"""
保存を確実にする（2026-10-13）
  kadai.html
    ・サーバーへの保存に失敗したら、3秒→6秒→12秒→24秒→30秒ごとに自動でやり直す（成功するまで）
    ・2回続けて失敗したら、上のバーに「保存できていません（自動でやり直しています）」と出す。直ったら「保存できました」を少し出して消す
    ・失敗と回復を記録に残す（save_fail / save_recovered）
    ・保存待ちのままページを閉じるときは、閉じる直前に送る（大きすぎて送れないときは、ブラウザ内の控えが残る）
    ・保存できていない間は、ページを閉じようとすると確認が出る
  app.py
    ・セッションの保存は、別のファイルに書き終わってから入れ替える（途中で止まっても前のファイルが壊れない）
使い方：python tools/patch_save_safety.py static/kadai.html app.py（何度実行しても同じ結果）
"""
import sys

html_p = sys.argv[1] if len(sys.argv) > 1 else "static/kadai.html"
app_p = sys.argv[2] if len(sys.argv) > 2 else "app.py"


def patch(path, pairs, marker):
    s = open(path, encoding="utf-8").read()
    if marker in s:
        print(path, "already patched"); return
    for old, new in pairs:
        assert s.count(old) == 1, (path, old[:80])
        s = s.replace(old, new)
    open(path, "w", encoding="utf-8").write(s)
    print(path, "patched")


patch(html_p, [
    ("/* usage log */", """/* 保存の状態 */
.save-state{font-size:12px;border-radius:999px;padding:4px 10px;background:#FEF3C7;color:#92400E;font-weight:700;white-space:nowrap}
.save-state.ok{background:#DCFCE7;color:#166534}

/* usage log */"""),
    ('''  <button class="export" id="exportBtn" type="button">PowerPointで書き出す</button>''',
     '''  <span class="save-state" id="saveState" role="status" aria-live="polite" hidden></span>
  <button class="export" id="exportBtn" type="button">PowerPointで書き出す</button>'''),
    ('''function save(){
  if (!S.session_id) return;
  try { localStorage.setItem(STORE_KEY, JSON.stringify(snapshot())); } catch (_) {}
  clearTimeout(saveTimer);
  saveTimer = setTimeout(async () => {
    try {
      const res = await fetch('/api/save_session', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(snapshot())});
      if (!res.ok) throw new Error(res.status);
    } catch (e) { console.warn('save', e); }
  }, 2000);
}''', '''// サーバーへの保存：失敗したら間隔をあけて自動でやり直す。2回続けて失敗したら画面に出す
let saveFails = 0, saveRetryTimer = null, saveOkTimer = null, saving = false, savePending = false;
function save(){
  if (!S.session_id) return;
  try { localStorage.setItem(STORE_KEY, JSON.stringify(snapshot())); } catch (_) {}
  clearTimeout(saveTimer);
  saveTimer = setTimeout(pushSave, 2000);
}
async function pushSave(){
  saveTimer = null; clearTimeout(saveRetryTimer); saveRetryTimer = null;
  if (!S.session_id) return;
  if (saving){ savePending = true; return; }          // 送っている途中なら、終わってからもう一度
  saving = true;
  try {
    const res = await fetch('/api/save_session', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(snapshot())});
    if (!res.ok) throw new Error('HTTP ' + res.status);
    const d = await res.json().catch(() => ({}));
    if (d && d.ok === false) throw new Error(d.error || 'save failed');
    if (saveFails){ log('save_recovered', null, {fails: saveFails}); saveFails = 0; paintSaveState(true); savePending = true; }
  } catch (e) {
    console.warn('save', e);
    saveFails++;
    if (saveFails === 1) log('save_fail', null, {error: String(e && e.message || e).slice(0, 80)});
    paintSaveState(false);
    saveRetryTimer = setTimeout(pushSave, Math.min(30000, 3000 * 2 ** (saveFails - 1)));
  } finally {
    saving = false;
    if (savePending && !saveRetryTimer){ savePending = false; clearTimeout(saveTimer); saveTimer = setTimeout(pushSave, 300); }
  }
}
function paintSaveState(recovered){
  const el = $('saveState'); if (!el) return;
  clearTimeout(saveOkTimer);
  if (saveFails >= 2){ el.className = 'save-state'; el.textContent = '保存できていません（自動でやり直しています）'; el.hidden = false; }
  else if (recovered && !el.hidden){ el.className = 'save-state ok'; el.textContent = '保存できました'; saveOkTimer = setTimeout(() => { el.hidden = true; }, 3000); }
  else if (!saveFails) el.hidden = true;
}
// 保存待ちのまま閉じるときは、閉じる直前に送る（64KBまで。それより大きいときはブラウザ内の控えが残る）
window.addEventListener('pagehide', () => {
  if (!S.session_id || (!saveTimer && !saveRetryTimer)) return;
  try {
    const body = JSON.stringify(snapshot());
    if (body.length < 60000 && navigator.sendBeacon) navigator.sendBeacon('/api/save_session', new Blob([body], {type: 'application/json'}));
  } catch (_) {}
});'''),
    ('''window.addEventListener('beforeunload', e => { if (recording){ e.preventDefault(); e.returnValue = ''; } });''',
     '''window.addEventListener('beforeunload', e => { if (recording || saveFails){ e.preventDefault(); e.returnValue = ''; } });'''),
], "pushSave")

patch(app_p, [
    ('''    filepath = os.path.join(SESSIONS_DIR, f"{session_id}.json")
    try:
        os.makedirs(SESSIONS_DIR, exist_ok=True)
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"セッション保存OK: {filepath}")''', '''    filepath = os.path.join(SESSIONS_DIR, f"{session_id}.json")
    try:
        os.makedirs(SESSIONS_DIR, exist_ok=True)
        # 別のファイルに書き終わってから入れ替える（途中でサーバーが止まっても、前のファイルが壊れない）
        tmp_path = f"{filepath}.{os.getpid()}.{threading.get_ident()}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, filepath)
        print(f"セッション保存OK: {filepath}")'''),
    ('''import zipfile''', '''import zipfile
import threading'''),
], "os.replace(tmp_path, filepath)")
