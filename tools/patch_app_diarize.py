# -*- coding: utf-8 -*-
"""app.py に「声の登録」と「話者分離つき文字起こし（gpt-4o-transcribe-diarize）」を入れるパッチ。
何度実行しても同じ結果になる。"""
import sys

p = sys.argv[1] if len(sys.argv) > 1 else "app.py"
s = open(p, encoding="utf-8").read()

BLOCK = r'''
# =========================
# 課題シート（/kadai）: 声の登録と話者分離つき文字起こし
# =========================
# 話者分離は OpenAI 側（gpt-4o-transcribe-diarize）で行うので、サーバーのメモリはほぼ増えない
# （2026-08-21 の測定で +13MB。pyannote をサーバーで動かすと 2GB を超えて落ちた）。
# 区間ごとに話者ラベルがリセットされる問題は、最初に登録した声（最大4人・各2〜10秒）を
# 毎回 known_speaker_references として渡して解決する。
# 画面では名前を出さず色（v1〜v4）だけを使うので、OpenAI にも名前は送らず v1〜v4 で送る。
# ※ gpt-4o-transcribe-diarize は 2027-02-26 に提供終了予定。事業化では差し替える前提で、
#    呼び出しを diarize_transcribe() に閉じ込めている。
import httpx  # noqa: E402

VOICES_DIR = os.getenv("VOICES_DIR", os.path.join(DATA_DIR, "voices"))
os.makedirs(VOICES_DIR, exist_ok=True)
VOICE_SLOTS = ("v1", "v2", "v3", "v4")
VOICE_TTL_SEC = 12 * 3600          # 声のサンプルは最長12時間で自動削除
DIARIZE_MODEL = os.getenv("DIARIZE_MODEL", "gpt-4o-transcribe-diarize")


def _voice_dir(session_id: str) -> str:
    return os.path.join(VOICES_DIR, _sanitize_session_id(session_id))


def _purge_old_voices():
    now = datetime.now().timestamp()
    for sid in os.listdir(VOICES_DIR):
        d = os.path.join(VOICES_DIR, sid)
        try:
            if os.path.isdir(d) and now - os.path.getmtime(d) > VOICE_TTL_SEC:
                shutil.rmtree(d, ignore_errors=True)
        except OSError:
            pass


def _to_wav(src: str, dst: str, max_sec: float = None):
    cmd = ["ffmpeg", "-y", "-i", src, "-ar", "16000", "-ac", "1"]
    if max_sec:
        cmd += ["-t", str(max_sec)]
    cmd.append(dst)
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError("ffmpeg失敗: " + r.stderr[-200:])


def _wav_seconds(path: str) -> float:
    r = subprocess.run(["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", path],
                       capture_output=True, text=True)
    try:
        return float(json.loads(r.stdout)["format"]["duration"])
    except Exception:
        return 0.0


@app.route("/api/register_voice", methods=["POST"])
def register_voice():
    """声のサンプル（5秒ほど）を受け取り、wav(16kHz mono, 最大10秒)で保存する。"""
    _purge_old_voices()
    if "audio" not in request.files:
        return jsonify({"ok": False, "error": "no audio"}), 400
    sid = request.form.get("session_id", "")
    slot = request.form.get("slot", "")
    if not sid or slot not in VOICE_SLOTS:
        return jsonify({"ok": False, "error": "session_id と slot(v1〜v4) が必要です"}), 400
    d = _voice_dir(sid)
    os.makedirs(d, exist_ok=True)
    suffix = os.path.splitext(request.files["audio"].filename or "")[1] or ".webm"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp_path = tmp.name
        request.files["audio"].save(tmp_path)
    dst = os.path.join(d, f"{slot}.wav")
    try:
        _to_wav(tmp_path, dst, max_sec=10)
        sec = _wav_seconds(dst)
        if sec < 2:
            os.remove(dst)
            return jsonify({"ok": False, "error": "声が短すぎました。もう一度、文を最後まで読んでください"}), 400
        return jsonify({"ok": True, "slot": slot, "seconds": round(sec, 1)})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass


@app.route("/api/delete_voices", methods=["POST"])
def delete_voices():
    sid = (request.get_json() or {}).get("session_id", "")
    if sid:
        shutil.rmtree(_voice_dir(sid), ignore_errors=True)
    return jsonify({"ok": True})


def diarize_transcribe(wav_path: str, session_id: str):
    """話者分離つき文字起こし。戻り値: (text, segments[{speaker,start,end,text}])"""
    d = _voice_dir(session_id)
    names, refs = [], []
    for slot in VOICE_SLOTS:
        f = os.path.join(d, f"{slot}.wav")
        if os.path.exists(f):
            with open(f, "rb") as fh:
                refs.append("data:audio/wav;base64," + base64.b64encode(fh.read()).decode())
            names.append(slot)
    data = {"model": DIARIZE_MODEL, "response_format": "diarized_json",
            "chunking_strategy": "auto", "language": "ja"}
    if names:
        data["known_speaker_names[]"] = names
        data["known_speaker_references[]"] = refs
    with open(wav_path, "rb") as fh:
        resp = httpx.post("https://api.openai.com/v1/audio/transcriptions",
                          headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
                          files={"file": ("segment.wav", fh.read(), "audio/wav")},
                          data=data, timeout=120.0)
    if resp.status_code != 200:
        raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:200]}")
    result = resp.json()
    segs = []
    for sg in result.get("segments", []) or []:
        spk = sg.get("speaker") or sg.get("speaker_label") or ""
        segs.append({"speaker": spk if spk in VOICE_SLOTS else "other",
                     "start": float(sg.get("start", 0) or 0), "end": float(sg.get("end", 0) or 0),
                     "text": (sg.get("text") or "").strip()})
    text = (result.get("text") or "").strip() or " ".join(x["text"] for x in segs)
    return text, segs


@app.route("/api/transcribe_diarize", methods=["POST"])
def transcribe_diarize():
    """
    90秒ごとの区間を話者分離つきで文字起こしする。
    失敗したときは話者なしの文字起こし（whisper-1）に切り替え、カード作りは止めない。
    """
    if "audio" not in request.files:
        return jsonify({"ok": False, "error": "no audio file"}), 400
    audio_file = request.files["audio"]
    suffix = os.path.splitext(audio_file.filename or "")[1] or ".webm"
    sid = request.form.get("session_id", "")
    safe_sid = re.sub(r'[^\w\-]', '_', sid, flags=re.UNICODE)[:40] if sid else ""
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp_path = tmp.name
        audio_file.save(tmp_path)
    wav_path = tmp_path + ".wav"
    try:
        _to_wav(tmp_path, wav_path)
        try:
            text, segs = diarize_transcribe(wav_path, sid)
            speech = defaultdict(float)
            for sg in segs:
                speech[sg["speaker"]] += max(0.0, sg["end"] - sg["start"])
            return jsonify({"ok": True, "diarized": True, "text": text, "segments": segs,
                            "speech_sec": {k: round(v, 1) for k, v in speech.items()}})
        except Exception as e:
            print("diarize失敗 → 話者なしで文字起こし:", e)
            text = (split_and_transcribe(wav_path, ".wav", hint=GENERIC_WHISPER_HINT) or "").strip()
            return jsonify({"ok": True, "diarized": False, "text": text, "segments": [], "speech_sec": {},
                            "warning": "話者分離に失敗したため、この区間は話者なしで文字起こししました"})
    except Exception as e:
        print("transcribe_diarize error:", traceback.format_exc())
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        try:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            sid_part = f"_{safe_sid}" if safe_sid else ""
            shutil.copy2(tmp_path, os.path.join(RECORDINGS_DIR, f"rec{sid_part}_{ts}{suffix}"))
        except Exception as _e:
            print(f"ERROR: 録音ファイル保存失敗: {_e}")
        for f in (tmp_path, wav_path):
            try:
                os.remove(f)
            except OSError:
                pass
'''

if "def transcribe_diarize():" not in s:
    anchor = '''# =========================
# エントリポイント
# ========================='''
    assert s.count(anchor) == 1
    s = s.replace(anchor, BLOCK.lstrip("\n") + "\n\n" + anchor)
    open(p, "w", encoding="utf-8").write(s)
print("patched", p)
