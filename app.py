import os
import tempfile
import json
import base64
import re
import math
import subprocess
import traceback
from datetime import datetime
from collections import defaultdict
from pydub import AudioSegment

from flask import Flask, request, jsonify, send_from_directory
from dotenv import load_dotenv
from openai import OpenAI
from pyannote.audio import Pipeline
import torch
import soundfile as sf

# =========================
# 環境変数・クライアント準備
# =========================
load_dotenv()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

if not OPENAI_API_KEY:
    raise RuntimeError("OPENAI_API_KEY が .env に設定されていません。")

# =========================
# pyannote パイプラインキャッシュ
# =========================
_diarization_pipeline = None

def get_diarization_pipeline():
    global _diarization_pipeline
    if _diarization_pipeline is None:
        _diarization_pipeline = Pipeline.from_pretrained(
            "pyannote/speaker-diarization-3.1",
            token=os.getenv("HUGGINGFACE_TOKEN")
        )
    return _diarization_pipeline

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
        "- さぐる：thought=factを中心に、現状・観察を多めに抽出\n"
        "- きづく：thought=problem/interpretを中心に抽出\n"
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
    音声ファイルをWhisper + pyannoteで処理し、
    話者情報付きのテキストと発言量サマリーを返す。
    """
    if "audio" not in request.files:
        return jsonify({"ok": False, "error": "no audio file"}), 400

    audio_file = request.files["audio"]
    suffix = os.path.splitext(audio_file.filename)[1] or ".webm"

    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp_path = tmp.name
        audio_file.save(tmp_path)

    wav_path = tmp_path + ".wav"
    try:
        result = subprocess.run([
            "ffmpeg", "-i", tmp_path,
            "-ar", "16000", "-ac", "1",
            wav_path, "-y"
        ], capture_output=True, text=True)
        if result.returncode != 0:
            print("ffmpeg error:", result.stderr)
            raise Exception("ffmpeg変換失敗: " + result.stderr[-200:])

        pipeline = get_diarization_pipeline()
        data, sample_rate = sf.read(wav_path)
        waveform = torch.tensor(data).unsqueeze(0).float()
        audio_input = {"waveform": waveform, "sample_rate": sample_rate}
        diarization = pipeline(audio_input).speaker_diarization

        totals = defaultdict(float)
        counts = defaultdict(int)
        segments = []
        for turn, _, speaker in diarization.itertracks(yield_label=True):
            totals[speaker] += turn.end - turn.start
            counts[speaker] += 1
            segments.append({
                "start": round(turn.start, 2),
                "end": round(turn.end, 2),
                "speaker": speaker
            })

        total_time = sum(totals.values())
        speaker_summary = []
        for sp in sorted(totals.keys()):
            pct = totals[sp] / total_time * 100 if total_time > 0 else 0
            speaker_summary.append({
                "speaker": sp,
                "duration": round(totals[sp], 1),
                "percentage": round(pct, 1),
                "count": counts[sp],
                "label": sp
            })

        text = (split_and_transcribe(tmp_path, suffix) or "").strip()

        return jsonify({
            "ok": True,
            "text": text,
            "segments": segments,
            "speaker_summary": speaker_summary,
            "num_speakers": len(totals)
        })

    except Exception as e:
        print("transcribe_with_speakers error:", traceback.format_exc())
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
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
# 自動配置 API
# =========================
def build_layout_prompt(phase, cards, canvas_width, canvas_height, areas=None):
    system_prompt = "あなたはワークシートへのカード配置の専門家です。"

    # phaseに応じてarea_guideを切り替える
    if phase in ("saguru", "kizuku"):
        area_guide = """
【絶対ルール：saguru/kizukuフェーズで使えるエリアのみ選ぶ】

area-target：
  人物・ターゲットに関するもの
  例）「お母さんが困っている」「中学生が〜」

area-fact：
  thought=fact または thought=problem のもの
  現状・観察・困っていること・事実の報告
  例）「混乱する」「リモコンを失くす」「ボタンが多すぎる」

area-wish：
  thought=wish かつ 「こうしたい・こうあるべき」
  という純粋な願望・要望を表すもの
  例）「シンプルで直感的にしたい」「使いやすくしてほしい」

area-interpret：
  thought=interpret のもの、
  または type=idea / type=reaction / type=opinion で
  thought=wish のもの（アイデアや反応は解釈エリアへ）
  例）「シンプルなインターフェース（アイデア）」
      「いいアイデア（reaction）」
      「追跡機能（idea）」
      「役立つ（reaction）」

【判断のポイント】
・type=ideaのカードは、さぐるフェーズでは
  area-interpretに入れる
  （アイデアはさぐる段階では「解釈・仮説」として扱う）
・type=reactionのカードは area-interpret に入れる
・思い切り「〜したい」という願望表現のみ area-wish に入れる
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
    音声ファイルの指定した時間範囲だけを処理して返す。
    chunk_start, chunk_end: 秒数（form フィールド）
    num_speakers: 話者数ヒント
    """
    if "audio" not in request.files:
        return jsonify({"ok": False, "error": "no audio file"}), 400

    audio_file = request.files["audio"]
    chunk_start  = float(request.form.get("chunk_start", 0))
    chunk_end    = float(request.form.get("chunk_end", 300))
    num_speakers = int(request.form.get("num_speakers", 2))
    suffix = os.path.splitext(audio_file.filename)[1] or ".webm"

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

        pipeline = get_diarization_pipeline()
        data, sample_rate = sf.read(wav_path)
        waveform = torch.tensor(data).unsqueeze(0).float()
        audio_input = {"waveform": waveform, "sample_rate": sample_rate}
        if num_speakers >= 2:
            diarization = pipeline(audio_input, num_speakers=num_speakers).speaker_diarization
        else:
            diarization = pipeline(audio_input).speaker_diarization

        segments = []
        totals = defaultdict(float)
        counts = defaultdict(int)
        for turn, _, speaker in diarization.itertracks(yield_label=True):
            real_start = round(turn.start + chunk_start, 2)
            real_end   = round(turn.end   + chunk_start, 2)
            segments.append({"start": real_start, "end": real_end, "speaker": speaker})
            totals[speaker] += turn.end - turn.start
            counts[speaker] += 1

        with open(wav_path, "rb") as f:
            whisper_res = client.audio.transcriptions.create(
                model="whisper-1",
                file=f,
                language="ja"
            )
        text = (whisper_res.text or "").strip()

        total_time = sum(totals.values())
        speaker_summary = []
        for sp in sorted(totals.keys()):
            pct = totals[sp] / total_time * 100 if total_time > 0 else 0
            speaker_summary.append({
                "speaker": sp,
                "duration": round(totals[sp], 1),
                "percentage": round(pct, 1),
                "count": counts[sp]
            })

        return jsonify({
            "ok": True,
            "text": text,
            "segments": segments,
            "speaker_summary": speaker_summary,
            "chunk_start": chunk_start,
            "chunk_end": chunk_end
        })

    except Exception as e:
        print("transcribe_chunk error:", traceback.format_exc())
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
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
# エントリポイント
# =========================
if __name__ == "__main__":
    # Railway では PORT 環境変数が渡されるので、それがあれば使う
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
