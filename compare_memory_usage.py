#!/usr/bin/env python3
"""
compare_memory_usage.py
──────────────────────────────────────────────────────────────
方式A（pyannote.audio）と方式B（gpt-4o-transcribe-diarize）を
同一音声ファイルで比較し、メモリ使用量・処理時間を記録する。

使い方:
  python compare_memory_usage.py <音声ファイルパス> [オプション]

オプション:
  --skip-pyannote     方式Aをスキップ（pyannote未インストール環境用）
  --skip-diarize      方式Bをスキップ
  --hf-token TOKEN    HuggingFaceトークン（未指定時は環境変数 HF_TOKEN）
  --output DIR        結果出力先ディレクトリ（デフォルト: カレント）

Render上での実行例:
  python compare_memory_usage.py data/recordings/rec_xxx.webm --skip-pyannote

SDK バージョン補足:
  openai>=1.45.0 では client.audio.transcriptions.create(...) で直接
  model="gpt-4o-transcribe-diarize" を使用可能。
  本スクリプトでは旧SDK（1.12.0）でも動くよう httpx 直接呼び出しを使用。
"""

import os
import sys
import time
import json
import csv
import argparse
import threading
import traceback
import tempfile
import math
from datetime import datetime
from pathlib import Path

import psutil
import httpx
from dotenv import load_dotenv
from pydub import AudioSegment

load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

# ============================================================
# ピークメモリ測定ユーティリティ
# ============================================================

class PeakMemoryTracker:
    """
    バックグラウンドスレッドで RSS を定期サンプリングし、
    処理中のピーク使用量を記録する。

    使い方:
        with PeakMemoryTracker() as tracker:
            heavy_processing()
        print(tracker.peak_mb, tracker.delta_mb)
    """

    def __init__(self, interval_s: float = 0.1):
        self._proc     = psutil.Process()
        self._interval = interval_s
        self._stop     = threading.Event()
        self._thread   = None
        self.baseline_mb: float = 0.0
        self.peak_mb:     float = 0.0
        self.end_mb:      float = 0.0

    def _sample_loop(self):
        while not self._stop.is_set():
            try:
                mb = self._proc.memory_info().rss / 1024 / 1024
                if mb > self.peak_mb:
                    self.peak_mb = mb
            except Exception:
                pass
            self._stop.wait(self._interval)

    def __enter__(self):
        self.baseline_mb = self._proc.memory_info().rss / 1024 / 1024
        self.peak_mb     = self.baseline_mb
        self._stop.clear()
        self._thread = threading.Thread(target=self._sample_loop, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *_):
        self._stop.set()
        self._thread.join(timeout=2)
        self.end_mb = self._proc.memory_info().rss / 1024 / 1024

    @property
    def delta_mb(self) -> float:
        return self.peak_mb - self.baseline_mb


def measure_memory(fn):
    """
    デコレータ版: 関数前後のメモリ使用量を標準出力にログする。

    @measure_memory
    def heavy():
        ...
    """
    def wrapper(*args, **kwargs):
        with PeakMemoryTracker() as t:
            result = fn(*args, **kwargs)
        print(f"[measure_memory] {fn.__name__}: "
              f"baseline={t.baseline_mb:.1f}MB  "
              f"peak={t.peak_mb:.1f}MB  "
              f"delta=+{t.delta_mb:.1f}MB  "
              f"end={t.end_mb:.1f}MB")
        return result
    return wrapper


# ============================================================
# 音声ファイルを WAV に変換（pyannote / API 送信用）
# ============================================================

def to_wav(audio_path: str, max_duration_s: int = 660) -> str:
    """
    任意の音声をモノラル16kHz WAVに変換して一時ファイルとして返す。
    max_duration_s を超える場合は先頭を切り出す。
    """
    audio = AudioSegment.from_file(audio_path)
    audio = audio.set_channels(1).set_frame_rate(16000)
    if len(audio) > max_duration_s * 1000:
        audio = audio[: max_duration_s * 1000]
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".wav")
    audio.export(tmp.name, format="wav")
    tmp.close()
    return tmp.name


# ============================================================
# 方式A: pyannote.audio による話者分離
# ============================================================

def run_method_a(wav_path: str, hf_token: str) -> dict:
    """
    pyannote.audio パイプラインを使って話者分離を実行し、
    メモリ使用量・処理時間・話者数・発話区間数を返す。

    必要環境:
      pip install pyannote-audio torch torchaudio
      HuggingFaceでモデルの利用申請が完了していること
    """
    # インポートはここで行う（未インストール環境でのエラーを遅延させる）
    try:
        from pyannote.audio import Pipeline
    except ImportError:
        raise RuntimeError(
            "pyannote.audio がインストールされていません。\n"
            "  pip install pyannote-audio torch torchaudio\n"
            "または --skip-pyannote を指定してください。"
        )

    print("  [方式A] Pipelineをロード中（これが最もメモリを消費します）...")
    with PeakMemoryTracker() as tracker:
        t_start = time.perf_counter()

        pipeline = Pipeline.from_pretrained(
            "pyannote/speaker-diarization-3.1",
            token=hf_token
        )
        print(f"  [方式A] Pipeline ロード完了  現在RSS: {tracker.peak_mb:.0f}MB")

        diarization = pipeline(wav_path)

        t_elapsed = time.perf_counter() - t_start

    # 結果を集計
    speaker_segments: dict[str, int] = {}
    for turn, _, speaker in diarization.itertracks(yield_label=True):
        speaker_segments[speaker] = speaker_segments.get(speaker, 0) + 1

    return {
        "method":          "A_pyannote",
        "baseline_mb":     round(tracker.baseline_mb, 1),
        "peak_mb":         round(tracker.peak_mb, 1),
        "delta_mb":        round(tracker.delta_mb, 1),
        "end_mb":          round(tracker.end_mb, 1),
        "elapsed_s":       round(t_elapsed, 2),
        "num_speakers":    len(speaker_segments),
        "speaker_segments": speaker_segments,
        "error":           None,
    }


# ============================================================
# 方式B: gpt-4o-transcribe-diarize (httpx 直接呼び出し)
# ============================================================

def run_method_b(wav_path: str, api_key: str) -> dict:
    """
    OpenAI gpt-4o-transcribe-diarize で話者分離付き文字起こしを実行し、
    メモリ使用量・処理時間・話者数・発話区間数を返す。

    httpx で直接 /v1/audio/transcriptions を呼び出すため、
    openai SDK のバージョンに依存しない。

    レスポンス形式（diarized_json）:
      response["segments"] に speaker, text, start, end が含まれる。
    """
    print("  [方式B] API 呼び出し中（gpt-4o-transcribe-diarize）...")

    with PeakMemoryTracker() as tracker:
        t_start = time.perf_counter()

        with open(wav_path, "rb") as f:
            audio_bytes = f.read()

        file_size_mb = len(audio_bytes) / 1024 / 1024
        print(f"  [方式B] ファイルサイズ: {file_size_mb:.1f}MB")

        # 5分を超える場合はチャンク分割して送る
        audio = AudioSegment.from_wav(wav_path)
        duration_ms = len(audio)
        chunk_ms    = 5 * 60 * 1000   # 5分

        all_segments: list[dict] = []
        all_text_parts: list[str] = []
        chunk_count = math.ceil(duration_ms / chunk_ms)

        for chunk_idx in range(chunk_count):
            chunk_start = chunk_idx * chunk_ms
            chunk_end   = min((chunk_idx + 1) * chunk_ms, duration_ms)
            chunk       = audio[chunk_start:chunk_end]

            tmp_chunk = tempfile.NamedTemporaryFile(delete=False, suffix=".wav")
            chunk.export(tmp_chunk.name, format="wav")
            tmp_chunk.close()

            try:
                with open(tmp_chunk.name, "rb") as cf:
                    chunk_bytes = cf.read()

                print(f"  [方式B] チャンク {chunk_idx+1}/{chunk_count} 送信中"
                      f" ({len(chunk_bytes)/1024:.0f}KB)...")

                resp = httpx.post(
                    "https://api.openai.com/v1/audio/transcriptions",
                    headers={"Authorization": f"Bearer {api_key}"},
                    files={
                        "file": (
                            f"chunk_{chunk_idx}.wav",
                            chunk_bytes,
                            "audio/wav",
                        )
                    },
                    data={
                        "model":              "gpt-4o-transcribe-diarize",
                        "response_format":    "diarized_json",
                        "chunking_strategy":  "auto",
                        "language":           "ja",
                    },
                    timeout=300.0,
                )

                if resp.status_code != 200:
                    raise RuntimeError(
                        f"gpt-4o-transcribe-diarize API エラー "
                        f"(チャンク {chunk_idx+1}/{chunk_count}): "
                        f"HTTP {resp.status_code} - {resp.text[:300]}"
                    )

                result = resp.json()
                all_text_parts.append(result.get("text", ""))
                segs = result.get("segments", [])
                # チャンクオフセット（秒）をセグメントの start/end に加算
                offset_s = chunk_start / 1000
                for seg in segs:
                    seg_copy = dict(seg)
                    seg_copy["start"] = seg_copy.get("start", 0) + offset_s
                    seg_copy["end"]   = seg_copy.get("end",   0) + offset_s
                    all_segments.append(seg_copy)

            finally:
                try:
                    os.remove(tmp_chunk.name)
                except OSError:
                    pass

        t_elapsed = time.perf_counter() - t_start

    # 話者ラベルを集計（gpt-4o-transcribe-diarize が対応している場合）
    speaker_segments: dict[str, int] = {}
    for seg in all_segments:
        spk = seg.get("speaker") or seg.get("speaker_label")
        if spk:
            speaker_segments[spk] = speaker_segments.get(spk, 0) + 1

    if not speaker_segments and all_segments:
        speaker_segments = {"(話者分離なし)": len(all_segments)}

    return {
        "method":           "B_gpt4o_diarize",
        "baseline_mb":      round(tracker.baseline_mb, 1),
        "peak_mb":          round(tracker.peak_mb, 1),
        "delta_mb":         round(tracker.delta_mb, 1),
        "end_mb":           round(tracker.end_mb, 1),
        "elapsed_s":        round(t_elapsed, 2),
        "num_speakers":     len(speaker_segments),
        "speaker_segments": speaker_segments,
        "total_text_len":   sum(len(t) for t in all_text_parts),
        "error":            None,
    }


# ============================================================
# 結果出力
# ============================================================

def format_table(results: list[dict]) -> str:
    """比較表をテキスト形式で整形する。"""
    lines = [
        "=" * 60,
        "  GraRec メモリ使用量比較結果",
        "=" * 60,
        f"  測定日時: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
    ]

    headers = ["項目", "方式A (pyannote)", "方式B (gpt-4o-diarize)"]
    rows_a = next((r for r in results if "A_" in r.get("method", "")), {})
    rows_b = next((r for r in results if "B_" in r.get("method", "")), {})

    def val(d, key):
        if not d:
            return "未測定"
        if d.get("error"):
            return f"ERROR: {str(d['error'])[:30]}"
        v = d.get(key)
        return str(v) if v is not None else "-"

    table = [
        ("ベースラインRSS (MB)",   val(rows_a, "baseline_mb"),    val(rows_b, "baseline_mb")),
        ("ピークRSS (MB)",         val(rows_a, "peak_mb"),         val(rows_b, "peak_mb")),
        ("増加量 ΔRSS (MB)",       val(rows_a, "delta_mb"),        val(rows_b, "delta_mb")),
        ("処理後RSS (MB)",         val(rows_a, "end_mb"),          val(rows_b, "end_mb")),
        ("処理時間 (秒)",          val(rows_a, "elapsed_s"),       val(rows_b, "elapsed_s")),
        ("検出話者数",             val(rows_a, "num_speakers"),    val(rows_b, "num_speakers")),
    ]

    col_w = [24, 18, 22]
    sep = "  " + "-" * (col_w[0] + col_w[1] + col_w[2] + 6)
    lines.append("  " + "  ".join(h.ljust(w) for h, w in zip(headers, col_w)))
    lines.append(sep)
    for row in table:
        lines.append("  " + "  ".join(v.ljust(w) for v, w in zip(row, col_w)))

    lines.append("")
    lines.append("【話者別発話区間数】")
    for r in results:
        if not r or r.get("error"):
            continue
        lines.append(f"  {r['method']}:")
        for spk, cnt in sorted(r.get("speaker_segments", {}).items()):
            lines.append(f"    {spk}: {cnt}区間")

    lines.append("")
    lines.append("=" * 60)
    return "\n".join(lines)


def save_results(results: list[dict], output_dir: str):
    os.makedirs(output_dir, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    # テキストサマリー
    txt_path = os.path.join(output_dir, f"memory_comparison_result_{ts}.txt")
    summary  = format_table(results)
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write(summary)
        f.write("\n\n【生データ (JSON)】\n")
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\n結果を保存: {txt_path}")

    # CSV
    csv_path = os.path.join(output_dir, f"memory_comparison_result_{ts}.csv")
    csv_fields = ["method", "baseline_mb", "peak_mb", "delta_mb",
                  "end_mb", "elapsed_s", "num_speakers", "error"]
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=csv_fields, extrasaction="ignore")
        w.writeheader()
        for r in results:
            w.writerow(r)
    print(f"CSV保存: {csv_path}")

    return txt_path


# ============================================================
# メイン
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="メモリ使用量比較スクリプト")
    parser.add_argument("audio",          help="測定に使用する音声ファイルパス")
    parser.add_argument("--skip-pyannote", action="store_true", help="方式Aをスキップ")
    parser.add_argument("--skip-diarize",  action="store_true", help="方式Bをスキップ")
    parser.add_argument("--hf-token",     default="", help="HuggingFaceトークン")
    parser.add_argument("--output",       default=".", help="結果出力ディレクトリ")
    args = parser.parse_args()

    audio_path = args.audio
    if not os.path.exists(audio_path):
        sys.exit(f"ERROR: ファイルが見つかりません: {audio_path}")

    hf_token  = args.hf_token or os.getenv("HF_TOKEN") or os.getenv("HUGGINGFACE_TOKEN", "")
    api_key   = OPENAI_API_KEY
    if not api_key:
        sys.exit("ERROR: OPENAI_API_KEY が設定されていません。")

    print("=" * 60)
    print("  GraRec メモリ使用量比較")
    print("=" * 60)
    print(f"  音声ファイル: {audio_path}")
    print(f"  出力先: {os.path.abspath(args.output)}")
    print()

    # 共通の WAV ファイルに変換（最大11分）
    print("共通WAVに変換中...")
    wav_path = to_wav(audio_path, max_duration_s=660)
    wav_size  = os.path.getsize(wav_path) / 1024 / 1024
    wav_dur   = len(AudioSegment.from_wav(wav_path)) / 1000
    print(f"  → {wav_path}  ({wav_size:.1f}MB, {wav_dur:.0f}秒)\n")

    results = []

    # ---- 方式A ----
    if not args.skip_pyannote:
        print("【方式A: pyannote.audio】")
        if not hf_token:
            print("  ⚠ HF_TOKEN が未設定のためスキップします。")
            print("    --hf-token TOKEN または環境変数 HF_TOKEN を設定してください。")
            results.append({
                "method": "A_pyannote",
                "error": "HF_TOKEN 未設定",
                "baseline_mb": None, "peak_mb": None, "delta_mb": None,
                "end_mb": None, "elapsed_s": None, "num_speakers": None,
                "speaker_segments": {},
            })
        else:
            try:
                r = run_method_a(wav_path, hf_token)
                results.append(r)
                print(f"  完了: peak={r['peak_mb']}MB  delta=+{r['delta_mb']}MB"
                      f"  時間={r['elapsed_s']}秒  話者数={r['num_speakers']}")
            except Exception as e:
                print(f"  ERROR: {e}")
                traceback.print_exc()
                results.append({
                    "method": "A_pyannote",
                    "error": str(e),
                    "baseline_mb": None, "peak_mb": None, "delta_mb": None,
                    "end_mb": None, "elapsed_s": None, "num_speakers": None,
                    "speaker_segments": {},
                })
    else:
        print("【方式A: スキップ】")

    print()

    # ---- 方式B ----
    if not args.skip_diarize:
        print("【方式B: gpt-4o-transcribe-diarize】")
        try:
            r = run_method_b(wav_path, api_key)
            results.append(r)
            print(f"  完了: peak={r['peak_mb']}MB  delta=+{r['delta_mb']}MB"
                  f"  時間={r['elapsed_s']}秒  話者数={r['num_speakers']}")
        except Exception as e:
            print(f"  ERROR: {e}")
            traceback.print_exc()
            results.append({
                "method": "B_gpt4o_diarize",
                "error": str(e),
                "baseline_mb": None, "peak_mb": None, "delta_mb": None,
                "end_mb": None, "elapsed_s": None, "num_speakers": None,
                "speaker_segments": {},
            })
    else:
        print("【方式B: スキップ】")

    print()

    # 一時 WAV 削除
    try:
        os.remove(wav_path)
    except OSError:
        pass

    # 結果出力
    summary = format_table(results)
    print(summary)
    save_results(results, args.output)


if __name__ == "__main__":
    main()
