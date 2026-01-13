import os
import tempfile
import json

from flask import Flask, request, jsonify, send_from_directory
from dotenv import load_dotenv
from openai import OpenAI

# =========================
# 環境変数・クライアント準備
# =========================
load_dotenv()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

if not OPENAI_API_KEY:
    raise RuntimeError("OPENAI_API_KEY が .env に設定されていません。")

client = OpenAI(api_key=OPENAI_API_KEY)

# =========================
# フェーズごとの設定
# =========================
PHASE_CONFIG = {
    # ①さぐる：現状をさぐる
    "saguru": {
        "label": "①さぐる：現状をさぐる",
        "card_type": "事実・観察・感情の「今こうなっている」カード",
        "count_min": 6,
        "count_max": 10,
        "examples": [
            "毎日カバンが重くて、肩が痛くなることが多い。",
            "授業で使わなかった教科書を、念のため持ってきている。",
            "教室が暑すぎたり寒すぎたりして集中しにくい。"
        ],
        "aim": (
            "観察欄・困りごと欄を補完する材料として、状況カードを多めに出す。"
            "評価や解決策は抑えめにし、事実と感じたことを見える化する。"
        ),
    },
    # ②きづく：本当の問題に気づく
    "kizuku": {
        "label": "②きづく：本当の問題に気づく",
        "card_type": "解決したい『問題の核心』や『問い』を表すカード",
        "count_min": 3,
        "count_max": 5,
        "examples": [
            "荷物が重くなる理由をきちんと整理できていないことが問題だ。",
            "生徒が本当に必要な持ち物を自分で判断できていない。"
        ],
        "aim": (
            "さぐるフェーズのカードを踏まえて、AIが問題候補をまとめ直す。"
            "生徒は『一番気になる問題』を選んでシートに一文で書く。"
            "カードは問いの文にして、次のひらめく・つくるの起点にする。"
        ),
    },
    # ③ひらめく：解決アイデアをひらめく
    "hirameku": {
        "label": "③ひらめく：解決アイデアをひらめく",
        "card_type": "解決アイデアや『こんな工夫をしてみたい』カード",
        "count_min": 8,
        "count_max": 12,
        "examples": [
            "曜日ごとに持ち物チェックリストをつくる。",
            "ロッカーに置いておける教科書を学年で決める。",
            "カバンの重さを1週間計測して、結果をポスターにまとめる。"
        ],
        "aim": (
            "アイデア出し欄を広げる役割。似ていても視点が違えば残す。"
            "評価はまだせず、生徒が『面白い・実現しやすい』観点で選べるようにする。"
        ),
    },
    # ④つくる：ペーパープロトタイプにまとめる
    "tsukuru": {
        "label": "④つくる：ペーパープロトタイプにまとめる",
        "card_type": "実際に形にするアイデアの要約カード＋具体化カード",
        "count_min": 3,
        "count_max": 7,
        "examples": [
            "休み時間に自分の空間を作れる『安眠ムードBOX』をつくる。",
            "机の上に置ける大きさにする。",
            "箱の中は暗くして、音もできるだけ遮る。",
            "軽くて持ち運びしやすい素材にする。"
        ],
        "aim": (
            "ワークシートの『見た目／大きさ／機能／特徴』と対応させる。"
            "AIは要約と具体化の候補を出し、生徒はそれをヒントに自分の言葉と絵で書く。"
        ),
    },
    # ⑤ためす：ユーザーテストとフィードバック
    "tamesu": {
        "label": "⑤ためす：ユーザーテストとフィードバック",
        "card_type": "良かったところ／もっと良くできそうなところ／次に取り組むこと",
        "count_min": 4,
        "count_max": 6,
        "examples": [
            "手軽に自分の空間ができるのが良さそうと言われた。",
            "BOXが大きくて置き場所に困るので、畳めるようにした方が良いと言われた。",
            "板を小さくして折り畳みしやすい形を考える。"
        ],
        "aim": (
            "フィードバックシートの3欄（良かった・もっと・次）を埋める材料。"
            "次のサイクルのさぐる・きづくに引き継ぐ学びのエッセンスとして少数精鋭にする。"
        ),
    },
}

# =========================
# Flask アプリ
# =========================
# Railway / Flask の慣習に合わせて templates は templates/ に置く場合は template_folder 引数を変更
app = Flask(__name__, static_folder="static", template_folder="static")


# ---------- ページ ----------
@app.route("/")
def index():
    # 授業で使うメイン画面（静的フォルダの index_ai.html）
    return send_from_directory("static", "index_ai.html")


@app.route("/ai")
def index_ai():
    # 必要なら別URLでも同じページを返す
    return send_from_directory("static", "index_ai.html")


# =========================
# プロンプト生成（keyword + quote 用）
# =========================
def build_prompt(memo: str, phase: str) -> str:
    """
    フェーズ設定とメモから、ChatGPT に渡す日本語プロンプトを組み立てる。
    keyword + quote の JSON 配列を返すように指示する。
    """
    cfg = PHASE_CONFIG.get(phase)
    if cfg is None:
        cfg = PHASE_CONFIG["saguru"]

    label = cfg["label"]
    card_type = cfg["card_type"]
    cmin = cfg["count_min"]
    cmax = cfg["count_max"]
    examples = cfg["examples"]
    aim = cfg["aim"]

    examples_text = ""
    for ex in examples:
        examples_text += f"・{ex}\n"

    prompt = (
        f"あなたは日本の中高生の話し合いを支援するファシリテーターです。\n"
        f"今はデザイン思考のフェーズ「{label}」にいます。\n\n"
        f"生徒たちの話し合いメモ（文字起こしを含む）から、"
        f"このフェーズにふさわしい内容のフレーズカードを {cmin}〜{cmax} 個、日本語で生成してください。\n"
        f"今回ほしいカードの種類：{card_type}\n\n"
        f"ねらい：{aim}\n\n"
        "【出力フォーマット】\n"
        "- 必ず JSON 配列だけを出力する。\n"
        "- 各要素は {\"keyword\": \"短いキーワード\", \"quote\": \"元の発話の一部\"} の形にする。\n"
        "- keyword は 20文字前後の短い常体の文（〜する、〜になる など）。\n"
        "- quote は元の発話から15〜25文字程度をそのまま抜粋する。見つからなければ空文字にする。\n"
        "- 丁寧語（〜です、〜ます）は使わず、子どもが自分のノートに書きそうな表現にする。\n"
        "- 同じ意味の内容は1つにまとめる。\n"
        "- JSON 以外の説明文やコメントは一切出力しない。\n\n"
        "【カードのイメージ例】\n"
        f"{examples_text}\n"
        "【元のメモ】\n"
        f"{memo}\n"
    )

    return prompt


# =========================
# キーワード抽出 API
# =========================
@app.route("/api/extract_keywords", methods=["POST"])
def extract_keywords():
    """
    memo: 文字起こし＋メモ
    phase: "saguru" / "kizuku" / "hirameku" / "tsukuru" / "tamesu"
    """
    data = request.get_json()
    if not data or "memo" not in data:
        return jsonify({"ok": False, "error": "no memo field"}), 400

    memo = data["memo"]
    phase = data.get("phase", "saguru")

    prompt = build_prompt(memo, phase)

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": "あなたは日本語の話し合いから、授業で使いやすいフレーズカードをJSON形式で作るアシスタントです。",
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.4,
        )
    except Exception as e:
        return jsonify({"ok": False, "error": f"OpenAI API error: {e}"}), 500

    raw_output = response.choices[0].message.content or ""

    keywords = []
    # ```json ... ``` で囲まれている場合に対応
    text = raw_output.strip()
    try:
        if text.startswith("```"):
            # 最初の ```xxx を飛ばす
            text = text.split("\n", 1)[1]
            # 最後の ``` を削る
            text = text.rsplit("```", 1)[0]
        parsed = json.loads(text)
        # 想定外の形式でも最低限整える
        if isinstance(parsed, list):
            for item in parsed:
                if isinstance(item, dict):
                    kw = item.get("keyword", "").strip()
                    qt = item.get("quote", "").strip()
                    if kw:
                        keywords.append({"keyword": kw, "quote": qt})
                elif isinstance(item, str):
                    keywords.append({"keyword": item.strip(), "quote": ""})
        else:
            # オブジェクト単体で返ってきた場合も一応ラップ
            keywords.append(
                {
                    "keyword": str(parsed).strip(),
                    "quote": ""
                }
            )
    except json.JSONDecodeError:
        # JSON パース失敗時は、従来の行単位分割でフォールバック
        for line in raw_output.splitlines():
            line = line.strip()
            if not line:
                continue
            line = line.lstrip("・-*").strip()
            if "." in line[:3]:
                line = line.split(".", 1)[1].strip()
            if line:
                keywords.append({"keyword": line, "quote": ""})

    return jsonify({"ok": True, "keywords": keywords, "phase": phase})


# =========================
# 音声 → テキスト API（OpenAI Whisper）
# =========================
@app.route("/api/transcribe", methods=["POST"])
def transcribe():
    """
    フロントから送られてきた音声ファイル（audio）をOpenAI Whisperで文字起こしして返す。
    m4a / wav / mp3 / webm などを想定。
    """
    if "audio" not in request.files:
        return jsonify({"ok": False, "error": "no audio file"}), 400

    audio_file = request.files["audio"]
    if audio_file.filename == "":
        return jsonify({"ok": False, "error": "empty filename"}), 400

    # 一時ファイルに保存
    with tempfile.NamedTemporaryFile(
        delete=False,
        suffix=os.path.splitext(audio_file.filename)[1] or ".webm"
    ) as tmp:
        tmp_path = tmp.name
        audio_file.save(tmp_path)

    try:
        with open(tmp_path, "rb") as f:
            response = client.audio.transcriptions.create(
                model="whisper-1",
                file=f,
                language="ja"
            )
        text = (response.text or "").strip()
    except Exception as e:
        return jsonify({"ok": False, "error": f"transcribe error: {e}"}), 500
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass

    return jsonify({"ok": True, "text": text})


# =========================
# エントリポイント
# =========================
if __name__ == "__main__":
    # Railway では PORT 環境変数が渡されるので、それがあれば使う
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
