import os
import psycopg2
import requests
from dotenv import load_dotenv
from flask import Flask, request, abort
import logging
import traceback
import json

# line-bot-sdk v2 系
from linebot import (
    LineBotApi,
    WebhookHandler
)
from linebot.exceptions import (
    InvalidSignatureError
)
from linebot.models import (
    MessageEvent,
    PostbackEvent,
    TextMessage,
    TextSendMessage,
    FlexSendMessage,
    BubbleContainer,
    BoxComponent,
    TextComponent,
    ButtonComponent,
    PostbackAction
)

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

app = Flask(__name__)

# ログ設定
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# LINE Bot API 設定 (v2)
line_bot_api = LineBotApi(CHANNEL_ACCESS_TOKEN)
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

@app.route("/", methods=["GET"])
def health_check():
    # Render などでのヘルスチェック用
    return "OK", 200

@app.route("/callback", methods=["POST"])
def callback():
    # Webhook 署名検証
    signature = request.headers.get('X-Line-Signature')
    if signature is None:
        abort(400)

    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError as e:
        logger.error(f"InvalidSignatureError: {e}")
        abort(400)
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        traceback.print_exc()
        abort(500)

    return 'OK', 200

def create_flex_message():
    """
    3つのボタンを含むメニューを表示する Flex Message (v2)
    """
    bubble = BubbleContainer(
        body=BoxComponent(
            layout='vertical',
            contents=[
                TextComponent(
                    text='モードを選択してください!',
                    weight='bold',
                    size='lg',
                    wrap=True
                )
            ]
        ),
        footer=BoxComponent(
            layout='vertical',
            contents=[
                ButtonComponent(
                    style='primary',
                    action=PostbackAction(
                        label='簡易見積',
                        data='quick_estimate'
                    )
                ),
                ButtonComponent(
                    style='primary',
                    action=PostbackAction(
                        label='WEBフォームから注文',
                        data='web_order'
                    )
                ),
                ButtonComponent(
                    style='primary',
                    action=PostbackAction(
                        label='注文用紙から注文',
                        data='paper_order'
                    )
                )
            ]
        )
    )

    return FlexSendMessage(
        alt_text='モードを選択してください',
        contents=bubble
    )

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    """
    テキストメッセージを受け取った時の処理
    """
    user_input = event.message.text.strip()
    if user_input == "モード選択":
        # Flex Message を返信
        reply_message = create_flex_message()
    else:
        # 通常のテキストメッセージを返信
        reply_message = TextSendMessage(text=f"あなたのメッセージ: {user_input}")

    line_bot_api.reply_message(event.reply_token, reply_message)

@handler.add(PostbackEvent)
def handle_postback(event):
    """
    ボタンが押された際の Postback イベントを処理
    """
    data = event.postback.data
    if data == "quick_estimate":
        response_text = "簡易見積モードを選択しました。"
    elif data == "web_order":
        response_text = "WEBフォームからの注文を選択しました。"
    elif data == "paper_order":
        response_text = "注文用紙からの注文を選択しました。"
    else:
        response_text = f"不明なモードです: {data}"

    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text=response_text)
    )

if __name__ == "__main__":
    # ローカルテストで Flask を起動する場合のみ必要
    app.run(host="0.0.0.0", port=8000, debug=True)
