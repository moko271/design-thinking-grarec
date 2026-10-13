import os
import io
import shutil
import tempfile
import json
import base64
import re
import math
import zipfile
import threading
import subprocess
import traceback
from datetime import datetime
from collections import defaultdict
from functools import wraps
from urllib.parse import quote as urlquote
from pydub import AudioSegment

from flask import Flask, request, jsonify, send_from_directory, Response
from dotenv import load_dotenv
from openai import OpenAI
# pyannote / torch / soundfile は話者分離除外に伴い不使用
# （後日オフライン処理用に requirements.txt は維持）

# =========================
# 環境変数・クライアント準備
# =========================
load_dotenv()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

if not OPENAI_API_KEY:
    raise RuntimeError("OPENAI_API_KEY が .env に設定されていません。")

# =========================
# データ永続化ディレクトリ
# =========================
DATA_DIR       = os.getenv("DATA_DIR", "data")
SESSIONS_DIR   = os.getenv("SESSIONS_DIR",   os.path.join(DATA_DIR, "sessions"))
RECORDINGS_DIR = os.getenv("RECORDINGS_DIR", os.path.join(DATA_DIR, "recordings"))
PROJECTS_DIR   = os.getenv("PROJECTS_DIR",   os.path.join(DATA_DIR, "projects"))
TIMELAPSE_DIR  = os.getenv("TIMELAPSE_DIR",  os.path.join(DATA_DIR, "timelapse"))

for _d in (SESSIONS_DIR, RECORDINGS_DIR, PROJECTS_DIR, TIMELAPSE_DIR):
    try:
        os.makedirs(_d, exist_ok=True)
    except OSError as _e:
        print(f"Warning: ディレクトリ作成失敗 {_d}: {_e}")

# =========================
# 管理画面 Basic 認証
# =========================
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")

def require_admin(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        # 環境変数未設定時は認証をスキップ（開発環境用）
        if not ADMIN_PASSWORD:
            print("Warning: ADMIN_PASSWORD 未設定。管理画面は認証なしで公開されています。")
            return f(*args, **kwargs)
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Basic "):
            try:
                creds = base64.b64decode(auth[6:]).decode("utf-8")
                user, pw = creds.split(":", 1)
                if user == ADMIN_USERNAME and pw == ADMIN_PASSWORD:
                    return f(*args, **kwargs)
            except Exception:
                pass
        return Response(
            "Authentication required",
            401,
            {"WWW-Authenticate": 'Basic realm="GraRec Admin"'},
        )
    return decorated

client = OpenAI(api_key=OPENAI_API_KEY)

# Whisper 文字起こし精度向上のための語彙ヒント
# prompt パラメータとして渡すことで専門用語の誤認識を抑制する
WHISPER_HINT = (
    "量子力学、シュレーディンガーの猫、重ね合わせ、観測、波動関数、"
    "不確定性原理、量子コンピュータ、量子もつれ、デコヒーレンス、"
    "量子テレポーテーション、スピン、エンタングルメント、"
    "ペルソナ、インサイト、デザイン思考、プロトタイプ、ユーザー体験、"
    "GraRec、ワークシート、キーワードカード"
)

# 課題シート（/kadai）など、テーマを固定しない画面用の軽いヒント。
# 専門用語を並べるとWhisperがそれを幻覚として出力することがあるため、最小限にする。
GENERIC_WHISPER_HINT = "大学の授業でのグループワークの話し合いです。"


def whisper_hint_for(mode: str) -> str:
    return GENERIC_WHISPER_HINT if mode == "generic" else WHISPER_HINT


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


@app.route("/review")
def review():
    return send_from_directory("static", "review.html")


@app.route("/admin")
@require_admin
def admin():
    return send_from_directory("static", "admin.html")


@app.route("/quantum")
def quantum():
    return send_from_directory("static", "index_ai.html")


@app.route("/mendan")
def mendan():
    return send_from_directory("static", "index_ai.html")


@app.route("/design")
def design_sheet():
    # デザイン思考ワークシート（①さぐる ②きづく ③ひらめく）。画面は課題シートと同じ
    return send_from_directory("static", "kadai.html")


@app.route("/kadai")
def kadai():
    # 授業の課題フォーマット（A4横・自由記述）に合わせた課題シート画面
    return send_from_directory("static", "kadai.html")


# =========================
# プロンプト生成（keyword + quote 用）
# =========================
def build_prompt(memo: str, phase: str, prev_text: str = "", template: str = "", long_segment: bool = False) -> tuple[str, str]:
    """
    メモとフェーズから、グループ学習発話の構造化抽出プロンプトを組み立てる。
    前文脈（prev_text）を考慮して、system_prompt と user_prompt を返す。
    """
    system_prompt = (
        "あなたは教育対話の分析専門家です。\n"
        "グループ学習の発話を分析し、\n"
        "Dialogue Act分類（DA分類）に基づいて\n"
        "すべての発言を構造化して抽出します。\n"
        "固有名詞（人名・地名・施設名・プロジェクト名）は"
        "必ず元の表記のまま抽出すること。"
        "抽象化・一般化しないこと。"
    )
    
    if long_segment:
        _extraction_rules = (
            "・量子力学の伝え方を考える企画立案において、\n"
            "  話し合いの本質的な進展（アイデア・気づき・意思決定）に\n"
            "  直接関わる重要な発言を優先的に選んで抽出する\n"
            "・相槌・繰り返し・些細な確認・作業的やり取りは積極的に除外する\n"
            "・枚数は内容の重要度で判断し、5〜12件程度を目安にする\n"
            "  （この期間に重要な発言が少なければ少なくてよい）\n"
            "・前の文脈を考慮して意図を判断する\n"
            "・話者が変わるタイミングも意識して分類する\n\n"
        )
    else:
        _extraction_rules = (
            "・必ず最低10件、できれば15〜20件抽出する\n"
            "・短いテキストでも1文ごとに1件を目安に抽出する\n"
            "・些細に見えても話し合いの記録として価値ある発言は全て含める\n"
            "・前の文脈を考慮して意図を判断する\n"
            "・話者が変わるタイミングも意識して分類する\n\n"
        )

    user_prompt = (
        "以下は中高生のグループ学習の発話記録です。\n"
        "すべての重要な発言を抽出し、各発言について\n"
        "以下の5フィールドを埋めてください。\n\n"
        "【発話記録】\n"
        "前の文脈（直前の発話）:\n"
        f"{prev_text}\n\n"
        "今回の発話テキスト:\n"
        f"{memo}\n\n"
        f"現在のフェーズ: {phase}\n\n"
        f"【抽出ルール】\n{_extraction_rules}"
        "【抽出しない発言】\n"
        "・雑談・世間話（例：お腹すいた・休憩しよう・今日暑いね 等）\n"
        "・単純な相槌のみの発言（例：そうだね・なるほど・うん・はい 等）\n"
        "・役割確認・作業分担のみの発言（例：じゃあAがやる・私が書く 等）\n"
        "・笑いや感嘆のみ（例：笑・えー・あはは・すごーい 等）\n"
        "・GraRecというシステム自体の操作・UI操作・システムへの感想・評価に関する発言\n"
        "  （例：押してみて／消してみて／バチッと消せる？／カードが出た／\n"
        "         重要なとこだけキーワードにしてくれてる／使いやすい／便利／すごい 等）\n"
        "  ※ 判定基準: 発言が量子力学の企画内容（ペルソナ・インサイト・伝え方の\n"
        "    アイデア等）に言及しているか確認する。GraRecという単語や、カードの\n"
        "    表示・操作・機能について言及している発言は、内容の重要度に関わらず除外する。\n"
        "    「このシステムはすごい」「使いやすい」「便利だね」のように\n"
        "    GraRec・ツール自体を主語または話題にした評価発言も除外する\n\n"
        "【typeの定義（発話の形式）】\n"
        "- fact：観察・現状・事実の報告\n"
        "- opinion：意見・考えの表明\n"
        "- question：疑問・問い\n"
        "- idea：アイデア・提案\n"
        "- reaction：共感・驚き・相槌\n"
        "- instruction：指示・依頼\n\n"
        "【thoughtの定義（発話の意図・状態）】\n"
        "- fact：起きていること・観察したこと\n"
        "- problem：困っていること・課題\n"
        "- interpret：なぜそうなるか・解釈\n"
        "- wish：こうしたい・こうあるべき\n\n"
        "【フェーズ別の抽出方針】\n"
        "- さぐる：ペルソナの人物像・現状イメージ・触れる場面を中心に抽出（thought=fact中心）\n"
        "- きづく：ペルソナのつまずき・深掘り発言・インサイト候補を中心に抽出（thought=problem/interpret中心）\n"
        "- ひらめく：type=ideaを中心に、解決アイデアを多めに抽出\n"
        "- つくる：具体的な機能・形状に関する発言を抽出\n"
        "- ためす：良かった点・改善点・次の一手を抽出\n"
        "- minutes（議事録）：\n"
        "  以下の4種類を必ず抽出する\n"
        "  ・議題・背景 → type=fact, thought=fact\n"
        "  ・決定事項 → type=fact, thought=wish（決定した内容）\n"
        "  ・アクションアイテム → type=instruction（誰が・何を・いつまで）\n"
        "  ・次回やること → type=instruction, thought=wish\n"
        "  ・保留・未解決 → type=question（今回決まらなかったこと）\n"
        "- interview（面談記録）：\n"
        "  以下を必ず抽出する\n"
        "  ・参加者名・役職・背景 → area-member（type=fact, thought=fact）\n"
        "    ※参加者名は「鈴木 咲絵さん」「鴫原 智宏さん」のように\n"
        "      実際の固有名詞をそのままkeywordに入れる。\n"
        "      「参加者の名前」「参加者A」のような抽象表現は絶対に使わない。\n"
        "      発話記録に参加者リストがある場合は各参加者を個別カードで抽出。\n"
        "      quoteには役職・所属・肩書きがあればそれを入れる。\n"
        "  ・3ヶ月の方針・目的 → area-next-goal（thought=wish）\n"
        "  ・直近の施策・アクション → area-event（type=instruction）\n"
        "  ・課題・論点 → area-trouble（thought=problem）\n"
        "  ・ネットワーク・出会いたい人 → area-connect（thought=wish）\n"
        "  ・コミュニケーターとして動けること → area-comm-action\n"
        "  ・決定事項・まとめ → area-next-goal\n\n"
        "必ず以下のJSON配列のみを返してください。\n"
        "説明文・```は不要です。\n\n"
    )

    is_interview = (phase == "interview" or template == "interview")
    if is_interview:
        user_prompt += (
            "[\n"
            "  {\n"
            "    \"keyword\": \"発言の核心を10〜20文字で。固有名詞（人名・施設名等）はそのまま表記\",\n"
            "    \"who\": \"人名・担当者（なければ空文字）\",\n"
            "    \"what\": \"行動・内容\",\n"
            "    \"when\": \"日時・期限（なければ空文字）\",\n"
            "    \"quote\": \"元の発言\",\n"
            "    \"type\": \"fact|opinion|question|idea|reaction|instruction\",\n"
            "    \"thought\": \"fact|problem|interpret|wish\",\n"
            "    \"area\": \"area-member|area-recent|area-next-goal|area-event|area-trouble|area-connect|area-comm-action|unplaced\"\n"
            "  }\n"
            "]\n\n"
            "【areaの選択基準（必ず1つだけ選ぶ。迷ったら unplaced）】\n"
            "area-member       : 参加者名・肩書き・日時・担当者\n"
            "area-recent       : 最近の出来事・先月の活動・現状報告\n"
            "area-next-goal    : 来月の目標・やりたいこと・方針・決定事項\n"
            "area-event        : 参加予定イベント・解決したい課題・直近アクション\n"
            "area-trouble      : 困っていること・悩み・課題・問題\n"
            "area-connect      : 会いたい人・繋がりたい人・紹介依頼\n"
            "area-comm-action  : コミュニケーターとして提供できること・支援内容\n"
            "unplaced          : 上記のいずれにも明確に当てはまらない場合\n"
        )
    else:
        user_prompt += (
            "[\n"
            "  {\n"
            "    \"keyword\": \"発言の内容そのものを10〜20文字で切り出す。『〜が〜する』という行動の説明にしない。話し合いの中で出てきた事実・気持ち・アイデア・問いをそのまま短くした言葉にする。良い例：朝の準備が毎日間に合わない／なぜ前の日に準備できないのか？ 悪い例（これにしない）：Bが困っていることをはなす／Cが頻度を確認する\",\n"
            "    \"who\": \"人名・担当者（なければ空文字）\",\n"
            "    \"what\": \"行動・内容\",\n"
            "    \"when\": \"日時・期限（なければ空文字）\",\n"
            "    \"quote\": \"元の発言\",\n"
            "    \"type\": \"fact|opinion|question|idea|reaction|instruction\",\n"
            "    \"thought\": \"fact|problem|interpret|wish\"\n"
            "  }\n"
            "]\n"
        )

    return system_prompt, user_prompt


# =========================
# キーワード抽出 API
# =========================
def calc_importance(kw_type, kw_thought, phase, template):
    phase_rules = {
        "saguru":   {"high": [("type","fact"),("type","question")],
                     "medium": [("type","opinion"),("thought","problem")],
                     "low":    [("type","idea"),("type","reaction")]},
        "kizuku":   {"high": [("thought","interpret"),("thought","problem")],
                     "medium": [("type","question"),("type","opinion")],
                     "low":    [("type","fact"),("type","reaction")]},
        "hirameku": {"high": [("type","idea"),("thought","wish")],
                     "medium": [("type","opinion"),("thought","interpret")],
                     "low":    [("type","fact"),("type","reaction")]},
        "tsukuru":  {"high": [("type","instruction"),("type","idea")],
                     "medium": [("thought","wish"),("thought","interpret")],
                     "low":    [("type","question"),("type","reaction")]},
        "tamesu":   {"high": [("thought","problem"),("thought","interpret")],
                     "medium": [("type","question"),("type","opinion")],
                     "low":    [("type","idea"),("type","reaction")]},
        "minutes":  {"high": [("type","instruction"),("type","question")],
                     "medium": [("type","fact"),("type","opinion")],
                     "low":    [("type","reaction"),("thought","fact")]},
        "interview":{"high": [("thought","wish"),("thought","problem")],
                     "medium": [("thought","interpret"),("type","question")],
                     "low":    [("type","fact"),("type","reaction")]},
    }
    rules = phase_rules.get(phase) or phase_rules.get(template) or {}
    for level in ("high", "medium", "low"):
        for field, val in rules.get(level, []):
            if field == "type"    and kw_type    == val: return level
            if field == "thought" and kw_thought == val: return level
    return "medium"


@app.route("/api/extract_keywords", methods=["POST"])
def extract_keywords():
    """
    memo: 文字起こし＋メモ
    phase: "saguru" / "kizuku" / "hirameku" / "tsukuru" / "tamesu"
    """
    import re
    
    data = request.get_json()
    if not data or "memo" not in data:
        return jsonify({"ok": False, "error": "no memo field"}), 400

    memo = data["memo"]
    phase = data.get("phase", "saguru")
    template = data.get("template", "")
    areas = data.get("areas") or None

    # memo 全体を「今回の発話」として扱い、直前の文脈はフロントから prev_text で受け取る。
    # （以前は memo の最後の5行を前文脈にしていたが、Whisperの結果は90秒で1行のため
    #   直近の発話が「前の文脈」扱いになり、3分サイクルでは「今回の発話」が空になっていた。
    #   simulate_offline.py の方式Bと同じ渡し方に揃える）
    memo_text = memo.strip()
    prev_text = (data.get("prev_text") or "").strip()[-500:] or "（なし）"

    long_segment = bool(data.get("long_segment", False))
    system_prompt, user_prompt = build_prompt(memo_text, phase, prev_text, template=template, long_segment=long_segment)

    if areas:
        area_desc = "\n".join(
            f"・{a['id']}（{a['label']}）" + (f"：{a['hint']}" if a.get("hint") else "")
            for a in areas
        )
        system_prompt += (
            f"\n\nこのワークシートには以下のエリアがあります：\n{area_desc}\n"
            "各キーワードの area フィールドに、最も適切なエリアID（完全一致）を入れてください。"
        )

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.4,
        )
    except Exception as e:
        return jsonify({"ok": False, "error": f"OpenAI API error: {e}"}), 500

    raw_output = response.choices[0].message.content or ""

    keywords = []
    # markdown code fence を削除
    text = raw_output.strip()
    if text.startswith("```"):
        text = re.sub(r"```[a-z]*\n?", "", text).strip().rstrip("```").strip()
    
    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            for item in parsed:
                if isinstance(item, dict):
                    kw = item.get("keyword", "").strip()
                    if kw:
                        keywords.append({
                            "keyword": kw,
                            "detail": item.get("detail", kw).strip(),
                            "quote": item.get("quote", "").strip(),
                            "type": item.get("type", "opinion").strip().lower(),
                            "thought": item.get("thought", "fact").strip().lower(),
                            "area": item.get("area", "").strip(),
                            "who": item.get("who", "").strip(),
                            "what": item.get("what", "").strip(),
                            "when": item.get("when", "").strip(),
                        })
    except json.JSONDecodeError as e:
        print(f"JSON parse error: {e}\nRaw: {raw_output[:200]}")

    for kw in keywords:
        kw["importance"] = calc_importance(
            kw.get("type", ""), kw.get("thought", ""), phase, template
        )

    return jsonify({"ok": True, "keywords": keywords, "phase": phase})


# =========================
# フェーズ提案 API
@app.route("/api/suggest_phase", methods=["POST"])
def suggest_phase():
    data = request.get_json()
    if not data or "theme" not in data:
        return jsonify({"ok": False, "error": "no theme field"}), 400

    theme = data["theme"].strip()
    if not theme:
        return jsonify({"ok": False, "error": "theme is empty"}), 400

    prompt = (
        f"以下のテーマについて、デザイン思考の\n"
        f"5フェーズ（さぐる・きづく・ひらめく・つくる・ためす）の\n"
        f"どれから始めるのが適切かを判断して、\n"
        f"以下のJSON形式のみで返してください：\n"
        f"{{\"phase\": \"saguru\", \"reason\": \"理由を一文で\"}}\n"
        f"テーマ：{theme}"
    )

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "あなたはデザイン思考ワークショップの適切な開始フェーズを判断するアシスタントです。"},
                {"role": "user", "content": prompt},
            ],
            temperature=0.4,
        )
    except Exception as e:
        return jsonify({"ok": False, "error": f"OpenAI API error: {e}"}), 500

    raw_output = response.choices[0].message.content or ""
    text = raw_output.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1]
        text = text.rsplit("```", 1)[0]

    phase = "saguru"
    reason = "AIの提案を取得できませんでした。"
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            phase = parsed.get("phase", phase)
            reason = parsed.get("reason", reason)
    except json.JSONDecodeError:
        pass

    return jsonify({"ok": True, "phase": phase, "reason": reason})


# =========================
# 音声 → テキスト API（OpenAI Whisper）
# =========================
def split_and_transcribe(file_path, suffix, hint=None):
    """5分超の音声を5分チャンクに分割してWhisperに送り、結果を結合する。"""
    hint = WHISPER_HINT if hint is None else hint
    fmt = suffix.lstrip('.').lower()
    if fmt == 'm4a':
        fmt = 'mp4'
    audio = AudioSegment.from_file(file_path, format=fmt)
    duration_ms = len(audio)
    chunk_ms = 5 * 60 * 1000  # 5分

    if duration_ms <= chunk_ms:
        with open(file_path, "rb") as f:
            response = client.audio.transcriptions.create(
                model="whisper-1",
                file=f,
                language="ja",
                prompt=hint
            )
        return response.text

    chunks = math.ceil(duration_ms / chunk_ms)
    texts = []
    for i in range(chunks):
        start = i * chunk_ms
        end = min((i + 1) * chunk_ms, duration_ms)
        chunk = audio[start:end]
        chunk_path = file_path + f"_chunk{i}.wav"
        chunk.export(chunk_path, format='wav')
        try:
            with open(chunk_path, "rb") as f:
                response = client.audio.transcriptions.create(
                    model="whisper-1",
                    file=f,
                    language="ja",
                    prompt=hint
                )
            texts.append(response.text)
        finally:
            try:
                os.remove(chunk_path)
            except OSError:
                pass

    return " ".join(texts)


@app.route("/api/transcribe", methods=["POST"])
def transcribe():
    """
    フロントから送られてきた音声ファイル（audio）をOpenAI Whisperで文字起こしして返す。
    5分超の音声は自動分割して順次送信し結果を結合する。
    """
    if "audio" not in request.files:
        return jsonify({"ok": False, "error": "no audio file"}), 400

    audio_file = request.files["audio"]
    if audio_file.filename == "":
        return jsonify({"ok": False, "error": "empty filename"}), 400

    suffix = os.path.splitext(audio_file.filename)[1] or ".webm"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp_path = tmp.name
        audio_file.save(tmp_path)

    try:
        text = (split_and_transcribe(tmp_path, suffix) or "").strip()
    except Exception as e:
        return jsonify({"ok": False, "error": f"transcribe error: {e}"}), 500
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass

    return jsonify({"ok": True, "text": text})


@app.route("/api/transcribe_with_speakers", methods=["POST"])
def transcribe_with_speakers():
    """
    音声ファイルをWhisperで文字起こしする（話者分離なし・軽量版）。
    録音ファイルは RECORDINGS_DIR に保存する（後日オフライン話者分離用）。
    """
    if "audio" not in request.files:
        return jsonify({"ok": False, "error": "no audio file"}), 400

    audio_file = request.files["audio"]
    suffix = os.path.splitext(audio_file.filename)[1] or ".webm"
    raw_sid = request.form.get("session_id", "")
    safe_sid = re.sub(r'[^\w\-]', '_', raw_sid, flags=re.UNICODE)[:40] if raw_sid else ""
    hint = whisper_hint_for(request.form.get("hint", ""))

    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp_path = tmp.name
        audio_file.save(tmp_path)

    try:
        text = (split_and_transcribe(tmp_path, suffix, hint=hint) or "").strip()

        return jsonify({
            "ok": True,
            "text": text,
            "segments": [],
            "speaker_summary": [],
            "num_speakers": 0
        })

    except Exception as e:
        print("transcribe_with_speakers error:", traceback.format_exc())
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        try:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            sid_part = f"_{safe_sid}" if safe_sid else ""
            rec_name = f"rec{sid_part}_{ts}{suffix}"
            dest = os.path.join(RECORDINGS_DIR, rec_name)
            shutil.copy2(tmp_path, dest)
            print(f"録音ファイル保存: {dest}")
        except Exception as _e:
            print(f"ERROR: 録音ファイル保存失敗: {_e} | RECORDINGS_DIR={RECORDINGS_DIR}")
        try:
            os.remove(tmp_path)
        except OSError:
            pass


# =========================
# 自動配置 API
# =========================
def build_layout_prompt(phase, cards, canvas_width, canvas_height, areas=None):
    system_prompt = "あなたはワークシートへのカード配置の専門家です。"

    # phaseに応じてarea_guideを切り替える
    if phase in ("saguru", "kizuku"):
        area_guide = """
【絶対ルール：saguru/kizukuフェーズで使えるエリアのみ選ぶ】
このワークシートは量子力学を伝える相手（ペルソナ）を妄想で設定し、
ペルソナへの伝え方を考えるデザイン思考活動です。

--- さぐるグループ（上段） ---

area-persona：
  伝える相手（ペルソナ）の人物像に関するカード
  例）「中学2年生、理科は好き」「数式が苦手」「宇宙や不思議な話が好き」

area-current-image：
  ペルソナが量子力学に対して持っていそうなイメージ・知識レベルのカード
  例）「聞いたことはあるけど難しそう」「なんか怖い実験みたい」

area-scene：
  ペルソナが量子力学に触れそうな場面・状況のカード
  例）「理科の授業で先生が言及」「SF映画で量子テレポーテーション」「ニュースで聞いた」

--- きづくグループ（下段） ---

area-confusion：
  ペルソナが量子力学のどの用語・概念につまずきそうかのカード
  例）「重ね合わせって何？」「シュレーディンガーの猫が意味不明」「波と粒って両方？」

area-why-but：
  「なぜなら」「でも・じつは」という形で深掘りした発言のカード
  例）「面白そうだけど、なぜなら映画で見たから」「じつは量子コンピュータは知っている」

area-insight：
  ペルソナの本当の困りごと・知りたいことを一行でまとめたカード
  例）「理科が苦手な女子中学生が楽しく量子力学を理解したい」

【判断のポイント】
・ペルソナの人物像 → area-persona
・ペルソナの現状イメージ → area-current-image
・ペルソナが触れる場面 → area-scene
・つまずきそうな用語・概念 → area-confusion
・「なぜなら」「でも・じつは」の深掘り → area-why-but
・一行の問いにまとめたもの → area-insight
"""
    elif phase == "hirameku":
        area_guide = """
【絶対ルール：hiramekuフェーズで使えるエリアのみ選ぶ】

hirameku-idea-slot-1〜4 : type=ideaのカード（複数は分散）
hirameku-core           : アイデア以外・課題まとめ
"""
    elif phase == "tsukuru":
        area_guide = """
【絶対ルール：tsukuruフェーズで使えるエリアのみ選ぶ】

area-idea-name    : アイデアの名前
area-core-problem : 解決したい課題
area-look         : 見た目・形・色
area-size         : 大きさ・持ち運び
area-function     : 機能・仕組み
area-feature      : こだわり・特徴
"""
    elif phase == "tamesu":
        area_guide = """
【絶対ルール：tamesuフェーズで使えるエリアのみ選ぶ】

area-good   : 良かった点
area-better : 改善したい点
area-next   : 次にやること
"""
    elif phase == "minutes":
        area_guide = """
【絶対ルール：minutesフェーズで使えるエリアのみ選ぶ】

area-agenda        : 議題・背景・話し合う内容
area-decision      : 会議で決まったこと・合意事項
area-minutes-action: 誰が・何を・いつまでにやるか
area-minutes-next  : 次回会議までにやること
area-pending       : 今回決まらなかった・持ち越しの課題

選び方の基準：
・type=fact / thought=fact → area-agenda
・thought=wish かつ決定した内容 → area-decision
・type=instruction / 誰かへのタスク → area-minutes-action
・thought=wish かつ次回に向けた内容 → area-minutes-next
・type=question / 未解決 → area-pending
"""
    elif phase == "interview":
        area_guide = """
【絶対ルール：interviewフェーズで使えるエリアのみ選ぶ】

area-member    : 参加者名・日付・役職・背景情報
area-recent    : 現在の状況・先月の出来事・進捗報告
area-next-goal : 3ヶ月の方針・目標・やりたいこと・決定事項・まとめ
area-event     : 直近の施策・イベント・アクションアイテム
area-trouble   : 課題・論点・優先順位が不明確なこと
area-connect   : 出会いたい人・ネットワーク・パートナー候補
area-comm-action: コミュニケーターとして支援できること・次のアクション

選び方の基準：
・参加者・背景情報 → area-member
・目標・方針・まとめ → area-next-goal
・施策・イベント・実施予定 → area-event
・課題・問題・優先順位 → area-trouble
・人・紹介・ネットワーク → area-connect
・支援・アクション・次のステップ → area-comm-action
・現状報告・進捗 → area-recent
"""
    elif phase == "custom":
        if areas:
            area_lines = "\n".join(
                f"{a['id']} : {a['label']}" + (f"（{a['hint']}）" if a.get("hint") else "")
                for a in areas
            )
            area_guide = f"""
【絶対ルール：customフェーズで使えるエリアのみ選ぶ】

以下のエリアにカードを振り分けてください：
{area_lines}

選び方の基準：
・カードの内容を各エリアに最も適した形で振り分ける
・似た内容のカードは同じエリアにまとめる
・area フィールドには上記のエリアID（完全一致）を入れること
"""
        else:
            area_guide = """
【絶対ルール：customフェーズで使えるエリアのみ選ぶ】

area-custom-1 : カスタム項目1
area-custom-2 : カスタム項目2
area-custom-3 : カスタム項目3
area-custom-4 : カスタム項目4
area-custom-5 : カスタム項目5
area-custom-6 : カスタム項目6

選び方の基準：
・カードの内容を均等に6つのエリアに分散させる
・似た内容のカードは同じエリアにまとめる
"""
    else:
        area_guide = ""

    user_prompt = f"""
現在のフェーズ：{phase}
カード一覧：{json.dumps(cards, ensure_ascii=False)}

{area_guide}

上記のエリア選択基準に従って、
各カードを最も適切なエリアに配置してください。

必ず以下のJSON配列のみ返してください。
説明文・```は不要です。

[
  {{
    "keyword": "キーワード",
    "area": "エリアID",
    "order": 0,
    "emoji": "内容に合った絵文字1つ"
  }}
]

emojiの選び方：
- 人・関係・チーム系 → 👥🗣️🤝
- 問題・困りごと系 → ❓😟⚠️
- アイデア・提案系 → 💡✨🚀
- 決定・完了系 → ✅📌🎯
- タスク・行動系 → 🏃📋⚙️
- 目標・未来系 → 🌟🎯📈
- イベント・つながり系 → 🎪🤝🌐
"""
    return system_prompt, user_prompt

@app.route("/api/auto_layout", methods=["POST"])
def auto_layout():
    data = request.get_json()
    if not data or "cards" not in data:
        return jsonify({"ok": False, "error": "no cards field"}), 400

    cards = data["cards"]
    phase = data.get("phase", "saguru")
    canvas_width = data.get("canvas_width", 800)
    canvas_height = data.get("canvas_height", 600)
    areas = data.get("areas") or None

    print('受信カード数:', len(data.get('cards',[])))

    system_prompt, user_prompt = build_layout_prompt(phase, cards, canvas_width, canvas_height, areas=areas)

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.4,
        )
    except Exception as e:
        return jsonify({"ok": False, "error": f"OpenAI API error: {e}"}), 500

    raw_output = response.choices[0].message.content or ""
    print("OpenAI raw output:", raw_output)  # デバッグ用
    print('GPTレスポンス（先頭200文字）:', raw_output[:200])

    text = raw_output.strip()

    # ```json〜```で囲まれている場合を除去
    if text.startswith("```"):
        text = text.split("\n", 1)[1]
        text = text.rsplit("```", 1)[0]

    try:
        layout = json.loads(text)
        return jsonify({"ok": True, "layout": layout})
    except json.JSONDecodeError:
        # パース失敗時はエラーログを出して原因を返す
        print("JSON parse failed. Raw output:", raw_output)
        return jsonify({
            "ok": False,
            "error": f"JSON parse error: {raw_output[:200]}"
        }), 500


@app.route("/api/save_png", methods=["POST"])
def save_png():
    data = request.get_json()
    if not data or "image" not in data or "title" not in data:
        return jsonify({"ok": False, "error": "image and title fields required"}), 400

    image_data = data["image"]
    title = data["title"]

    # base64データをデコード
    try:
        image_bytes = base64.b64decode(image_data.split(',')[1])  # data:image/png;base64, の部分を除去
    except Exception as e:
        return jsonify({"ok": False, "error": f"Invalid base64 data: {e}"}), 400

    # ファイル名生成: タイトル_日付_時刻.png
    now = datetime.now()
    date_str = now.strftime("%Y%m%d")
    time_str = now.strftime("%H%M%S")
    filename = f"{title}_{date_str}_{time_str}.png"

    # static/saved/ に保存
    saved_dir = os.path.join(os.path.dirname(__file__), "static", "saved")
    os.makedirs(saved_dir, exist_ok=True)
    filepath = os.path.join(saved_dir, filename)

    try:
        with open(filepath, "wb") as f:
            f.write(image_bytes)
    except Exception as e:
        return jsonify({"ok": False, "error": f"Failed to save file: {e}"}), 500

    return jsonify({"ok": True, "filename": filename})


# =========================
# タイムラプス API
# =========================

@app.route("/api/save_timelapse", methods=["POST"])
def save_timelapse():
    """フロントエンドからキャンバスのスクリーンショットを受け取り保存する（認証不要）"""
    data = request.get_json()
    if not data:
        return jsonify({"ok": False, "error": "no data"}), 400
    session_id = data.get("session_id", "").strip()
    image_data  = data.get("image", "")
    if not session_id or not image_data:
        return jsonify({"ok": False, "error": "session_id and image required"}), 400

    safe_id = re.sub(r'[^\w\-]', '_', session_id, flags=re.UNICODE)[:80]
    session_dir = os.path.join(TIMELAPSE_DIR, safe_id)
    os.makedirs(session_dir, exist_ok=True)

    try:
        if ',' in image_data:
            image_data = image_data.split(',', 1)[1]
        img_bytes = base64.b64decode(image_data)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{ts}.png"
        with open(os.path.join(session_dir, filename), "wb") as f:
            f.write(img_bytes)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

    return jsonify({"ok": True, "filename": filename})


@app.route("/api/list_timelapse/<path:session_id>", methods=["GET"])
@require_admin
def list_timelapse(session_id):
    safe_id = re.sub(r'[^\w\-]', '_', session_id, flags=re.UNICODE)[:80]
    session_dir = os.path.join(TIMELAPSE_DIR, safe_id)
    if not os.path.isdir(session_dir):
        return jsonify({"ok": True, "images": []})
    images = []
    for f in sorted(os.listdir(session_dir)):
        if f.endswith('.png'):
            ts_str = f.replace('.png', '')
            images.append({
                "filename": f,
                "url": f"/api/timelapse_img/{safe_id}/{f}",
                "timestamp": ts_str
            })
    return jsonify({"ok": True, "session_id": session_id, "images": images})


@app.route("/api/timelapse_img/<path:session_id>/<filename>")
@require_admin
def serve_timelapse_img(session_id, filename):
    safe_id = re.sub(r'[^\w\-]', '_', session_id, flags=re.UNICODE)[:80]
    session_dir = os.path.join(TIMELAPSE_DIR, safe_id)
    if not filename.endswith('.png') or '/' in filename or '..' in filename:
        return jsonify({"ok": False}), 400
    return send_from_directory(session_dir, filename)


@app.route("/api/download_session_zip/<path:session_id>", methods=["GET"])
@require_admin
def download_session_zip(session_id):
    """1つのセッションだけ：セッションJSON・録音・タイムラプスをまとめてZIPで"""
    sid = _sanitize_session_id(session_id)
    jpath = os.path.join(SESSIONS_DIR, f"{sid}.json")
    if not os.path.exists(jpath):
        return jsonify({"ok": False, "error": "session not found"}), 404
    rec_prefix = "rec_" + re.sub(r'[^\w\-]', '_', session_id, flags=re.UNICODE)[:40] + "_"
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
    ascii_id = re.sub(r'[^\w\-]', '_', session_id, flags=re.ASCII)[:60]
    cd = f"attachment; filename=\"session_{ascii_id}.zip\"; filename*=UTF-8''{urlquote(f'session_{sid}.zip', safe='')}"
    print(f"セッションZIP: {sid} 録音{n_rec} タイムラプス{n_tl}")
    return Response(buf.read(), mimetype="application/zip", headers={"Content-Disposition": cd})


@app.route("/api/download_timelapse_zip/<path:session_id>", methods=["GET"])
@require_admin
def download_timelapse_zip(session_id):
    """セッションのタイムラプス画像をまとめてZIPでダウンロード"""
    safe_id = re.sub(r'[^\w\-]', '_', session_id, flags=re.UNICODE)[:80]
    session_dir = os.path.join(TIMELAPSE_DIR, safe_id)
    if not os.path.isdir(session_dir):
        return jsonify({"ok": False, "error": "タイムラプスデータがありません"}), 404

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for fname in sorted(os.listdir(session_dir)):
            if fname.endswith('.png'):
                zf.write(os.path.join(session_dir, fname), fname)
    buf.seek(0)
    # ASCII安全なファイル名（班など非ASCII文字はアンダースコアに置換）
    ascii_id = re.sub(r'[^\w\-]', '_', session_id)[:40]  # re.UNICODEなし → ASCIIのみ
    zip_name = f"timelapse_{ascii_id}.zip"
    zip_name_encoded = urlquote(f"timelapse_{safe_id}.zip", safe='')
    cd = f"attachment; filename=\"{zip_name}\"; filename*=UTF-8''{zip_name_encoded}"
    return Response(
        buf.read(),
        mimetype="application/zip",
        headers={"Content-Disposition": cd}
    )


@app.route("/api/list_recordings", methods=["GET"])
@require_admin
def list_recordings():
    """録音ファイル一覧を返す（session_id クエリで絞り込み可）"""
    sid_filter = request.args.get("session_id", "")
    safe_filter = re.sub(r'[^\w\-]', '_', sid_filter, flags=re.UNICODE)[:40] if sid_filter else ""
    try:
        files = []
        for fname in sorted(os.listdir(RECORDINGS_DIR), reverse=True):
            if not (fname.endswith('.webm') or fname.endswith('.ogg') or fname.endswith('.wav') or fname.endswith('.mp4')):
                continue
            if safe_filter and safe_filter not in fname:
                continue
            fpath = os.path.join(RECORDINGS_DIR, fname)
            size_kb = round(os.path.getsize(fpath) / 1024)
            files.append({
                "filename": fname,
                "size_kb": size_kb,
                "url": f"/api/download_recording/{fname}"
            })
        return jsonify({"ok": True, "files": files, "dir": RECORDINGS_DIR})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/download_recording/<filename>", methods=["GET"])
@require_admin
def download_recording(filename):
    """録音ファイルを個別にダウンロード"""
    if '/' in filename or '..' in filename:
        return jsonify({"ok": False}), 400
    allowed_ext = ('.webm', '.ogg', '.wav', '.mp4', '.m4a')
    if not any(filename.endswith(e) for e in allowed_ext):
        return jsonify({"ok": False}), 400
    return send_from_directory(RECORDINGS_DIR, filename, as_attachment=True)


@app.route("/api/download_all_zip", methods=["GET"])
@require_admin
def download_all_zip():
    """全データ（セッション JSON + タイムラプス + 録音）をZIPで一括ダウンロード"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        # セッション JSON
        if os.path.isdir(SESSIONS_DIR):
            for fname in sorted(os.listdir(SESSIONS_DIR)):
                if fname.endswith('.json'):
                    zf.write(os.path.join(SESSIONS_DIR, fname), f"sessions/{fname}")
        # タイムラプス画像
        if os.path.isdir(TIMELAPSE_DIR):
            for sid in sorted(os.listdir(TIMELAPSE_DIR)):
                sid_path = os.path.join(TIMELAPSE_DIR, sid)
                if os.path.isdir(sid_path):
                    for fname in sorted(os.listdir(sid_path)):
                        if fname.endswith('.png'):
                            zf.write(os.path.join(sid_path, fname), f"timelapse/{sid}/{fname}")
        # 録音ファイル
        if os.path.isdir(RECORDINGS_DIR):
            for fname in sorted(os.listdir(RECORDINGS_DIR)):
                fpath = os.path.join(RECORDINGS_DIR, fname)
                if os.path.isfile(fpath):
                    zf.write(fpath, f"recordings/{fname}")
    buf.seek(0)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    zip_name = f"grarec_all_{ts}.zip"
    return Response(
        buf.read(),
        mimetype="application/zip",
        headers={"Content-Disposition": f"attachment; filename=\"{zip_name}\""}
    )


@app.route("/api/generate_custom_areas", methods=["POST"])
def generate_custom_areas():
    data = request.get_json()
    agenda = data.get("agenda", "") if data else ""

    system_prompt = (
        "あなたはファシリテーション支援AIです。"
        "入力されたアジェンダや話し合いのテーマをもとに、"
        "グラフィックレコーディング用のワークシートエリアを最大6つ生成してください。\n\n"
        "必ずアジェンダの内容を反映した具体的なエリア名をつけてください。"
        "「項目1」「エリア1」「項目A」のような汎用的な名前は絶対に使わないでください。\n\n"
        "出力形式（JSONのみ、マークダウン不要）：\n"
        '{"areas": ['
        '{"id": "area_1", "label": "具体的なエリア名（10文字以内）", '
        '"hint": "このエリアに貼るカードの説明（20文字以内）", "emoji": "絵文字1つ"}'
        ']}'
    )
    user_prompt = f"以下のアジェンダ・テーマでワークシートを作ってください：\n{agenda}"
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.4,
    )

    text = response.choices[0].message.content.strip()
    if text.startswith("```"):
        text = re.sub(r"```[a-z]*\n?", "", text).strip().rstrip("```").strip()
    parsed = json.loads(text)
    areas = parsed.get("areas", parsed) if isinstance(parsed, dict) else parsed
    for i, area in enumerate(areas):
        area["index"] = i
        area["id"] = f"area-custom-{i + 1}"
    return jsonify({"ok": True, "areas": areas})


# =========================
# 音声長さ取得 API
# =========================
@app.route("/api/get_audio_duration", methods=["POST"])
def get_audio_duration():
    if "audio" not in request.files:
        return jsonify({"ok": False, "error": "no audio file"}), 400
    audio_file = request.files["audio"]
    suffix = os.path.splitext(audio_file.filename)[1] or ".webm"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp_path = tmp.name
        audio_file.save(tmp_path)
    try:
        result = subprocess.run([
            "ffprobe", "-v", "quiet",
            "-print_format", "json",
            "-show_streams", tmp_path
        ], capture_output=True, text=True)
        probe = json.loads(result.stdout)
        duration = float(probe["streams"][0]["duration"])
        return jsonify({"ok": True, "duration": duration})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass


# =========================
# チャンク分割処理 API
# =========================
@app.route("/api/transcribe_chunk", methods=["POST"])
def transcribe_chunk():
    """
    音声ファイルの指定した時間範囲をWhisperで文字起こしする（話者分離なし・軽量版）。
    chunk_start, chunk_end: 秒数（form フィールド）
    録音チャンクは RECORDINGS_DIR に保存する（後日オフライン話者分離用）。
    """
    if "audio" not in request.files:
        return jsonify({"ok": False, "error": "no audio file"}), 400

    audio_file = request.files["audio"]
    chunk_start = float(request.form.get("chunk_start", 0))
    chunk_end   = float(request.form.get("chunk_end", 300))
    suffix = os.path.splitext(audio_file.filename)[1] or ".webm"
    raw_sid = request.form.get("session_id", "")
    safe_sid = re.sub(r'[^\w\-]', '_', raw_sid, flags=re.UNICODE)[:40] if raw_sid else ""

    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp_path = tmp.name
        audio_file.save(tmp_path)

    wav_path = tmp_path + "_chunk.wav"
    try:
        result = subprocess.run([
            "ffmpeg", "-i", tmp_path,
            "-ss", str(chunk_start),
            "-to", str(chunk_end),
            "-ar", "16000", "-ac", "1",
            wav_path, "-y"
        ], capture_output=True, text=True)
        if result.returncode != 0:
            print("ffmpeg error:", result.stderr)
            raise Exception("ffmpeg失敗: " + result.stderr[-200:])

        with open(wav_path, "rb") as f:
            whisper_res = client.audio.transcriptions.create(
                model="whisper-1",
                file=f,
                language="ja",
                prompt=WHISPER_HINT
            )
        text = (whisper_res.text or "").strip()

        return jsonify({
            "ok": True,
            "text": text,
            "segments": [],
            "speaker_summary": [],
            "chunk_start": chunk_start,
            "chunk_end": chunk_end
        })

    except Exception as e:
        print("transcribe_chunk error:", traceback.format_exc())
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        try:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            start_sec = int(chunk_start)
            sid_part = f"_{safe_sid}" if safe_sid else ""
            rec_name = f"chunk{sid_part}_{ts}_{start_sec}s{suffix}"
            dest = os.path.join(RECORDINGS_DIR, rec_name)
            shutil.copy2(tmp_path, dest)
            print(f"録音チャンク保存: {dest}")
        except Exception as _e:
            print(f"ERROR: 録音チャンクファイル保存失敗: {_e} | RECORDINGS_DIR={RECORDINGS_DIR}")
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        try:
            if os.path.exists(wav_path):
                os.remove(wav_path)
        except OSError:
            pass


# =========================
# セッションデータ保存 API
# =========================
def _sanitize_session_id(sid: str) -> str:
    return re.sub(r'[^\w\-]', '_', sid, flags=re.UNICODE)[:80]


@app.route("/api/save_session", methods=["POST"])
def save_session():
    data = request.get_json()
    if not data or "session_id" not in data:
        return jsonify({"ok": False, "error": "session_id required"}), 400

    session_id = _sanitize_session_id(str(data["session_id"]))
    if not session_id:
        return jsonify({"ok": False, "error": "invalid session_id"}), 400

    data["session_id"] = session_id
    data["saved_at"] = datetime.now().isoformat()

    filepath = os.path.join(SESSIONS_DIR, f"{session_id}.json")
    try:
        os.makedirs(SESSIONS_DIR, exist_ok=True)
        # 別のファイルに書き終わってから入れ替える（途中でサーバーが止まっても、前のファイルが壊れない）
        tmp_path = f"{filepath}.{os.getpid()}.{threading.get_ident()}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, filepath)
        print(f"セッション保存OK: {filepath}")
        return jsonify({"ok": True, "session_id": session_id})
    except Exception as e:
        print(f"ERROR: セッション保存失敗: {e} | path={filepath}")
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/get_session/<session_id>", methods=["GET"])
def get_session(session_id):
    session_id = _sanitize_session_id(session_id)
    filepath = os.path.join(SESSIONS_DIR, f"{session_id}.json")
    if not os.path.exists(filepath):
        return jsonify({"ok": False, "error": "session not found"}), 404
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
        return jsonify({"ok": True, "session": data})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# =========================
# 班別プロジェクト API
# =========================
def _sanitize_group_number(gn: str) -> str:
    """班番号を安全なファイル名用文字列に変換する。"""
    return re.sub(r'[^\w]', '_', str(gn))[:20]


@app.route("/api/get_project/<group_number>", methods=["GET"])
def get_project(group_number):
    gn = _sanitize_group_number(group_number)
    filepath = os.path.join(PROJECTS_DIR, f"group_{gn}.json")
    if not os.path.exists(filepath):
        return jsonify({"ok": True, "found": False})
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
        return jsonify({"ok": True, "found": True, "project": data})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/save_project", methods=["POST"])
def save_project():
    data = request.get_json()
    if not data or "group_number" not in data:
        return jsonify({"ok": False, "error": "group_number required"}), 400

    gn = _sanitize_group_number(data["group_number"])
    if not gn:
        return jsonify({"ok": False, "error": "invalid group_number"}), 400

    filepath = os.path.join(PROJECTS_DIR, f"group_{gn}.json")

    # 既存プロジェクトを読み込むか新規作成
    if os.path.exists(filepath):
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                project = json.load(f)
        except Exception:
            project = {"group_number": gn, "sessions": []}
    else:
        project = {"group_number": gn, "sessions": []}

    # プロジェクト状態を更新（ノートとフェーズ）
    project["group_number"] = gn
    project["title"]       = data.get("title",    project.get("title", ""))
    project["phase"]       = data.get("phase",    project.get("phase", "saguru"))
    project["template"]    = data.get("template", project.get("template", "saguru"))
    project["notes"]       = data.get("notes",    project.get("notes", []))
    project["updated_at"]  = datetime.now().isoformat()

    # セッションログに追記（session_id単位で1エントリ）
    session_id = data.get("session_id", "")
    today = datetime.now().strftime("%Y-%m-%d")
    sessions = project.get("sessions", [])

    existing = next((s for s in sessions if s.get("session_id") == session_id), None)
    if existing:
        existing["phase_reached"] = data.get("phase", existing.get("phase_reached", ""))
        existing["card_count"]    = len(data.get("notes", []))
        existing["ai_support"]    = data.get("aiSupport", existing.get("ai_support", True))
        existing["saved_at"]      = datetime.now().isoformat()
    else:
        sessions.append({
            "date":          today,
            "session_id":    session_id,
            "phase_reached": data.get("phase", "saguru"),
            "ai_support":    data.get("aiSupport", True),
            "card_count":    len(data.get("notes", [])),
            "started_at":    data.get("created_at", datetime.now().isoformat()),
            "saved_at":      datetime.now().isoformat(),
        })

    project["sessions"] = sessions

    try:
        os.makedirs(PROJECTS_DIR, exist_ok=True)
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(project, f, ensure_ascii=False, indent=2)
        print(f"プロジェクト保存OK: {filepath} | カード数={len(project.get('notes',[]))}")
        return jsonify({"ok": True, "group_number": gn})
    except Exception as e:
        print(f"ERROR: プロジェクト保存失敗: {e} | path={filepath}")
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/list_projects", methods=["GET"])
@require_admin
def list_projects():
    try:
        projects = []
        for fname in sorted(os.listdir(PROJECTS_DIR)):
            if not fname.endswith(".json") or not fname.startswith("group_"):
                continue
            fpath = os.path.join(PROJECTS_DIR, fname)
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    d = json.load(f)
                projects.append({
                    "group_number": d.get("group_number", ""),
                    "title":        d.get("title", ""),
                    "phase":        d.get("phase", ""),
                    "note_count":   len(d.get("notes", [])),
                    "sessions":     d.get("sessions", []),
                    "updated_at":   d.get("updated_at", ""),
                })
            except Exception:
                pass
        return jsonify({"ok": True, "projects": projects})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


def _compute_metrics(d: dict) -> dict:
    """セッションデータから活用度指標を計算する。"""
    notes = d.get("notes", [])

    # エリア分布
    area_dist: dict = defaultdict(int)
    for n in notes:
        area = n.get("area") or "unplaced"
        area_dist[area] += 1

    # 手動 / AI 別カード数
    manual_count  = sum(1 for n in notes if n.get("source") == "manual")
    ai_count      = sum(1 for n in notes if n.get("source") == "ai")
    unknown_count = sum(1 for n in notes if not n.get("source"))

    # セッション時間推定（分）
    duration_min = None
    created_at = d.get("created_at") or ""
    saved_at   = d.get("saved_at") or ""
    if created_at and saved_at:
        try:
            t0 = datetime.fromisoformat(created_at)
            t1 = datetime.fromisoformat(saved_at)
            diff = (t1 - t0).total_seconds() / 60
            if diff >= 0:
                duration_min = round(diff, 1)
        except Exception:
            pass

    # カード追加タイムライン（5分刻みバケット）
    timeline: dict = defaultdict(int)
    for n in notes:
        ts = n.get("addedAt") or n.get("added_at") or ""
        if not ts:
            continue
        try:
            t = datetime.fromisoformat(ts)
            bucket_min = (t.hour * 60 + t.minute) // 5 * 5
            bucket = f"{bucket_min // 60:02d}:{bucket_min % 60:02d}"
            timeline[bucket] += 1
        except Exception:
            pass

    return {
        "area_distribution": dict(area_dist),
        "manual_count":      manual_count,
        "ai_count":          ai_count,
        "unknown_count":     unknown_count,
        "duration_min":      duration_min,
        "card_timeline":     [
            {"bucket": k, "count": v}
            for k, v in sorted(timeline.items())
        ],
    }


@app.route("/api/delete_session/<session_id>", methods=["DELETE"])
@require_admin
def delete_session(session_id):
    session_id = _sanitize_session_id(session_id)
    filepath = os.path.join(SESSIONS_DIR, f"{session_id}.json")
    if not os.path.exists(filepath):
        return jsonify({"ok": False, "error": "session not found"}), 404
    try:
        os.remove(filepath)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/delete_project/<group_number>", methods=["DELETE"])
@require_admin
def delete_project(group_number):
    gn = _sanitize_group_number(group_number)
    filepath = os.path.join(PROJECTS_DIR, f"group_{gn}.json")
    if not os.path.exists(filepath):
        return jsonify({"ok": False, "error": "project not found"}), 404
    try:
        os.remove(filepath)
        print(f"プロジェクト削除: {filepath}")
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/delete_all_data", methods=["POST"])
@require_admin
def delete_all_data():
    body = request.get_json() or {}
    include_media = bool(body.get("include_media", False))
    dirs_to_clear = [SESSIONS_DIR, PROJECTS_DIR]
    if include_media:
        dirs_to_clear += [RECORDINGS_DIR, TIMELAPSE_DIR]
    deleted = 0
    errors = []
    for d in dirs_to_clear:
        if not os.path.isdir(d):
            continue
        for fname in os.listdir(d):
            fpath = os.path.join(d, fname)
            try:
                if os.path.isfile(fpath):
                    os.remove(fpath)
                    deleted += 1
            except Exception as e:
                errors.append(f"{fname}: {e}")
    print(f"一括削除完了: {deleted}ファイル削除 include_media={include_media}")
    return jsonify({"ok": True, "deleted": deleted, "errors": errors})


@app.route("/api/list_sessions", methods=["GET"])
@require_admin
def list_sessions():
    # ?type=mendan → mendan_ prefix のみ / ?type=general → それ以外 / 省略 → 全件
    session_type = request.args.get("type", "all")
    try:
        sessions = []
        for fname in sorted(os.listdir(SESSIONS_DIR), reverse=True):
            if not fname.endswith(".json"):
                continue
            sid = fname[:-5]
            if session_type == "mendan" and not sid.startswith("mendan_"):
                continue
            if session_type == "general" and sid.startswith("mendan_"):
                continue
            fpath = os.path.join(SESSIONS_DIR, fname)
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    d = json.load(f)
                metrics = _compute_metrics(d)
                sessions.append({
                    "session_id":      d.get("session_id", sid),
                    "group_number":    d.get("group_number", ""),
                    "operator_name":   d.get("operator_name", ""),
                    "title":           d.get("title", ""),
                    "phase":           d.get("phase", ""),
                    "template":        d.get("template", ""),
                    "ai_support":      d.get("aiSupport", True),
                    "note_count":      len(d.get("notes", [])),
                    "speaker_summary": d.get("speakerSummary", []),
                    "saved_at":        d.get("saved_at", ""),
                    "created_at":      d.get("created_at", ""),
                    **metrics,
                })
            except Exception:
                pass
        return jsonify({"ok": True, "sessions": sessions})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# =========================
# 課題シート（/kadai）: カード抽出
# =========================
# 研究目的（対話構造のリアルタイム可視化で「議論構造」「他者の意見」「自己の考え」への
# 気づきを促す）に合わせ、「重要な発言を全部」ではなく「話し合いを一歩引いて見るための
# 少数のカード」を、役割の違う種類から選ぶ。AIは新しい答えやアイデアを足さない。
CARD_KINDS = {
    "topic":      "論点",      # いま班が中心に話していること → 議論構造
    "opinion":    "意見",      # 具体的な意見・アイデア・経験 → 他者の意見／自己の考え
    "difference": "ちがい",    # 意見が分かれた点・別の見方 → 他者の意見／議論構造
    "question":   "問い",      # 出たがまだ答えが出ていない問い → 議論構造（次の一歩）
    "agreement":  "まとまり",  # 班で合意した・決まったこと → 議論構造
}
KIND_IMPORTANCE = {"question": "high", "difference": "high", "topic": "medium",
                   "agreement": "medium", "opinion": "medium"}


def build_card_prompt(memo, prev_text, existing, first_cycle, max_cards, task_hint=""):
    system_prompt = (
        "あなたは大学の授業のグループワークを「見える化」するアシスタントです。\n"
        "参加者が自分たちの話し合いを一歩引いて見て、話の流れや他の人の意見、"
        "自分の考えに気づけるように、話し合いの記録から少数のカードを選びます。\n"
        "あなたは議事録係でも、アイデアや答えを出す人でもありません。"
        "実際に話された内容だけを、話し手の言葉をなるべく残して短くします。"
    )
    existing_txt = "\n".join(f"・{k}" for k in existing[:60]) or "（まだありません）"
    if first_cycle:
        count_rule = (
            f"・今回は最初のカードです。「この班がいま何について話しているか」がわかる\n"
            f"  論点(topic)を中心に3〜{min(4, max_cards)}枚にする\n"
        )
    else:
        count_rule = (
            f"・最大{max_cards}枚。大事なものが少なければ少なくてよい（無理に埋めない）\n"
            "・同じ種類(kind)は最大2枚まで\n"
            "・問い(question)とちがい(difference)が話し合いに出ていれば、優先して1枚ずつ入れる\n"
            "  （話し合いの次の一歩や、他の人の見方に気づくきっかけになるため）\n"
        )
    user_prompt = (
        (f"【この授業の課題】\n{task_hint}\n\n" if task_hint else "")
        + "【直前の区間の発話（文脈として参照するだけ。ここからは選ばない）】\n"
        f"{prev_text or '（なし）'}\n\n"
        "【今回の区間の発話（ここからカードを選ぶ）】\n"
        f"{memo}\n\n"
        "【すでに画面に出ているカード】\n"
        f"{existing_txt}\n\n"
        "【カードの種類 kind】\n"
        "- topic（論点）：いまこの班が中心に話していること\n"
        "- opinion（意見）：誰かが出した具体的な意見・アイデア・経験\n"
        "- difference（ちがい）：意見が分かれている点、別の見方が出た点\n"
        "- question（問い）：出たけれど、まだ答えが出ていない問い・保留になっていること\n"
        "- agreement（まとまり）：班で合意した・決まったこと\n\n"
        "【選び方】\n"
        + count_rule +
        "・発話の行頭の「話者1：」などは話し手の区別（名前ではない）。誰か1人だけが言って他の人が反応していない視点や、\n"
        "  話者によって意見が分かれている点を見つける手がかりにする。keyword や quote には話者番号を書かない\n"
        "・1回しか出ず誰も拾わなかったが、話し合いを広げられそうな視点があれば1枚入れ、lone を true にする\n"
        "・すでに出ているカードと同じ内容は出さない。ただし同じ話題がこの区間でも中心的に\n"
        "  繰り返されたときは、既存カードの文言を一字一句そのまま keyword に入れて返す\n"
        "・新しいアイデア・答え・助言・まとめの言い換えを付け足さない（話されていないことは書かない）\n\n"
        "【選ばない発言】\n"
        "・雑談、相槌、笑い、段取りや役割分担だけの発言\n"
        "・GraRec・カード・録音・画面など、道具の操作や感想についての発言\n\n"
        "【書き方】\n"
        "・keyword：10〜20文字。話し手の言葉を残す。「〜について話す」「Aさんが〜を提案」のような説明にしない\n"
        "  良い例：昼休みに食堂が混みすぎる／なぜみんな同じ時間に行くのか？\n"
        "・quote：元の発言をそのまま（40文字以内）\n"
        "・type：fact|opinion|question|idea|reaction|instruction（発話の形式）\n"
        "・thought：fact|problem|interpret|wish（発話の意図）\n"
        "・reason：このカードを選んだ理由（20文字以内）\n\n"
        "次のJSONオブジェクトだけを返す：\n"
        '{"cards":[{"keyword":"","quote":"","kind":"topic|opinion|difference|question|agreement",'
        '"lone":false,"type":"","thought":"","reason":""}]}'
    )
    return system_prompt, user_prompt


def _select_cards(raw_cards, max_cards):
    """AIの出力を検証し、枚数・種類の上限を守らせる。"""
    out, per_kind, seen = [], defaultdict(int), set()
    for c in raw_cards:
        if not isinstance(c, dict):
            continue
        kw = str(c.get("keyword", "")).strip()
        kind = str(c.get("kind", "")).strip().lower()
        if not kw or kind not in CARD_KINDS or kw in seen:
            continue
        if per_kind[kind] >= 2:
            continue
        per_kind[kind] += 1
        seen.add(kw)
        out.append({
            "keyword": kw[:40],
            "quote": str(c.get("quote", "")).strip()[:80],
            "kind": kind,
            "kind_label": CARD_KINDS[kind],
            "lone": bool(c.get("lone", False)),
            "type": str(c.get("type", "")).strip().lower(),
            "thought": str(c.get("thought", "")).strip().lower(),
            "reason": str(c.get("reason", "")).strip()[:40],
            "importance": KIND_IMPORTANCE.get(kind, "medium"),
        })
        if len(out) >= max_cards:
            break
    return out


# カード選びに使うモデル。環境変数 CARD_MODEL で切り替えられる。
# 使えなかったとき（モデル名の誤り・API側の不調など）は CARD_FALLBACK_MODEL で自動的にやり直す。
# ※ /quantum・/mendan の DA 分類は、κ で妥当性を確認した gpt-4o-mini のまま。
CARD_MODEL = os.getenv("CARD_MODEL", "gpt-5.4-mini")
CARD_FALLBACK_MODEL = os.getenv("CARD_FALLBACK_MODEL", "gpt-4o-mini")


def card_chat_json(system_prompt, user_prompt):
    messages = [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}]
    try:
        res = client.chat.completions.create(
            model=CARD_MODEL, messages=messages,
            response_format={"type": "json_object"},
            extra_body={"reasoning_effort": "low"},
        )
        return res.choices[0].message.content or "{}", CARD_MODEL
    except Exception as e:
        print(f"{CARD_MODEL} で失敗したので {CARD_FALLBACK_MODEL} でやり直します: {e}")
        res = client.chat.completions.create(
            model=CARD_FALLBACK_MODEL, messages=messages,
            response_format={"type": "json_object"}, temperature=0.3,
        )
        return res.choices[0].message.content or "{}", CARD_FALLBACK_MODEL


@app.route("/api/extract_cards", methods=["POST"])
def extract_cards():
    """
    課題シート用のカード抽出。
    memo: 今回の区間の文字起こし / prev_text: 直前の区間（文脈）
    existing: すでに画面に出ているカードの文言 / first_cycle: 最初のカードか
    """
    data = request.get_json() or {}
    memo = (data.get("memo") or "").strip()
    if len(memo) < 10:
        return jsonify({"ok": True, "cards": []})
    prev_text = (data.get("prev_text") or "").strip()[-500:]
    existing = [str(k) for k in (data.get("existing") or []) if k][:60]
    first_cycle = bool(data.get("first_cycle", False))
    max_cards = max(1, min(int(data.get("max_cards", 6) or 6), 8))
    task_hint = (data.get("task_hint") or "").strip()[:300]

    system_prompt, user_prompt = build_card_prompt(memo, prev_text, existing, first_cycle, max_cards, task_hint)
    try:
        raw, used_model = card_chat_json(system_prompt, user_prompt)
    except Exception as e:
        return jsonify({"ok": False, "error": f"OpenAI API error: {e}"}), 500

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        print("extract_cards JSON parse error:", raw[:200])
        return jsonify({"ok": False, "error": "AIの応答を読み取れませんでした"}), 500
    raw_cards = parsed.get("cards", []) if isinstance(parsed, dict) else parsed
    cards = _select_cards(raw_cards if isinstance(raw_cards, list) else [], max_cards)
    return jsonify({"ok": True, "cards": cards, "first_cycle": first_cycle, "model": used_model})


# =========================
# 課題シート（/kadai）: PowerPoint 書き出し
# =========================
KADAI_TEMPLATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "templates", "kadai_format.pptx")
# 元フォーマットの自由スペース（区切り線の下〜ページ番号の上）。単位はEMU
KADAI_BODY = {"left": 287557, "top": 1320000, "width": 9360000, "height": 4980000}
KADAI_CARD_W = 1783080   # シート幅の18%（画面のカード幅と同じ比率）
KADAI_CARD_H = 560000


def _safe_filename_part(t: str) -> str:
    return re.sub(r'[\\/:*?"<>|\s]', '_', t)[:40]


def _kadai_pct(v, lo=0.0, hi=100.0):
    try:
        return max(lo, min(float(v), hi))
    except (TypeError, ValueError):
        return None


def _kadai_set_text(tf, text):
    """書式（最初の文字の大きさ・色など）を残したまま、文字だけ入れ替える"""
    paras = tf.paragraphs
    for extra in paras[1:]:
        extra._p.getparent().remove(extra._p)
    runs = paras[0].runs
    if runs:
        runs[0].text = text
        for r in runs[1:]:
            r._r.getparent().remove(r._r)
    else:
        paras[0].add_run().text = text


def _kadai_color(v, default=(0xDD, 0xDD, 0xDD)):
    """'#9c27b0' または 'rgba(243,229,245,.75)'（白の上に重ねた色にする）を (r, g, b) に"""
    v = str(v or "").strip()
    try:
        if v.startswith("#") and len(v) == 7:
            return tuple(int(v[i:i + 2], 16) for i in (1, 3, 5))
        if v.startswith("rgba(") or v.startswith("rgb("):
            parts = [float(x) for x in v[v.index("(") + 1:v.index(")")].split(",")]
            a = parts[3] if len(parts) > 3 else 1.0
            return tuple(int(round(c * a + 255 * (1 - a))) for c in parts[:3])
    except (ValueError, IndexError):
        pass
    return default


def _kadai_add_area(slide, a, Emu, Pt, RGBColor, MSO_SHAPE):
    """デザイン思考ワークシートのエリア（枠・見出し・説明）"""
    b = KADAI_BODY
    x, y = _kadai_pct(a.get("x", 0)), _kadai_pct(a.get("y", 0))
    w, h = _kadai_pct(a.get("w", 30), 1.0), _kadai_pct(a.get("h", 30), 1.0)
    if None in (x, y, w, h):
        return
    shp = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Emu(int(b["left"] + b["width"] * x / 100)),
                                 Emu(int(b["top"] + b["height"] * y / 100)),
                                 Emu(int(b["width"] * w / 100)), Emu(int(b["height"] * h / 100)))
    shp.adjustments[0] = 0.03
    shp.fill.solid()
    shp.fill.fore_color.rgb = RGBColor(*_kadai_color(a.get("b"), (0xF5, 0xF5, 0xF5)))
    shp.line.color.rgb = RGBColor(*_kadai_color(a.get("c"), (0x99, 0x99, 0x99)))
    shp.line.width = Pt(1.25)
    shp.shadow.inherit = False
    tf = shp.text_frame
    tf.word_wrap = True
    from pptx.enum.text import MSO_ANCHOR
    tf.vertical_anchor = MSO_ANCHOR.TOP
    for side in ("margin_left", "margin_right", "margin_top", "margin_bottom"):
        setattr(tf, side, Emu(72000))
    from pptx.enum.text import PP_ALIGN
    tf.paragraphs[0].alignment = PP_ALIGN.LEFT
    r = tf.paragraphs[0].add_run()
    r.text = str(a.get("label", ""))[:40]
    r.font.size = Pt(12)
    r.font.bold = True
    r.font.color.rgb = RGBColor(0x22, 0x22, 0x22)
    hint = str(a.get("hint", ""))[:120]
    if hint:
        p2 = tf.add_paragraph()
        p2.alignment = PP_ALIGN.LEFT
        r2 = p2.add_run()
        r2.text = hint
        r2.font.size = Pt(8)
        r2.font.color.rgb = RGBColor(0x55, 0x55, 0x55)


def _kadai_add_card(slide, c, Emu, Pt, RGBColor, MSO_SHAPE, MSO_ANCHOR):
    b = KADAI_BODY
    x, y = _kadai_pct(c.get("x", 0)), _kadai_pct(c.get("y", 0))
    if x is None or y is None:
        return
    left = min(int(b["left"] + b["width"] * x / 100), b["left"] + b["width"] - KADAI_CARD_W)
    top = min(int(b["top"] + b["height"] * y / 100), b["top"] + b["height"] - KADAI_CARD_H)
    box = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Emu(left), Emu(top), Emu(KADAI_CARD_W), Emu(KADAI_CARD_H))
    box.fill.solid()
    box.fill.fore_color.rgb = RGBColor(0xFF, 0xF9, 0xD9)
    box.line.color.rgb = RGBColor(0xE6, 0xD9, 0x8C)
    box.shadow.inherit = False
    tf = box.text_frame
    tf.word_wrap = True
    tf.vertical_anchor = MSO_ANCHOR.MIDDLE
    for side in ("margin_left", "margin_right", "margin_top", "margin_bottom"):
        setattr(tf, side, Emu(54000))
    run = tf.paragraphs[0].add_run()
    run.text = str(c.get("keyword", ""))[:60]
    run.font.size = Pt(12)
    run.font.bold = True
    run.font.color.rgb = RGBColor(0x11, 0x11, 0x11)


def _kadai_add_text(slide, t, Emu, Pt, RGBColor):
    """ふつうのPowerPointのテキストボックスと同じ（枠線・塗りなし、幅固定で折り返し、高さは文字に合わせる）"""
    from pptx.enum.text import MSO_AUTO_SIZE
    b = KADAI_BODY
    text = str(t.get("text", "")).replace("\r", "")[:2000]
    if not text.strip():
        return
    x, y = _kadai_pct(t.get("x", 0)), _kadai_pct(t.get("y", 0))
    w = _kadai_pct(t.get("w", 30), 3.0, 100.0)
    if x is None or y is None or w is None:
        return
    try:
        size = max(8, min(int(float(t.get("size", 18))), 72))
    except (TypeError, ValueError):
        size = 18
    lines = text.split("\n")
    left = int(b["left"] + b["width"] * x / 100)
    top = int(b["top"] + b["height"] * y / 100)
    width = int(b["width"] * w / 100)
    height = int(Pt(size).emu * 1.3 * len(lines) + 91440)
    box = slide.shapes.add_textbox(Emu(left), Emu(top), Emu(width), Emu(height))
    tf = box.text_frame
    tf.word_wrap = True
    tf.auto_size = MSO_AUTO_SIZE.SHAPE_TO_FIT_TEXT
    for i, line in enumerate(lines):
        para = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        run = para.add_run()
        run.text = line
        run.font.size = Pt(size)
        run.font.color.rgb = RGBColor(0x11, 0x11, 0x11)


@app.route("/api/export_kadai_pptx", methods=["POST"])
def export_kadai_pptx():
    try:
        from pptx import Presentation
        from pptx.util import Emu, Pt
        from pptx.dml.color import RGBColor
        from pptx.enum.shapes import MSO_SHAPE
        from pptx.enum.text import MSO_ANCHOR
    except ImportError:
        return jsonify({"ok": False, "error": "python-pptx が入っていません。pip install python-pptx を実行してください"}), 500

    data = request.get_json() or {}
    kadai_no = str(data.get("kadai_no", "")).strip()[:10] or "1-1"
    leader_id = str(data.get("leader_id", "")).strip()[:20]
    members = data.get("members") or []
    cards = data.get("cards") or []
    group = str(data.get("group_number", "")).strip()[:10]
    slides_in = data.get("slides")
    if not isinstance(slides_in, list) or not slides_in:
        slides_in = [{"cards": cards, "texts": []}]
    slides_in = [x for x in slides_in if isinstance(x, dict)][:30] or [{"cards": [], "texts": []}]
    is_design = data.get("sheet") == "design"
    theme = str(data.get("theme", "")).strip()[:80]

    prs = Presentation(KADAI_TEMPLATE)
    slide = prs.slides[0]

    for shape in slide.shapes:
        # 「課題 1-1」の番号部分
        if shape.has_text_frame and "課題" in shape.text_frame.text:
            runs = [r for p in shape.text_frame.paragraphs for r in p.runs]
            if len(runs) >= 2:
                runs[-1].text = kadai_no
        # リーダ学番・メンバ名の表
        if shape.has_table:
            table = shape.table
            c_id = table.cell(1, 0).text_frame
            c_id.paragraphs[0].text = ""
            r = c_id.paragraphs[0].add_run()
            r.text = leader_id
            tf = table.cell(1, 1).text_frame
            for extra in tf.paragraphs[1:]:
                extra._p.getparent().remove(extra._p)
            para = tf.paragraphs[0]
            names = [m for m in members if isinstance(m, dict) and str(m.get("name", "")).strip()]
            for i, m in enumerate(names):
                if i:
                    sep = para.add_run()
                    sep.text = "、"
                run = para.add_run()
                run.text = str(m["name"]).strip()[:20]
                role = m.get("role", "")
                if role == "leader":
                    run.font.color.rgb = RGBColor(0xFF, 0x00, 0x00)
                elif role == "observer":
                    run.font.color.rgb = RGBColor(0x00, 0x70, 0xC0)

    # デザイン思考ワークシート：見出しを「テーマ｜チームメンバ」に、左上はフェーズ名（あとで各スライドに入れる）
    if is_design:
        for shape in slide.shapes:
            if shape.has_text_frame and not shape.is_placeholder and "課題" in shape.text_frame.text:
                _kadai_set_text(shape.text_frame, "__PHASE__")
            if shape.has_table:
                tb = shape.table
                total = sum(col.width for col in tb.columns)
                tb.columns[0].width = int(total * 0.38)
                tb.columns[1].width = total - int(total * 0.38)
                _kadai_set_text(tb.cell(0, 0).text_frame, "テーマ")
                _kadai_set_text(tb.cell(1, 0).text_frame, theme)
                _kadai_set_text(tb.cell(0, 1).text_frame, "チームメンバ")

    # 2枚目以降：見出しを書き込んだ1枚目の図形をそのままコピーする（レイアウトの空のタイトル枠は消す）
    import copy as _copy
    pages = [slide]
    for _ in range(len(slides_in) - 1):
        ns = prs.slides.add_slide(slide.slide_layout)
        for shp in list(ns.shapes):
            shp._element.getparent().remove(shp._element)
        for shp in slide.shapes:
            ns.shapes._spTree.append(_copy.deepcopy(shp._element))
        pages.append(ns)
    # ページ番号（フィールドなのでPowerPointで開くと自動で直るが、念のため表示も合わせる）
    for no, pg in enumerate(pages, 1):
        for shp in pg.shapes:
            if shp.is_placeholder and shp.has_text_frame:
                for t in shp._element.iter("{http://schemas.openxmlformats.org/drawingml/2006/main}t"):
                    t.text = str(no)

    # カードとテキストボックス（画面上の位置 x,y,w は自由スペースに対する%）
    for pg, sd in zip(pages, slides_in):
        if is_design:
            for shp in pg.shapes:
                if shp.has_text_frame and not shp.is_placeholder and "__PHASE__" in shp.text_frame.text:
                    _kadai_set_text(shp.text_frame, str(sd.get("label") or ""))
                    for r in shp.text_frame.paragraphs[0].runs:
                        r.font.size = Pt(18)
            for ar in (sd.get("areas") or [])[:12]:
                _kadai_add_area(pg, ar, Emu, Pt, RGBColor, MSO_SHAPE)
        for c in (sd.get("cards") or [])[:80]:
            _kadai_add_card(pg, c, Emu, Pt, RGBColor, MSO_SHAPE, MSO_ANCHOR)
        for t in (sd.get("texts") or [])[:80]:
            _kadai_add_text(pg, t, Emu, Pt, RGBColor)

    buf = io.BytesIO()
    prs.save(buf)
    buf.seek(0)
    name = (f"デザイン思考" if is_design else f"課題{_safe_filename_part(kadai_no)}") + (f"_{_safe_filename_part(group)}班" if group else "") + ".pptx"
    cd = f"attachment; filename=\"kadai.pptx\"; filename*=UTF-8''{urlquote(name, safe='')}"
    return Response(buf.read(),
                    mimetype="application/vnd.openxmlformats-officedocument.presentationml.presentation",
                    headers={"Content-Disposition": cd})


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


# =========================
# エントリポイント
# =========================
if __name__ == "__main__":
    # Railway では PORT 環境変数が渡されるので、それがあれば使う
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
