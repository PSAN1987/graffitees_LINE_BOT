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
    TextMessageContent,
    PostbackEvent
)

import logging
import traceback
import json

# 環境変数の読み込み
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

def get_db_connection():
    """データベース接続関数"""
    return psycopg2.connect(
        dbname=DATABASE_NAME,
        user=DATABASE_USER,
        password=DATABASE_PASSWORD,
        host=DATABASE_HOST,
        port=DATABASE_PORT
    )

@app.route("/", methods=["GET"])
def health_check():
    """Render Health Check 用"""
    return "OK", 200

@app.route("/callback", methods=['POST'])
def callback():
    """LINE Messaging API Webhook エンドポイント"""
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

def create_flex_message():
    """
    Flex Message（辞書形式）を作成して返す関数。
    3つのボタンを含むメニューを表示する例。
    """
    bubble_dict = {
        "type": "bubble",
        "body": {
            "type": "box",
            "layout": "vertical",
            "contents": [
                {
                    "type": "text",
                    "text": "モードを選択してください!",
                    "weight": "bold",
                    "size": "lg",
                    "wrap": True
                }
            ]
        },
        "footer": {
            "type": "box",
            "layout": "vertical",
            "contents": [
                {
                    "type": "button",
                    "style": "primary",
                    "action": {
                        "type": "postback",
                        "label": "簡易見積",
                        "data": "quick_estimate"
                    }
                },
                {
                    "type": "button",
                    "style": "primary",
                    "action": {
                        "type": "postback",
                        "label": "WEBフォームから注文",
                        "data": "web_order"
                    }
                },
                {
                    "type": "button",
                    "style": "primary",
                    "action": {
                        "type": "postback",
                        "label": "注文用紙から注文",
                        "data": "paper_order"
                    }
                }
            ]
        }
    }

    return FlexMessage(
        alt_text="モードを選択してください",
        contents=bubble_dict
    )

@handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
    """
    テキストメッセージ受信時のハンドラ。
    ユーザーが「モード選択」と入力した場合に FlexMessage を返し、
    そうでない場合は通常のテキストメッセージを返す。
    """
    user_input = event.message.text.strip()

    if user_input == "モード選択":
        reply_message = create_flex_message()
    else:
        reply_message = TextMessage(text=f"あなたのメッセージ: {user_input}")

    body = ReplyMessageRequest(
        reply_token=event.reply_token,
        messages=[reply_message]
    )
    messaging_api.reply_message(body)


@handler.add(PostbackEvent)
def handle_postback(event):
    """
    ボタンが押された際の PostbackEvent を処理するハンドラ。
    data に応じて分岐してメッセージを返す。
    """
    data = event.postback.data

    if data == "quick_estimate":
        response_text = "簡易見積モードを選択しました。"
    elif data == "web_order":
        response_text = "WEBフォームからの注文を選択しました。"
    elif data == "paper_order":
        response_text = "注文用紙からの注文を選択しました。"
    else:
        response_text = f"不明なモード: {data}"

    # 通常のテキストメッセージで返信
    reply_message = TextMessage(text=response_text)
    body = ReplyMessageRequest(
        reply_token=event.reply_token,
        messages=[reply_message]
    )
    messaging_api.reply_message(body)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=True)
