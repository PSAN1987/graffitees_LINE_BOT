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

from linebot.v3.messaging import (
    FlexMessage,
    BubbleContainer,
    BoxComponent,
    TextComponent,
    ButtonComponent,
    PostbackAction
)

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
from linebot.models import FlexSendMessag
def create_flex_message():
    # "bubble" コンテナを作る
    bubble = BubbleContainer(
        body=BoxComponent(
            layout='vertical',
            contents=[
                TextComponent(
                    text='モードを選択してください',
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

    # FlexMessage を生成
    flex_message = FlexMessage(
        alt_text='モードを選択してください',
        contents=bubble
    )
    return flex_message

# メッセージイベントの処理
@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    handler.handle(body, signature)
    return 'OK', 200


@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_input = event.message.text.strip()

    # 「モード選択」と入力された場合だけ Flex メッセージを返す例
    if user_input == "モード選択":
        reply_message = create_flex_message()
    else:
        # 通常のテキストメッセージを返す
        reply_message = TextMessage(text=f"あなたのメッセージ: {user_input}")

    # v3 では ReplyMessageRequest に Pydantic モデルのリストを渡す
    body = ReplyMessageRequest(
        reply_token=event.reply_token,
        messages=[reply_message]
    )
    messaging_api.reply_message(body)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=True)
