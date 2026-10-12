# -*- coding: utf-8 -*-
"""
授業前の通し確認（サーバー側・本物のAPI）。grarec_light フォルダで .venv を有効にして実行する:
    python tools/rehearsal_api.py

ブラウザを使わずに、/kadai が授業中に呼ぶ順番どおりに API を呼ぶ：
  1. 声の登録（2人分）      /api/register_voice
  2. 話者分離つき文字起こし  /api/transcribe_diarize   ← 90秒の区間を2回
  3. カード作り              /api/extract_cards        ← 1回目（最初のカード）と2回目
  4. 話者なしの文字起こし    /api/transcribe_with_speakers（声を登録しなかったときの経路）
  5. シートの書き出し        /api/export_kadai_pptx
  6. 後片付け               /api/delete_voices ＋ テスト用の録音ファイルを削除

使う音声は interview_full.wav（mokoが録った会話）。誰がいつ話したかは interview_full.rttm
（pyannoteの推定）から読み、声の登録用に「その人だけが6秒続けて話している所」を切り出す。
話者分離の結果を rttm と比べて、だいたい合っているかも出す（rttm自体も推定なので目安）。

費用の目安：文字起こし 約3.5分ぶん＋カード2回。数十円程度。
結果は tools/rehearsal_result_<日時>.json に保存する（APIキーは書き出さない）。
"""
import glob
import json
import os
import subprocess
import sys
import tempfile
import time
import zipfile
from collections import defaultdict
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, ROOT)

WAV = os.path.join(ROOT, "interview_full.wav")
RTTM = os.path.join(ROOT, "interview_full.rttm")
SID = "rehearsal_test_" + datetime.now().strftime("%Y%m%d_%H%M%S")
SEG_SEC = 90
OK, NG, WARN = "✅", "❌", "⚠️"
results = {"session_id": SID, "started": datetime.now().isoformat(), "steps": []}
problems = 0


def step(name, ok, detail="", warn=False, **extra):
    global problems
    mark = OK if ok and not warn else (WARN if ok else NG)
    print(f"{mark} {name}" + (f" … {detail}" if detail else ""))
    if not ok:
        problems += 1
    results["steps"].append({"name": name, "ok": ok, "warn": warn, "detail": detail, **extra})


def cut(src, start, dur, dst):
    r = subprocess.run(["ffmpeg", "-y", "-v", "quiet", "-ss", str(start), "-t", str(dur), "-i", src,
                        "-ar", "16000", "-ac", "1", dst], capture_output=True)
    if r.returncode != 0:
        raise RuntimeError("ffmpeg で切り出せませんでした")


def read_rttm(path):
    segs = []
    for line in open(path, encoding="utf-8"):
        p = line.split()
        if len(p) >= 8 and p[0] == "SPEAKER":
            s, d = float(p[3]), float(p[4])
            segs.append((s, s + d, p[7]))
    return segs


def talk_in(segs, a, b):
    out = defaultdict(float)
    for s, e, spk in segs:
        o = min(e, b) - max(s, a)
        if o > 0:
            out[spk] += o
    return out


def solo_window(segs, spk, length=6.0, t_from=120, t_to=1800):
    """spk だけが length 秒続けて話している所（ほかの人の声が0.3秒未満）を探す"""
    for s, e, who in segs:
        if who != spk or s < t_from or s > t_to:
            continue
        for start in (s, s + 0.5, s + 1.0):
            a, b = start, start + length
            t = talk_in(segs, a, b)
            others = sum(v for k, v in t.items() if k != spk)
            if t.get(spk, 0) >= length * 0.8 and others < 0.3:
                return a
    return None


def busy_window(segs, two, length=SEG_SEC, t_from=120, t_to=2400):
    """登録した2人がどちらもよく話している90秒を探す（10秒刻み）"""
    best, best_a = -1, None
    a = t_from
    while a + length <= t_to:
        t = talk_in(segs, a, a + length)
        score = min(t.get(two[0], 0), t.get(two[1], 0))
        if score > best:
            best, best_a = score, a
        a += 10
    return best_a


def main():
    print(f"\n=== GraRec 授業前の通し確認（{SID}）===\n")
    # 0) 前提
    for f in (WAV, RTTM):
        if not os.path.exists(f):
            step(f"ファイル {os.path.basename(f)}", False, "grarec_light の直下にありません")
            return
    try:
        import app as A
    except Exception as e:
        step("app.py の読み込み", False, f"{type(e).__name__}: {e}（.venv を有効にした？ .env に OPENAI_API_KEY はある？）")
        return
    step("app.py の読み込み", True, f"カード: {A.CARD_MODEL} ／ 話者分離: {A.DIARIZE_MODEL}")
    client = A.app.test_client()

    segs = read_rttm(RTTM)
    total = defaultdict(float)
    for s, e, spk in segs:
        total[spk] += e - s
    two = [k for k, _ in sorted(total.items(), key=lambda kv: -kv[1])[:2]]
    tmp = tempfile.mkdtemp(prefix="grarec_rehearsal_")

    # 1) 声の登録
    slot_of = {}
    for slot, spk in zip(("v1", "v2"), two):
        a = solo_window(segs, spk)
        if a is None:
            step(f"声の登録 {slot}", False, f"{spk} だけが話している6秒が見つかりませんでした")
            continue
        clip = os.path.join(tmp, f"{slot}.wav")
        cut(WAV, a, 6, clip)
        t0 = time.time()
        with open(clip, "rb") as fh:
            r = client.post("/api/register_voice", data={"session_id": SID, "slot": slot,
                                                          "audio": (fh, "voice.wav")}, content_type="multipart/form-data")
        d = r.get_json() or {}
        ok = r.status_code == 200 and d.get("ok")
        step(f"声の登録 {slot}", ok, f"{d.get('seconds')}秒・{time.time() - t0:.1f}秒かかった（元: {spk} の {a / 60:.1f}分〜）"
             if ok else str(d.get("error")), slot=slot, rttm_speaker=spk, at_sec=a)
        if ok:
            slot_of[spk] = slot
    if len(slot_of) < 2:
        step("話者分離の確認", False, "声が2人分登録できなかったので、ここで止めます")
        return

    # 2) 話者分離つき文字起こし（90秒×2区間）
    a0 = busy_window(segs, two)
    texts, diar_rows = [], []
    for i, a in enumerate((a0, a0 + SEG_SEC)):
        clip = os.path.join(tmp, f"seg{i}.webm")
        subprocess.run(["ffmpeg", "-y", "-v", "quiet", "-ss", str(a), "-t", str(SEG_SEC), "-i", WAV,
                        "-c:a", "libopus", "-b:a", "32k", clip], capture_output=True)
        if not os.path.exists(clip):           # opus が使えない ffmpeg のときは wav で送る
            clip = os.path.join(tmp, f"seg{i}.wav"); cut(WAV, a, SEG_SEC, clip)
        t0 = time.time()
        with open(clip, "rb") as fh:
            r = client.post("/api/transcribe_diarize", data={"session_id": SID, "hint": "generic",
                                                              "audio": (fh, os.path.basename(clip))}, content_type="multipart/form-data")
        took = time.time() - t0
        d = r.get_json() or {}
        if not (r.status_code == 200 and d.get("ok")):
            step(f"話者分離つき文字起こし 区間{i + 1}", False, str(d.get("error")))
            continue
        truth = talk_in(segs, a, a + SEG_SEC)
        got = d.get("speech_sec") or {}
        row = {"segment": i + 1, "start_min": round(a / 60, 2), "took_sec": round(took, 1), "diarized": d.get("diarized"),
               "speech_sec": got, "rttm_sec": {slot_of.get(k, "other"): round(v, 1) for k, v in truth.items()},
               "n_segments": len(d.get("segments") or []), "chars": len(d.get("text") or "")}
        diar_rows.append(row)
        if not d.get("diarized"):
            step(f"話者分離つき文字起こし 区間{i + 1}", True, f"話者分離に失敗して話者なしに切り替わった：{d.get('warning')}", warn=True, **row)
        else:
            # rttm と比べた、登録した2人の割合の差
            def share(m):
                t = sum(m.get(s, 0) for s in ("v1", "v2"))
                return {s: (m.get(s, 0) / t if t else 0) for s in ("v1", "v2")}
            rt = defaultdict(float)
            for k, v in truth.items():
                rt[slot_of.get(k, "other")] += v
            gap = abs(share(got)["v1"] - share(rt)["v1"])
            slow = took > 60
            step(f"話者分離つき文字起こし 区間{i + 1}", True,
                 f"{took:.0f}秒・{row['chars']}文字・話した秒数 {got} ／ rttmでは {dict((k, round(v, 1)) for k, v in rt.items())}"
                 + f"・2人の割合のずれ {gap * 100:.0f}ポイント" + ("（90秒の区間より処理が遅い！）" if slow else ""),
                 warn=slow or gap > 0.25, share_gap=round(gap, 3), **row)
        segs_txt = [(("話者" + str(("v1", "v2", "v3", "v4").index(g["speaker"]) + 1)) if g["speaker"] in ("v1", "v2", "v3", "v4") else "話者?")
                    + "：" + g["text"] for g in (d.get("segments") or []) if g.get("text")]
        texts.append("\n".join(segs_txt) or d.get("text", ""))
    results["diarize"] = diar_rows

    # 3) カード作り（1回目＝最初のカード、2回目＝前回の文脈つき）
    cards_all = []
    if texts:
        prev = ""
        for i, memo in enumerate(texts):
            t0 = time.time()
            r = client.post("/api/extract_cards", json={"memo": memo, "prev_text": prev[-500:],
                                                        "existing": [c["keyword"] for c in cards_all],
                                                        "first_cycle": i == 0, "max_cards": 6, "task_hint": ""})
            d = r.get_json() or {}
            ok = r.status_code == 200 and d.get("ok")
            cs = d.get("cards") or []
            kinds = defaultdict(int)
            for c in cs:
                kinds[c.get("kind")] += 1
            grounded = sum(1 for c in cs if (c.get("quote") or "").replace(" ", "") and
                           (c.get("quote") or "").replace(" ", "")[:8] in memo.replace(" ", ""))
            step(f"カード作り {i + 1}回目", ok and len(cs) > 0,
                 (f"{len(cs)}枚・{time.time() - t0:.1f}秒・{d.get('model')}・種類 {dict(kinds)}・引用の書き出しが原文にある {grounded}/{len(cs)}"
                  if ok else str(d.get("error"))), cards=cs)
            cards_all += cs
            prev = memo

    # 4) 話者なしの文字起こし（声を登録しない班の経路）
    clip = os.path.join(tmp, "plain.wav"); cut(WAV, a0, 30, clip)
    t0 = time.time()
    with open(clip, "rb") as fh:
        r = client.post("/api/transcribe_with_speakers", data={"session_id": SID, "hint": "generic",
                                                                "audio": (fh, "segment.wav")}, content_type="multipart/form-data")
    d = r.get_json() or {}
    step("話者なしの文字起こし（30秒）", r.status_code == 200 and d.get("ok") and bool(d.get("text")),
         f"{len(d.get('text') or '')}文字・{time.time() - t0:.1f}秒" if d.get("ok") else str(d.get("error")))

    # 5) 書き出し
    placed = [{"keyword": c["keyword"], "x": 5 + (i % 4) * 23, "y": 10 + (i // 4) * 20} for i, c in enumerate(cards_all[:8])]
    r = client.post("/api/export_kadai_pptx", json={"kadai_no": "1-1", "leader_id": "0000000", "group_number": "99",
                                                     "members": [{"name": "テスト", "role": "leader"}], "cards": placed})
    out = os.path.join(ROOT, "tools", f"rehearsal_{SID}.pptx")
    ok = r.status_code == 200 and r.data[:2] == b"PK"
    if ok:
        open(out, "wb").write(r.data)
        with zipfile.ZipFile(out) as z:
            ok = any(n.startswith("ppt/slides/slide") for n in z.namelist())
    step("シートの書き出し", ok, f"{len(placed)}枚を置いたシート → tools/{os.path.basename(out)}（開いて見た目を確認してね）"
         if ok else (r.get_json() or {}).get("error", r.status_code))

    # 6) 後片付け
    client.post("/api/delete_voices", json={"session_id": SID})
    left_voice = os.path.exists(A._voice_dir(SID))
    removed = 0
    for f in glob.glob(os.path.join(A.RECORDINGS_DIR, f"rec_{SID}_*")):
        try:
            os.remove(f); removed += 1
        except OSError:
            pass
    step("後片付け", not left_voice, f"声のサンプルを削除・テスト録音 {removed} 件を削除")


if __name__ == "__main__":
    try:
        main()
    finally:
        results["problems"] = problems
        path = os.path.join(ROOT, "tools", f"rehearsal_result_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
        json.dump(results, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print(f"\n{'問題なし' if problems == 0 else f'問題 {problems} 件'}。結果: tools/{os.path.basename(path)}\n")
