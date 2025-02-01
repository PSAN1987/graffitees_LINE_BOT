import os
import psycopg2
import requests
from dotenv import load_dotenv
from flask import Flask, request, abort

# linebot v3 関連インポート
from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    ApiClient,
    Configuration,
    MessagingApi,
    ReplyMessageRequest,
    TextMessage,
    FlexMessage
)
from linebot.v3.webhooks import (
    MessageEvent,
    TextMessageContent
)

# ★ Flex関連のクラスは v3.types.flex_message / action からインポート ★
from linebot.v3.types.flex_message import (
    Bubble,
    Box,
    Text,
    Button
)
from linebot.v3.types.action import PostbackAction

import logging
import traceback
import json

# 環境変数を読み込む
load_dotenv()

# 環境変数から各種情報を取得
CHANNEL_ACCESS_TOKEN = os.getenv('CHANNEL_ACCESS_TOKEN')
CHANNEL_SECRET = os.getenv('CHANNEL_SECRET')
DATABASE_NAME = os.getenv('DATABASE_NAME')
DATABASE_USER = os.getenv('DATABASE_USER')
DATABASE_PASSWORD = os.getenv('DATABASE_PASSWORD')
DATABASE_HOST = os.getenv('DATABASE_HOST')
DATABASE_PORT = os.getenv('DATABASE_PORT')

# Flaskアプリの初期化
app = Flask(__name__)

# ログ設定
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# LINE Bot API 設定
config = Configuration(access_token=CHANNEL_ACCESS_TOKEN)
api_client = ApiClient(configuration=config)
messaging_api = MessagingApi(api_client=api_client)
handler = WebhookHandler(CHANNEL_SECRET)

# データベース接続関数
def get_db_connection():
    return psycopg2.connect(
        dbname=DATABASE_NAME,
        user=DATABASE_USER,
        password=DATABASE_PASSWORD,
        host=DATABASE_HOST,
        port=DATABASE_PORT
    )

# ルートエンドポイント (Render Health Check 用)
@app.route("/", methods=["GET"])
def health_check():
    return "OK", 200

# Webhookエンドポイント
@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers.get('X-Line-Signature', '')
    if not signature:
        abort(400)

    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError as e:
        logger.error(f"Invalid signature. Error: {e}")
        abort(400)
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        traceback.print_exc()
        abort(500)

    return 'OK', 200

# Flex Message作成用関数 (v3対応)
def create_flex_message():
    """
    LINE Bot SDK v3対応のFlex Messageの例
    """
    bubble = Bubble(
        body=Box(
            layout='vertical',
            contents=[
                Text(
                    text='モードを選択してください',
                    weight='bold',
                    size='lg',
                    wrap=True
                )
            ]
        ),
        footer=Box(
            layout='vertical',
            contents=[
                Button(
                    style='primary',
                    action=PostbackAction(
                        label='簡易見積',
                        data='quick_estimate'
                    )
                ),
                Button(
                    style='primary',
                    action=PostbackAction(
                        label='WEBフォームから注文',
                        data='web_order'
                    )
                ),
                Button(
                    style='primary',
                    action=PostbackAction(
                        label='注文用紙から注文',
                        data='paper_order'
                    )
                )
            ]
        )
    )

    # FlexMessageを返す
    flex_message = FlexMessage(
        alt_text='モードを選択してください',
        contents=bubble
    )
    return flex_message

# メッセージイベントのハンドラ
@handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
    user_input = event.message.text.strip()

    if user_input == "モード選択":
        # FlexMessage を返す
        reply_message = create_flex_message()
    else:
        # 通常のテキストメッセージ
        reply_message = TextMessage(text=f"あなたのメッセージ: {user_input}")

    # v3 では ReplyMessageRequest に v3 のモデルをリストで渡す
    body = ReplyMessageRequest(
        reply_token=event.reply_token,
        messages=[reply_message]
    )
    messaging_api.reply_message(body)

if __name__ == "__main__":
    # gunicorn で起動するなら下記不要ですが、ローカル実行テスト用に残します
    app.run(host="0.0.0.0", port=8000, debug=True)
