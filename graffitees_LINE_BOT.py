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

# ���ϐ���ǂݍ���
load_dotenv()

# ���ݒ�
CHANNEL_ACCESS_TOKEN = os.getenv('CHANNEL_ACCESS_TOKEN')
CHANNEL_SECRET = os.getenv('CHANNEL_SECRET')
DATABASE_NAME = os.getenv('DATABASE_NAME')
DATABASE_USER = os.getenv('DATABASE_USER')
DATABASE_PASSWORD = os.getenv('DATABASE_PASSWORD')
DATABASE_HOST = os.getenv('DATABASE_HOST')
DATABASE_PORT = os.getenv('DATABASE_PORT')

# Flask�A�v���̏�����
app = Flask(__name__)

# ���O�ݒ�
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# LINE Bot API �ݒ�
config = Configuration(access_token=CHANNEL_ACCESS_TOKEN)
api_client = ApiClient(configuration=config)
messaging_api = MessagingApi(api_client=api_client)
handler = WebhookHandler(CHANNEL_SECRET)

# �f�[�^�x�[�X�ڑ��֐�
def get_db_connection():
    return psycopg2.connect(
        dbname=DATABASE_NAME, user=DATABASE_USER,
        password=DATABASE_PASSWORD, host=DATABASE_HOST, port=DATABASE_PORT
    )

# Flex Message�e���v���[�g���쐬
def create_flex_message():
    flex_content = {
        "type": "bubble",
        "body": {
            "type": "box",
            "layout": "vertical",
            "contents": [
                {"type": "text", "text": "�������@��I�����Ă�������", "weight": "bold", "size": "lg"},
                {"type": "button", "style": "primary", "action": {"type": "postback", "label": "�ȈՌ���", "data": "quick_estimate"}},
                {"type": "button", "style": "primary", "action": {"type": "postback", "label": "WEB�t�H�[�����璍��", "data": "web_order"}},
                {"type": "button", "style": "primary", "action": {"type": "postback", "label": "�����p�����璍��", "data": "paper_order"}}
            ]
        }
    }
    return FlexMessage(alt_text="�������@��I�����Ă�������", contents=flex_content)

# ���b�`���j���[���쐬
def create_rich_menu():
    rich_menu = {
        "size": {"width": 2500, "height": 843},
        "selected": True,
        "name": "Order Menu",
        "chatBarText": "���j���[���J��",
        "areas": [
            {"bounds": {"x": 0, "y": 0, "width": 833, "height": 843}, "action": {"type": "postback", "data": "quick_estimate", "displayText": "�ȈՌ���"}},
            {"bounds": {"x": 833, "y": 0, "width": 833, "height": 843}, "action": {"type": "postback", "data": "web_order", "displayText": "WEB�t�H�[�����璍��"}},
            {"bounds": {"x": 1666, "y": 0, "width": 833, "height": 843}, "action": {"type": "postback", "data": "paper_order", "displayText": "�����p�����璍��"}}
        ]
    }

    headers = {
        "Authorization": f"Bearer {CHANNEL_ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }

    url = "https://api.line.me/v2/bot/richmenu"
    response = requests.post(url, headers=headers, json=rich_menu)

    if response.status_code == 200:
        rich_menu_id = response.json()["richMenuId"]
        return rich_menu_id
    return None

# ���b�`���j���[��K�p
def set_rich_menu_for_users(rich_menu_id):
    headers = {"Authorization": f"Bearer {CHANNEL_ACCESS_TOKEN}"}
    url = f"https://api.line.me/v2/bot/user/all/richmenu/{rich_menu_id}"
    requests.post(url, headers=headers)

# ���b�Z�[�W�C�x���g�̏���
@handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
    user_input = event.message.text.strip()
    reply_message = create_flex_message() if user_input == "����" else TextMessage(text="����������ꍇ�́u�����v�Ƒ��M���Ă��������B")
    messaging_api.reply_message(ReplyMessageRequest(reply_token=event.reply_token, messages=[reply_message]))

# LINE��Webhook���N�G�X�g������
@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
    try:
        handler.handle(request.get_data(as_text=True), signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'

if __name__ == "__main__":
    rich_menu_id = create_rich_menu()
    if rich_menu_id:
        set_rich_menu_for_users(rich_menu_id)
    app.run(port=8000)

