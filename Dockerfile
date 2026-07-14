# Python 3.10ベース（開発環境と合わせる）
FROM python:3.10-slim

# システムレベルの依存関係（ffmpeg: 音声変換に必須）
RUN apt-get update && apt-get install -y \
    ffmpeg \
    libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 依存関係を先にコピーしてインストール（キャッシュ活用のため）
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# アプリ本体をコピー
COPY . .

# Renderは環境変数PORTでポートを指定してくる
ENV PORT=10000
# 永続ディスクのマウントパスに合わせる（Render disk は /data にマウント）
ENV DATA_DIR=/data
EXPOSE 10000

# Gunicornで起動（本番用サーバー）
# --timeout 300: 音声処理は時間がかかるため長めに設定
# --workers 2: メモリ消費とのバランスを見て調整可能
CMD gunicorn -w 1 --threads 4 --timeout 300 -b 0.0.0.0:$PORT app:app
