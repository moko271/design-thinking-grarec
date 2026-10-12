# -*- coding: utf-8 -*-
"""
本物のAPIにつなぐ前の点検スクリプト。grarec_light フォルダで実行する:
    python tools/check_setup.py
"""
import os
import shutil
import sys

OK, NG = "✅", "❌"
problems = 0


def check(label, ok, hint=""):
    global problems
    print(f"{OK if ok else NG} {label}" + ("" if ok else f"\n   → {hint}"))
    if not ok:
        problems += 1


# 1) Python パッケージ
for mod, pipname in [("flask", "flask==3.0.0"), ("openai", "openai==1.12.0"), ("dotenv", "python-dotenv"),
                     ("httpx", "httpx==0.27.2"), ("pydub", "pydub"), ("pptx", "python-pptx")]:
    try:
        __import__(mod)
        check(f"パッケージ {pipname}", True)
    except ImportError:
        check(f"パッケージ {pipname}", False, f"pip install {pipname}")

# 2) ffmpeg / ffprobe（声の登録・話者分離で音声を変換するのに使う）
check("ffmpeg", bool(shutil.which("ffmpeg")), "brew install ffmpeg")
check("ffprobe", bool(shutil.which("ffprobe")), "brew install ffmpeg")

# 3) APIキー
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass
key = os.getenv("OPENAI_API_KEY", "")
check("OPENAI_API_KEY（.env）", key.startswith("sk-"), ".env に OPENAI_API_KEY=sk-... を書く")
if not key:
    sys.exit(1)

# 4) モデルが使えるか（このキーのプロジェクトで許可されているか）
try:
    from openai import OpenAI
    client = OpenAI(api_key=key)
    card_model = os.getenv("CARD_MODEL", "gpt-5.4-mini")
    for m in [card_model, "gpt-4o-mini", "gpt-4o-transcribe-diarize", "whisper-1"]:
        try:
            client.models.retrieve(m)
            check(f"モデル {m} が使える", True)
        except Exception as e:
            check(f"モデル {m} が使える", False,
                  f"{str(e)[:120]}\n     OpenAIの管理画面 → Settings → Project → Limits（Model access）で許可されているか確認")
    # 5) カード選びのモデルに実際に1回聞いてみる（ごく短い・数円未満）
    try:
        res = client.chat.completions.create(
            model=card_model,
            messages=[{"role": "user", "content": 'JSONで {"ok": true} とだけ返して'}],
            response_format={"type": "json_object"},
            extra_body={"reasoning_effort": "low"},
        )
        check(f"{card_model} に質問して返事が来る", "ok" in (res.choices[0].message.content or ""))
    except Exception as e:
        check(f"{card_model} に質問して返事が来る", False,
              f"{str(e)[:160]}\n     → このままでも gpt-4o-mini に自動で切り替わるので動きます。エラー文を送ってください")
except Exception as e:
    check("OpenAI に接続", False, str(e)[:200])

print("\n" + ("すべてOK。PORT=8080 python app.py で起動して http://localhost:8080/kadai を開いてください"
              if problems == 0 else f"{problems} 件の問題があります。上の → の手順を試してください"))
