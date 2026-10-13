# -*- coding: utf-8 -*-
"""
管理画面：1つのセッションだけをまとめてダウンロードできるようにする（2026-10-13）
  /api/download_session_zip/<session_id>（管理者のみ）
    → session.json ＋ そのセッションの録音（rec_<セッション名>_*.webm など）＋ タイムラプス画像
  admin.html：セッション一覧の各行に「このセッションだけZIP」ボタン
使い方：python tools/patch_session_zip.py app.py static/admin.html（何度実行しても同じ結果）
"""
import sys
app_p = sys.argv[1] if len(sys.argv) > 1 else "app.py"
adm_p = sys.argv[2] if len(sys.argv) > 2 else "static/admin.html"

s = open(app_p, encoding="utf-8").read()
if "def download_session_zip" not in s:
    anchor = '''@app.route("/api/download_timelapse_zip/<path:session_id>", methods=["GET"])'''
    assert s.count(anchor) == 1
    new = '''@app.route("/api/download_session_zip/<path:session_id>", methods=["GET"])
@require_admin
def download_session_zip(session_id):
    """1つのセッションだけ：セッションJSON・録音・タイムラプスをまとめてZIPで"""
    sid = _sanitize_session_id(session_id)
    jpath = os.path.join(SESSIONS_DIR, f"{sid}.json")
    if not os.path.exists(jpath):
        return jsonify({"ok": False, "error": "session not found"}), 404
    rec_prefix = "rec_" + re.sub(r'[^\\w\\-]', '_', session_id, flags=re.UNICODE)[:40] + "_"
    buf = io.BytesIO()
    n_rec = n_tl = 0
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(jpath, f"{sid}/session.json")
        if os.path.isdir(RECORDINGS_DIR):
            for fname in sorted(os.listdir(RECORDINGS_DIR)):
                if fname.startswith(rec_prefix):
                    zf.write(os.path.join(RECORDINGS_DIR, fname), f"{sid}/recordings/{fname}"); n_rec += 1
        tdir = os.path.join(TIMELAPSE_DIR, sid)
        if os.path.isdir(tdir):
            for fname in sorted(os.listdir(tdir)):
                if fname.endswith(".png"):
                    zf.write(os.path.join(tdir, fname), f"{sid}/timelapse/{fname}"); n_tl += 1
    buf.seek(0)
    ascii_id = re.sub(r'[^\\w\\-]', '_', session_id, flags=re.ASCII)[:60]
    cd = f"attachment; filename=\\"session_{ascii_id}.zip\\"; filename*=UTF-8''{urlquote(f'session_{sid}.zip', safe='')}"
    print(f"セッションZIP: {sid} 録音{n_rec} タイムラプス{n_tl}")
    return Response(buf.read(), mimetype="application/zip", headers={"Content-Disposition": cd})


'''
    s = s.replace(anchor, new + anchor)
    open(app_p, "w", encoding="utf-8").write(s); print(app_p, "patched")
else:
    print(app_p, "already patched")

h = open(adm_p, encoding="utf-8").read()
if "download_session_zip" not in h:
    old = '''          <button class="del-btn" style="background:#37474f;color:#fff;" data-sid="${esc(s.session_id)}" onclick="showRecordingsForSession(this, event)">録音</button>'''
    assert h.count(old) >= 1, "anchor not found"
    new = old + '''
          ／
          <a href="/api/download_session_zip/${encodeURIComponent(s.session_id)}" onclick="event.stopPropagation()" title="このセッションのJSON・録音・タイムラプスだけをZIPで">
            <button class="del-btn" style="background:#2e7d32;color:#fff;">このセッションだけZIP</button>
          </a>'''
    h = h.replace(old, new)
    open(adm_p, "w", encoding="utf-8").write(h); print(adm_p, "patched")
else:
    print(adm_p, "already patched")
