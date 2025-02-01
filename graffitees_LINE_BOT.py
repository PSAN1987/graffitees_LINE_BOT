import os
import psycopg2
import requests
from dotenv import load_dotenv
from flask import Flask, request, abort
import logging
import traceback
import json

# ★★★ line-bot-sdk v2 系 ★★★
from linebot import (
    LineBotApi,
    WebhookHandler
)
from linebot.exceptions import InvalidSignatureError
from linebot.models import (
    MessageEvent,
    PostbackEvent,
    TextMessage,
    TextSendMessage,
    PostbackAction,
    FlexSendMessage,
    BubbleContainer,
    BoxComponent,
    TextComponent,
    ButtonComponent
)

load_dotenv()

CHANNEL_ACCESS_TOKEN = os.getenv('CHANNEL_ACCESS_TOKEN')
CHANNEL_SECRET = os.getenv('CHANNEL_SECRET')

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

line_bot_api = LineBotApi(CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(CHANNEL_SECRET)

# ◆ データを一時保存するための簡易的な辞書 (本番ではDB管理がおすすめ)
#   key: ユーザーID, value: dict(状態や入力内容を保存)
user_states = {}

@app.route("/", methods=["GET"])
def health_check():
    return "OK", 200

@app.route("/callback", methods=["POST"])
def callback():
    signature = request.headers.get("X-Line-Signature")
    if not signature:
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

    return "OK", 200

def create_mode_selection_flex():
    """
    3つのモードボタンを含むメニューを表示する Flex Message
    （「簡易見積」「WEBフォームから注文」「注文用紙から注文」）
    """
    bubble = BubbleContainer(
        body=BoxComponent(
            layout='vertical',
            contents=[
                TextComponent(
                    text='モードを選択してください!',
                    weight='bold',
                    size='lg'
                )
            ]
        ),
        footer=BoxComponent(
            layout='vertical',
            contents=[
                ButtonComponent(
                    style='primary',
                    action=PostbackAction(label='簡易見積', data='quick_estimate')
                ),
                ButtonComponent(
                    style='primary',
                    action=PostbackAction(label='WEBフォームから注文', data='web_order')
                ),
                ButtonComponent(
                    style='primary',
                    action=PostbackAction(label='注文用紙から注文', data='paper_order')
                )
            ]
        )
    )

    return FlexSendMessage(
        alt_text='モードを選択してください',
        contents=bubble
    )

def create_quick_estimate_intro_flex():
    """
    簡易見積モードへ入ったときに最初に表示するFlex。
    全8項目をリストアップし、「入力を開始する」ボタンを1つ用意する例。
    """
    bubble = BubbleContainer(
        body=BoxComponent(
            layout='vertical',
            contents=[
                TextComponent(
                    text='簡易見積に必要な項目を順番に確認します。',
                    weight='bold',
                    size='md',
                    wrap=True
                ),
                TextComponent(
                    text='1. 学校または団体名\n'
                         '2. お届け先(都道府県)\n'
                         '3. 早割確認(14日前以上/14日前以内)\n'
                         '4. 1枚当たりの予算\n'
                         '5. 商品名(複数から選択)\n'
                         '6. 枚数\n'
                         '7. プリント位置(前/背中/前と背中)\n'
                         '8. 使用する色数(カラー追加など)',
                    size='sm',
                    wrap=True
                )
            ]
        ),
        footer=BoxComponent(
            layout='vertical',
            contents=[
                ButtonComponent(
                    style='primary',
                    action=PostbackAction(label='入力を開始する', data='start_quick_estimate_input')
                )
            ]
        )
    )
    return FlexSendMessage(
        alt_text='簡易見積モード',
        contents=bubble
    )

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    """
    テキストメッセージを受け取った時の処理。
    """
    user_id = event.source.user_id
    user_input = event.message.text.strip()
    logger.info(f"user_input: {user_input}")

    # (A) 「モード選択」と入力した場合 → 3つのモードボタンを出す
    if user_input == "モード選択":
        flex_msg = create_mode_selection_flex()
        line_bot_api.reply_message(event.reply_token, flex_msg)
        return

    # (B) ユーザーが現在「学校名入力待ち」などの状態か確認
    if user_id in user_states:
        state_data = user_states[user_id]
        if state_data.get("state") == "await_school_name":
            # 学校名を受け取り、保存
            state_data["school_name"] = user_input
            # 次のステップへ進行 (例として都道府県入力を促す)
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="学校名を保存しました。\n次にお届け先(都道府県)を入力してください。")
            )
            # 状態を都道府県待ちに変更
            state_data["state"] = "await_prefecture"
            return
        elif state_data.get("state") == "await_prefecture":
            # 都道府県を受け取り、保存
            state_data["prefecture"] = user_input
            # 次のステップ → 早割確認など
            # 本来はボタン選択などが良いが省略
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="都道府県を保存しました。(以下省略)\n他の項目も同様に実装してください。")
            )
            state_data["state"] = "done"
            return

    # (C) それ以外は普通のメッセージ
    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text=f"あなたのメッセージ: {user_input}")
    )

@handler.add(PostbackEvent)
def handle_postback(event):
    """
    ボタンが押された際の Postback イベントを処理。
    """
    user_id = event.source.user_id
    data = event.postback.data
    logger.info(f"Postback data: {data}")

    if data == "quick_estimate":
        # 簡易見積モードに入る → 初回の案内Flexを送る
        flex_msg = create_quick_estimate_intro_flex()
        line_bot_api.reply_message(event.reply_token, flex_msg)
        return

    if data == "start_quick_estimate_input":
        # 入力開始 → まずは学校または団体名を聞く
        # ユーザー状態を記録
        user_states[user_id] = {
            "state": "await_school_name"
        }
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="わかりました。\nまずは学校または団体名を入力してください。")
        )
        return

    elif data == "web_order":
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="WEBフォームからの注文モードは未実装です。")
        )
        return

    elif data == "paper_order":
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="注文用紙からの注文モードは未実装です。")
        )
        return

    # 想定外のデータ
    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text=f"不明なモード: {data}")
    )

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=True)
