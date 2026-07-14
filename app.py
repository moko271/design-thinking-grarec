import os
import io
import shutil
import tempfile
import json
import base64
import re
import math
import zipfile
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


# =========================
# プロンプト生成（keyword + quote 用）
# =========================
def build_prompt(memo: str, phase: str, prev_text: str = "") -> tuple[str, str]:
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
        "【抽出ルール】\n"
        "・必ず最低10件、できれば15〜20件抽出する\n"
        "・短いテキストでも1文ごとに1件を目安に抽出する\n"
        "・些細に見えても話し合いの記録として価値ある発言は全て含める\n"
        "・前の文脈を考慮して意図を判断する\n"
        "・話者が変わるタイミングも意識して分類する\n\n"
        "【抽出しない発言】\n"
        "・雑談・世間話（例：お腹すいた・休憩しよう・今日暑いね 等）\n"
        "・単純な相槌のみの発言（例：そうだね・なるほど・うん・はい 等）\n"
        "・役割確認・作業分担のみの発言（例：じゃあAがやる・私が書く 等）\n"
        "・笑いや感嘆のみ（例：笑・えー・あはは・すごーい 等）\n\n"
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

    # メモを改行で分割して、最後の5行を前文脈、残りを本文とする
    lines = memo.strip().split("\n")
    if len(lines) > 5:
        prev_text = "\n".join(lines[-5:])
        memo_text = "\n".join(lines[:-5])
    else:
        prev_text = memo
        memo_text = ""

    system_prompt, user_prompt = build_prompt(memo_text, phase, prev_text)

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
                            "thought": item.get("thought", "fact").strip().lower()
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
def split_and_transcribe(file_path, suffix):
    """5分超の音声を5分チャンクに分割してWhisperに送り、結果を結合する。"""
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
                language="ja"
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
                    language="ja"
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

    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp_path = tmp.name
        audio_file.save(tmp_path)

    try:
        text = (split_and_transcribe(tmp_path, suffix) or "").strip()

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
                language="ja"
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
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return jsonify({"ok": True, "session_id": session_id})
    except Exception as e:
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
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(project, f, ensure_ascii=False, indent=2)
        return jsonify({"ok": True, "group_number": gn})
    except Exception as e:
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


@app.route("/api/list_sessions", methods=["GET"])
@require_admin
def list_sessions():
    try:
        sessions = []
        for fname in sorted(os.listdir(SESSIONS_DIR), reverse=True):
            if not fname.endswith(".json"):
                continue
            fpath = os.path.join(SESSIONS_DIR, fname)
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    d = json.load(f)
                metrics = _compute_metrics(d)
                sessions.append({
                    "session_id":      d.get("session_id", fname[:-5]),
                    "group_number":    d.get("group_number", ""),
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
# エントリポイント
# =========================
if __name__ == "__main__":
    # Railway では PORT 環境変数が渡されるので、それがあれば使う
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
