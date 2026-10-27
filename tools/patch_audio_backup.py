# -*- coding: utf-8 -*-
"""
kadai.html：録音をブラウザの中にも控えておき、「記録と録音を保存」で手元のPCに保存できるようにする（2026-10-13）
  背景：10/13の授業で Render のディスクがいっぱいになり、録音と記録がサーバーに残らなかった
  ・90秒ごとの区間の録音を、サーバーに送るのと同時にブラウザの中（IndexedDB）にも控える（再読み込みしても残る）
  ・上のバーの「記録と録音を保存」：その班の記録（session.json）と録音を1つのZIPにして保存
      ZIPを作る部品（JSZip）が読み込めないときは、記録と録音を1つずつ保存する
  ・録音を止めたとき「授業が終わったら『記録と録音を保存』を押してください」と出す
  ・ブラウザの中の控えは、14日たったら自動で消す（共用PCに声が残り続けないように）
  ・記録：audio_backup_fail（控えに失敗）、backup_download（保存した区間数・大きさ）
何度実行しても同じ結果になる。
"""
import sys

p = sys.argv[1] if len(sys.argv) > 1 else "static/kadai.html"
s = open(p, encoding="utf-8").read()
if "AudioStore" in s:
    print("already patched"); sys.exit(0)


def rep(old, new):
    global s
    assert s.count(old) == 1, old[:80]
    s = s.replace(old, new)


rep('''  <button class="bar-btn" id="newBtn" type="button"''',
    '''  <button class="bar-btn" id="backupBtn" type="button" title="この班の記録と録音を、このPCに保存します（サーバーに残らなかったときの控え）">記録と録音を保存</button>
  <button class="bar-btn" id="newBtn" type="button"''')

rep('''    if (blob.size < 2000){ log('seg_empty', null, {seg_start: t0, seg_end: t1, bytes: blob.size}); return; }   // ほぼ無音・極短''',
    '''    if (blob.size < 2000){ log('seg_empty', null, {seg_start: t0, seg_end: t1, bytes: blob.size}); return; }   // ほぼ無音・極短
    AudioStore.put(S.session_id, t0, t1, blob).catch(e => log('audio_backup_fail', null, {seg_start: t0, error: String(e && e.message || e).slice(0, 80)}));''')

rep('''  log('rec_stop'); paintRec(); save();''', '''  log('rec_stop'); paintRec(); save();
  banner('授業が終わったら、上の「記録と録音を保存」を押して、このPCにも保存してください');''')

rep('''$('newBtn').onclick = () => {''', r'''// ================= 録音の控え（ブラウザの中）と、手元への保存 =================
const AudioStore = (() => {
  let dbp = null;
  const open = () => dbp || (dbp = new Promise((res, rej) => {
    if (!('indexedDB' in window)) return rej(new Error('indexedDB なし'));
    const r = indexedDB.open('grarec_audio', 1);
    r.onupgradeneeded = () => { const st = r.result.createObjectStore('seg', {keyPath: 'key'}); st.createIndex('sid', 'sid'); };
    r.onsuccess = () => res(r.result); r.onerror = () => rej(r.error);
  }));
  const done = t => new Promise((res, rej) => { t.oncomplete = () => res(); t.onerror = () => rej(t.error); t.onabort = () => rej(t.error); });
  return {
    async put(sid, t0, t1, blob){
      const db = await open(); const t = db.transaction('seg', 'readwrite');
      t.objectStore('seg').put({key: sid + '|' + t0, sid, t0, t1, type: blob.type, blob, savedAt: Date.now()});
      return done(t);
    },
    async list(sid){
      const db = await open(); const out = [];
      await new Promise((res, rej) => {
        const req = db.transaction('seg').objectStore('seg').index('sid').openCursor(IDBKeyRange.only(sid));
        req.onsuccess = () => { const c = req.result; if (c){ out.push(c.value); c.continue(); } else res(); };
        req.onerror = () => rej(req.error);
      });
      return out.sort((a, b) => a.t0 - b.t0);
    },
    async purgeOlderThan(days){
      const db = await open(); const t = db.transaction('seg', 'readwrite'); const limit = Date.now() - days * 86400000;
      const req = t.objectStore('seg').openCursor();
      req.onsuccess = () => { const c = req.result; if (c){ if (c.value.savedAt < limit) c.delete(); c.continue(); } };
      return done(t);
    }
  };
})();
AudioStore.purgeOlderThan(14).catch(() => {});
if (navigator.storage && navigator.storage.persist) navigator.storage.persist().catch(() => {});
const p2 = n => String(n).padStart(2, '0');
const hms = ms => { const d = new Date(ms); return p2(d.getHours()) + p2(d.getMinutes()) + p2(d.getSeconds()); };
const asciiName = s => s.replace(/[^\w.\-]/g, '_');
function loadJSZip(){
  if (window.JSZip) return Promise.resolve(window.JSZip);
  return new Promise((res, rej) => {
    const sc = document.createElement('script');
    sc.src = 'https://cdnjs.cloudflare.com/ajax/libs/jszip/3.10.1/jszip.min.js';
    sc.onload = () => window.JSZip ? res(window.JSZip) : rej(new Error('JSZip なし'));
    sc.onerror = () => rej(new Error('JSZip を読み込めない'));
    document.head.appendChild(sc);
    setTimeout(() => rej(new Error('JSZip の読み込みが遅い')), 15000);
  });
}
function saveBlob(blob, name){
  const a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = name;
  document.body.appendChild(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(a.href), 10000);
}
$('backupBtn').onclick = async () => {
  if (!S.session_id){ toast('まだ記録がありません'); return; }
  const btn = $('backupBtn'); btn.disabled = true; const label = btn.textContent; btn.textContent = '保存の準備中…';
  try {
    const segs = await AudioStore.list(S.session_id).catch(() => []);
    const sid = S.session_id, base = asciiName(`grarec_g${S.group_number || 'x'}_${sid.replace(/^.*?_(\d{8}_\d{6})$/, '$1')}`);
    const json = new Blob([JSON.stringify(snapshot(), null, 1)], {type: 'application/json'});
    const files = segs.map(x => ({name: `recordings/rec_${hms(x.t0)}-${hms(x.t1)}.${extOf(x.type)}`, blob: x.blob}));
    const bytes = files.reduce((a, f) => a + f.blob.size, 0);
    try {
      const JSZip = await loadJSZip();
      const zip = new JSZip();
      zip.file('session.json', json);
      files.forEach(f => zip.file(f.name, f.blob));
      zip.file('README.txt', `GraRec の記録と録音（${sid}）\nsession.json … カード・置き方・テキスト・文字起こし・使われ方の記録\nrecordings/ … 90秒ごとの録音（ファイル名は録音の開始-終了時刻）\n`);
      saveBlob(await zip.generateAsync({type: 'blob', compression: 'STORE'}), base + '.zip');
    } catch (e) {
      // ZIPが作れないときは1つずつ（ブラウザが「複数ファイルのダウンロード」を聞いてきたら許可する）
      console.warn('zip', e);
      saveBlob(json, base + '_session.json');
      for (const f of files){ await new Promise(r => setTimeout(r, 400)); saveBlob(f.blob, base + '_' + f.name.replace('recordings/', '')); }
    }
    log('backup_download', null, {segments: files.length, bytes}); save();
    toast(`保存しました（記録と録音 ${files.length} 区間）`);
  } catch (e) {
    banner('保存できませんでした：' + (e && e.message || e));
  } finally { btn.disabled = false; btn.textContent = label; }
};

$('newBtn').onclick = () => {''')

open(p, "w", encoding="utf-8").write(s)
print("patched")
