# -*- coding: utf-8 -*-
"""シミュレーション共通部品（標準ライブラリのみ）"""
import math, os, re, unicodedata

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TRANSCRIPTS = {g: os.path.join(ROOT, "grarec_transcribe_tool", "全班_文字起こし結果", f"班{g}_文字起こし_時系列.txt")
               for g in (1, 2, 4, 5, 6, 31)}
TRANSCRIPTS[32] = os.path.join(ROOT, "grarec_transcribe_tool", "班32_文字起こし結果_v2", "班32_文字起こし_時系列.txt")
GROUPS = [1, 2, 4, 5, 6, 31, 32]

LINE = re.compile(r"^\[(\d\d):(\d\d):(\d\d)\]\s*(.+?)\s*(⚠️.*)?$")


def load_transcript(group):
    """[(秒, テキスト)]。秒は録音開始（最初の行）からの経過秒。"""
    rows = []
    for line in open(TRANSCRIPTS[group], encoding="utf-8"):
        m = LINE.match(line.strip())
        if m:
            rows.append((int(m[1]) * 3600 + int(m[2]) * 60 + int(m[3]), m[4]))
    t0 = rows[0][0] if rows else 0
    return [(t - t0, x) for t, x in rows]


def norm(s):
    return re.sub(r"[\s、。！？!?「」『』・]", "", unicodedata.normalize("NFKC", s or "")).lower()


def similar(a, b):
    """kadai.html の similar() と同じ判定"""
    x, y = norm(a), norm(b)
    if not x or not y:
        return False
    if x == y:
        return True
    return min(len(x), len(y)) >= 5 and (x in y or y in x)


def entropy_balance(xs):
    """正規化エントロピー（1=均等）。合計0なら None"""
    s = sum(xs); n = len(xs)
    if n < 2 or s <= 0:
        return None
    h = -sum((x / s) * math.log(x / s) for x in xs if x > 0)
    return h / math.log(n)


def gini_norm(xs):
    """正規化ジニ係数（0=均等, 1=1人だけ）。概要の定義と同じ。n<2 または合計0なら None"""
    n = len(xs); s = sum(xs)
    if n < 2 or s <= 0:
        return None
    mean = s / n
    g = sum(abs(a - b) for a in xs for b in xs) / (2 * n * n * mean)
    return g * n / (n - 1)


def schedule(plan, end_sec):
    """カードを出す時刻（秒）の列。plan: '3-10' / '3' / '5' / '10'"""
    ts, t, i = [], 0, 0
    while True:
        step = (3 if i == 0 else 10) if plan == "3-10" else int(plan)
        t += step * 60
        if t > end_sec + 60:
            break
        ts.append(t); i += 1
    return ts
