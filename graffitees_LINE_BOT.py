import os
import psycopg2
import requests
from dotenv import load_dotenv
from flask import Flask, request, abort
from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    ApiClient, Configuration, MessagingApi,
    ReplyMessageRequest, TextMessage, FlexMessage
)
from linebot.v3.webhooks import MessageEvent, TextMessageContent
import logging
import traceback
import json

# 環境変数を読み込む
load_dotenv()

# 環境設定
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
        dbname=DATABASE_NAME, user=DATABASE_USER,
        password=DATABASE_PASSWORD, host=DATABASE_HOST, port=DATABASE_PORT
    )

# ルートエンドポイント (Render Health Check 用)
@app.route("/", methods=["GET"])
def health_check():
    return "OK", 200

# LINE Webhook用エンドポイント
@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers.get('X-Line-Signature', '')
    if not signature:
        abort(400)

    try:
        handler.handle(request.get_data(as_text=True), signature)
    except InvalidSignatureError:
        abort(400)
    
    return 'OK', 200

# Flex Messageテンプレートを作成
def create_flex_message():
    flex_content = {
        "type": "bubble",
        "body": {
            "type": "box",
            "layout": "vertical",
            "contents": [
                {"type": "text", "text": "モードを選択してください", "weight": "bold", "size": "lg"},
                {
                    "type": "button", "style": "primary", 
                    "action": {"type": "postback", "label": "簡易見積", "data": "quick_estimate"}
                },
                {
                    "type": "button", "style": "primary", 
                    "action": {"type": "postback", "label": "WEBフォームから注文", "data": "web_order"}
                },
                {
                    "type": "button", "style": "primary", 
                    "action": {"type": "postback", "label": "注文用紙から注文", "data": "paper_order"}
                }
            ]
        }
    }
    return FlexMessage(alt_text="モードを選択してください", contents=flex_content)

# メッセージイベントの処理
@handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
    user_input = event.message.text.strip()
    if user_input == "モード選択":
        reply_message = create_flex_message()
    else:
        reply_message = TextMessage(text=f"あなたのメッセージ: {user_input}")
    messaging_api.reply_message(ReplyMessageRequest(reply_token=event.reply_token, messages=[reply_message]))

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))  # Render のポートに合わせる

    # Flaskアプリの起動
    app.run(host="0.0.0.0", port=port)
