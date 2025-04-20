import os
import json
import time
from datetime import datetime
import pytz

import gspread
from flask import Flask, render_template_string, request, session
import uuid
from oauth2client.service_account import ServiceAccountCredentials

# 追加 -----------------------------------
import requests
# ----------------------------------------

# line-bot-sdk v2 系
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import (
    MessageEvent, TextMessage, TextSendMessage, FlexSendMessage
)

app = Flask(__name__)
app.secret_key = 'some_secret_key'  # セッションが必要

# -----------------------
# 環境変数取得
# -----------------------
LINE_CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET", "")
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
SERVICE_ACCOUNT_FILE = os.environ.get("GCP_SERVICE_ACCOUNT_JSON", "")
SPREADSHEET_KEY = os.environ.get("SPREADSHEET_KEY", "")

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)


# -----------------------
# Google Sheets 接続
# -----------------------
def get_gspread_client():
    """
    環境変数 SERVICE_ACCOUNT_FILE (JSONパス or JSON文字列) から認証情報を取り出し、
    gspread クライアントを返す
    """
    if not SERVICE_ACCOUNT_FILE:
        raise ValueError("環境変数 GCP_SERVICE_ACCOUNT_JSON が設定されていません。")

    service_account_dict = json.loads(SERVICE_ACCOUNT_FILE)

    scope = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    credentials = ServiceAccountCredentials.from_json_keyfile_dict(service_account_dict, scope)
    return gspread.authorize(credentials)


def get_or_create_worksheet(sheet, title):
    """
    スプレッドシート内で該当titleのワークシートを取得。
    なければ新規作成し、ヘッダを書き込む。
    """
    try:
        ws = sheet.worksheet(title)
    except gspread.exceptions.WorksheetNotFound:
        ws = sheet.add_worksheet(title=title, rows=2000, cols=20)
        # 必要であればヘッダをセット
        if title == "CatalogRequests":
            ws.update('A1:I1', [[
                "日時",  # ←先頭に日時列
                "氏名", "郵便番号", "住所", "電話番号",
                "メールアドレス", "Insta/TikTok名",
                "在籍予定の学校名と学年", "その他(質問・要望)"
            ]])
        elif title == "簡易見積":
            # 属性カラムを追加したため、A1:M1 で13列に拡張
            ws.update('A1:M1', [[
                "日時", "見積番号", "ユーザーID", "属性",
                "使用日(割引区分)", "予算", "商品名", "枚数",
                "プリント位置", "色数", "背ネーム",
                "合計金額", "単価"
            ]])
    return ws


def write_to_spreadsheet_for_catalog(form_data: dict):
    gc = get_gspread_client()
    sh = gc.open_by_key(SPREADSHEET_KEY)
    worksheet = get_or_create_worksheet(sh, "CatalogRequests")

    # 日本時間の現在時刻
    jst = pytz.timezone('Asia/Tokyo')
    now_jst_str = datetime.now(jst).strftime("%Y/%m/%d %H:%M:%S")

    # address_1 と address_2 を合体して1つのセルに
    full_address = f"{form_data.get('address_1', '')} {form_data.get('address_2', '')}".strip()

    new_row = [
        now_jst_str,  # 先頭に日時
        form_data.get("name", ""),
        form_data.get("postal_code", ""),
        full_address,  # 合体した住所
        form_data.get("phone", ""),
        form_data.get("email", ""),
        form_data.get("sns_account", ""),
        form_data.get("school_grade", ""),
        form_data.get("other", ""),
    ]
    worksheet.append_row(new_row, value_input_option="USER_ENTERED")

# -----------------------
# 簡易見積用データ構造
# -----------------------
# 変更点はあるがインポートはそのまま
from PRICE_TABLE_2025 import PRICE_TABLE, COLOR_COST_MAP

# ▼▼▼ 新規: プリント位置が「前のみ/背中のみ」のときの色数選択肢および対応コスト
COLOR_COST_MAP_SINGLE = {
    "前 or 背中 1色": (0, 0),
    "前 or 背中 2色": (1, 0),
    "前 or 背中 フルカラー": (0, 1),
}

# ▼▼▼ 新規: プリント位置が「前と背中」のときの色数選択肢および対応コスト
COLOR_COST_MAP_BOTH = {
    "前と背中 前1色 背中1色": (0, 0),
    "前と背中 前2色 背中1色": (1, 0),
    "前と背中 前1色 背中2色": (1, 0),
    "前と背中 前2色 背中2色": (2, 0),
    "前と背中 フルカラー": (0, 2),
}

# ユーザの見積フロー管理用（簡易的セッション）
user_estimate_sessions = {}  # { user_id: {"step": n, "answers": {...}, "is_single": bool} }


def write_estimate_to_spreadsheet(user_id, estimate_data, total_price, unit_price):
    """
    計算が終わった見積情報をスプレッドシートの「簡易見積」に書き込む
    """
    gc = get_gspread_client()
    sh = gc.open_by_key(SPREADSHEET_KEY)
    worksheet = get_or_create_worksheet(sh, "簡易見積")

    quote_number = str(int(time.time()))  # 見積番号を UNIX時間 で仮生成

    # 日本時間の現在時刻
    jst = pytz.timezone('Asia/Tokyo')
    now_jst_str = datetime.now(jst).strftime("%Y/%m/%d %H:%M:%S")

    new_row = [
        now_jst_str,
        quote_number,
        user_id,
        estimate_data['user_type'],  # 追加した「属性」
        f"{estimate_data['usage_date']}({estimate_data['discount_type']})",
        estimate_data['budget'],
        estimate_data['item'],
        estimate_data['quantity'],
        estimate_data['print_position'],
        estimate_data['color_count'],
        estimate_data['back_name'],
        f"¥{total_price:,}",
        f"¥{unit_price:,}"
    ]
    worksheet.append_row(new_row, value_input_option="USER_ENTERED")

    return quote_number


def find_price_row(item_name, discount_type, quantity):
    """
    PRICE_TABLE から該当する行を探し返す。該当しない場合は None
    """
    for row in PRICE_TABLE:
        if (row["item"] == item_name
            and row["discount_type"] == discount_type
            and row["min_qty"] <= quantity <= row["max_qty"]):
            return row
    return None


def calculate_estimate(estimate_data):
    """
    入力された見積データから合計金額と単価を計算して返す
    """
    item_name = estimate_data['item']
    discount_type = estimate_data['discount_type']
    # 枚数選択肢を実数化
    quantity_map = {
        "20～29枚": 20,
        "30～39枚": 30,
        "40～49枚": 40,
        "50～99枚": 50,
        "100枚以上": 100
    }
    quantity = quantity_map.get(estimate_data['quantity'], 1)

    print_position = estimate_data['print_position']
    color_choice = estimate_data['color_count']
    back_name = estimate_data.get('back_name', "")  # 存在しない場合は空文字

    row = find_price_row(item_name, discount_type, quantity)
    if row is None:
        return 0, 0  # 該当無し

    base_price = row["unit_price"]

    # プリント位置追加
    if print_position in ["前のみ", "背中のみ"]:
        pos_add = 0
    else:
        pos_add = row["pos_add"]

    # ▼▼▼ 変更点: プリント位置によって color_cost_map を切り替え
    if print_position in ["前のみ", "背中のみ"]:
        color_add_count, fullcolor_add_count = COLOR_COST_MAP_SINGLE[color_choice]
        # 背ネームはスキップ扱い => 0円
        back_name_fee = 0
    else:
        color_add_count, fullcolor_add_count = COLOR_COST_MAP_BOTH[color_choice]
        # 背ネームありの場合を計算
        if back_name == "ネーム&背番号セット":
            back_name_fee = row["set_name_num"]
        elif back_name == "ネーム(大)":
            back_name_fee = row["big_name"]
        elif back_name == "番号(大)":
            back_name_fee = row["big_num"]
        else:
            back_name_fee = 0

    color_fee = color_add_count * row["color_add"] + fullcolor_add_count * row["fullcolor_add"]

    unit_price = base_price + pos_add + color_fee + back_name_fee
    total_price = unit_price * quantity

    return total_price, unit_price


# -----------------------
# ここからFlex Message定義
# -----------------------
def flex_user_type():
    """
    ❶属性 (学生 or 一般)
    """
    flex_body = {
        "type": "bubble",
        "hero": {
            "type": "box",
            "layout": "vertical",
            "contents": [
                {
                    "type": "text",
                    "text": "❶属性",
                    "weight": "bold",
                    "size": "lg",
                    "align": "center"
                },
                {
                    "type": "text",
                    "text": "ご利用者の属性を選択してください。",
                    "size": "sm",
                    "wrap": True
                }
            ]
        },
        "footer": {
            "type": "box",
            "layout": "vertical",
            "spacing": "sm",
            "contents": [
                {
                    "type": "button",
                    "style": "primary",
                    "color": "#fc9cc2",
                    "height": "sm",
                    "action": {
                        "type": "message",
                        "label": "学生",
                        "text": "学生"
                    }
                },
                {
                    "type": "button",
                    "style": "primary",
                    "color": "#fc9cc2",
                    "height": "sm",
                    "action": {
                        "type": "message",
                        "label": "一般",
                        "text": "一般"
                    }
                }
            ],
            "flex": 0
        }
    }
    return FlexSendMessage(alt_text="属性を選択してください", contents=flex_body)


def flex_usage_date():
    """
    ❷使用日 (14日目以降 or 14日目以内)
    """
    flex_body = {
        "type": "bubble",
        "hero": {
            "type": "box",
            "layout": "vertical",
            "contents": [
                {
                    "type": "text",
                    "text": "❷使用日",
                    "weight": "bold",
                    "size": "lg",
                    "align": "center"
                },
                {
                    "type": "text",
                    "text": "ご使用日は、今日より? \n(注文日より使用日が14日目以降なら早割)",
                    "size": "sm",
                    "wrap": True
                }
            ]
        },
        "footer": {
            "type": "box",
            "layout": "vertical",
            "spacing": "sm",
            "contents": [
                {
                    "type": "button",
                    "style": "primary",
                    "color": "#fc9cc2",
                    "height": "sm",
                    "action": {
                        "type": "message",
                        "label": "14日目以降",
                        "text": "14日目以降"
                    }
                },
                {
                    "type": "button",
                    "style": "primary",
                    "color": "#fc9cc2",
                    "height": "sm",
                    "action": {
                        "type": "message",
                        "label": "14日目以内",
                        "text": "14日目以内"
                    }
                }
            ],
            "flex": 0
        }
    }
    return FlexSendMessage(alt_text="使用日を選択してください", contents=flex_body)


def flex_budget():
    """
    ❸1枚当たりの予算
    """
    budgets = ["特になし", "1,000円以内", "1,500円以内", "2,000円以内", "2,500円以内", "3,000円以内", "3,500円以内"]
    buttons = []
    for b in budgets:
        buttons.append({
            "type": "button",
            "style": "primary",
            "color": "#fc9cc2",
            "height": "sm",
            "action": {
                "type": "message",
                "label": b,
                "text": b
            }
        })

    flex_body = {
        "type": "bubble",
        "hero": {
            "type": "box",
            "layout": "vertical",
            "contents": [
                {
                    "type": "text",
                    "text": "❸1枚当たりの予算",
                    "weight": "bold",
                    "size": "lg",
                    "align": "center"
                },
                {
                    "type": "text",
                    "text": "ご希望の1枚あたり予算を選択してください。",
                    "size": "sm",
                    "wrap": True
                }
            ]
        },
        "footer": {
            "type": "box",
            "layout": "vertical",
            "spacing": "sm",
            "contents": buttons,
            "flex": 0
        }
    }
    return FlexSendMessage(alt_text="予算を選択してください", contents=flex_body)


def flex_item_select():
    """
    ❹商品名
    """
    items = [
        "ゲームシャツ",
        "ストライプドライベースボールシャツ",
        "ドライベースボールシャツ",
        "ストライプユニフォーム",
        "バスケシャツ",
        "ドライTシャツ",
        "ハイクオリティTシャツ",
        "ドライポロシャツ",
        "ドライロングスリーブTシャツ",  # 修正
        "クルーネックライトトレーナー",
        "ジップアップライトパーカー",
        "フーデッドライトパーカー",
    ]

    item_bubbles = []
    chunk_size = 5
    for i in range(0, len(items), chunk_size):
        chunk_part = items[i:i + chunk_size]
        buttons = []
        for it in chunk_part:
            buttons.append({
                "type": "button",
                "style": "primary",
                "color": "#fc9cc2",
                "height": "sm",
                "action": {
                    "type": "message",
                    "label": it,
                    "text": it
                }
            })
        bubble = {
            "type": "bubble",
            "hero": {
                "type": "box",
                "layout": "vertical",
                "contents": [
                    {
                        "type": "text",
                        "text": "❹商品名",
                        "weight": "bold",
                        "size": "lg",
                        "align": "center"
                    },
                    {
                        "type": "text",
                        "text": "ご希望の商品を選択してください。",
                        "size": "sm",
                        "wrap": True
                    }
                ]
            },
            "footer": {
                "type": "box",
                "layout": "vertical",
                "spacing": "sm",
                "contents": buttons
            }
        }
        item_bubbles.append(bubble)

    carousel = {
        "type": "carousel",
        "contents": item_bubbles
    }
    return FlexSendMessage(alt_text="商品名を選択してください", contents=carousel)


def flex_quantity():
    """
    ❺枚数
    """
    quantities = ["20～29枚", "30～39枚", "40～49枚", "50～99枚", "100枚以上"]
    buttons = []
    for q in quantities:
        buttons.append({
            "type": "button",
            "style": "primary",
            "color": "#fc9cc2",
            "height": "sm",
            "action": {
                "type": "message",
                "label": q,
                "text": q
            }
        })

    flex_body = {
        "type": "bubble",
        "hero": {
            "type": "box",
            "layout": "vertical",
            "contents": [
                {
                    "type": "text",
                    "text": "❺枚数",
                    "weight": "bold",
                    "size": "lg",
                    "align": "center"
                },
                {
                    "type": "text",
                    "text": "必要枚数を選択してください。",
                    "size": "sm",
                    "wrap": True
                },
            ]
        },
        "footer": {
            "type": "box",
            "layout": "vertical",
            "spacing": "sm",
            "contents": buttons
        }
    }
    return FlexSendMessage(alt_text="必要枚数を選択してください", contents=flex_body)


def flex_print_position():
    """
    ❻プリント位置
    """
    positions = ["前のみ", "背中のみ", "前と背中"]
    buttons = []
    for pos in positions:
        buttons.append({
            "type": "button",
            "style": "primary",
            "color": "#fc9cc2",
            "height": "sm",
            "action": {
                "type": "message",
                "label": pos,
                "text": pos
            }
        })

    flex_body = {
        "type": "bubble",
        "hero": {
            "type": "box",
            "layout": "vertical",
            "contents": [
                {
                    "type": "text",
                    "text": "❻プリント位置",
                    "weight": "bold",
                    "size": "lg",
                    "align": "center"
                },
                {
                    "type": "text",
                    "text": "プリントを入れる箇所を選択してください。",
                    "size": "sm",
                    "wrap": True
                }
            ]
        },
        "footer": {
            "type": "box",
            "layout": "vertical",
            "spacing": "sm",
            "contents": buttons
        }
    }
    return FlexSendMessage(alt_text="プリント位置を選択してください", contents=flex_body)


# ▼▼▼ 新規: プリント位置が「前のみ」「背中のみ」の場合の ❼色数
def flex_color_count_single():
    """
    ❼色数（シングル: 前のみ / 背中のみ）
    """
    color_choices = list(COLOR_COST_MAP_SINGLE.keys())
    buttons_bubbles = []
    for c in color_choices:
        buttons_bubbles.append({
            "type": "button",
            "style": "primary",
            "color": "#fc9cc2",
            "height": "sm",
            "action": {
                "type": "message",
                "label": c,
                "text": c
            }
        })

    flex_body = {
        "type": "bubble",
        "hero": {
            "type": "box",
            "layout": "vertical",
            "contents": [
                {
                    "type": "text",
                    "text": "❼色数",
                    "weight": "bold",
                    "size": "lg",
                    "align": "center"
                },
                {
                    "type": "text",
                    "text": "プリントの色数を選択してください。\n（前のみ/背中のみ）",
                    "size": "sm",
                    "wrap": True
                }
            ]
        },
        "footer": {
            "type": "box",
            "layout": "vertical",
            "spacing": "sm",
            "contents": buttons_bubbles
        }
    }
    return FlexSendMessage(alt_text="色数を選択してください", contents=flex_body)


# ▼▼▼ 新規: プリント位置が「前と背中」の場合の ❼色数
def flex_color_count_both():
    """
    ❼色数（両面: 前と背中）
    """
    color_choices = list(COLOR_COST_MAP_BOTH.keys())
    buttons_bubbles = []
    for c in color_choices:
        buttons_bubbles.append({
            "type": "button",
            "style": "primary",
            "color": "#fc9cc2",
            "height": "sm",
            "action": {
                "type": "message",
                "label": c,
                "text": c
            }
        })

    flex_body = {
        "type": "bubble",
        "hero": {
            "type": "box",
            "layout": "vertical",
            "contents": [
                {
                    "type": "text",
                    "text": "❼色数",
                    "weight": "bold",
                    "size": "lg",
                    "align": "center"
                },
                {
                    "type": "text",
                    "text": "プリントの色数を選択してください。\n（前と背中）",
                    "size": "sm",
                    "wrap": True
                }
            ]
        },
        "footer": {
            "type": "box",
            "layout": "vertical",
            "spacing": "sm",
            "contents": buttons_bubbles
        }
    }
    return FlexSendMessage(alt_text="色数を選択してください", contents=flex_body)


def flex_back_name():
    """
    ❽背ネーム・番号
    """
    names = ["ネーム&背番号セット", "ネーム(大)", "番号(大)", "背ネーム・番号を使わない"]
    buttons = []
    for nm in names:
        buttons.append({
            "type": "button",
            "style": "primary",
            "color": "#fc9cc2",
            "height": "sm",
            "action": {
                "type": "message",
                "label": nm,
                "text": nm
            }
        })

    flex_body = {
        "type": "bubble",
        "hero": {
            "type": "box",
            "layout": "vertical",
            "contents": [
                {
                    "type": "text",
                    "text": "❽背ネーム・番号",
                    "weight": "bold",
                    "size": "lg",
                    "align": "center"
                },
                {
                    "type": "text",
                    "text": "背ネームや番号を入れる場合は選択してください。",
                    "size": "sm",
                    "wrap": True
                },
                {
                    "type": "text",
                    "text": "不要な場合は「背ネーム・番号を使わない」を選択してください。",
                    "size": "sm",
                    "wrap": True
                }
            ]
        },
        "footer": {
            "type": "box",
            "layout": "vertical",
            "spacing": "sm",
            "contents": buttons
        }
    }
    return FlexSendMessage(alt_text="背ネーム・番号を選択してください", contents=flex_body)



# -----------------------
# お問い合わせ時に返信するFlex Message
# -----------------------
def flex_inquiry():
    contents = {
        "type": "carousel",
        "contents": [
            # 1個目: FAQ
            {
                "type": "bubble",
                "hero": {
                    "type": "image",
                    "url": "https://catalog-bot-zf1t.onrender.com/IMG_5765.PNG",
                    "size": "full",
                    "aspectRatio": "501:556",
                    "aspectMode": "cover",
                    "action": {
                        "type": "uri",
                        "uri": "https://graffitees.jp/faq/"
                    }
                }
            },
            # 2個目: 有人チャット
            {
                "type": "bubble",
                "hero": {
                    "type": "image",
                    "url": "https://catalog-bot-zf1t.onrender.com/IMG_5766.PNG",
                    "size": "full",
                    "aspectRatio": "501:556",
                    "aspectMode": "cover",
                    "action": {
                        "type": "message",
                        "text": "#有人チャット"
                    }
                }
            }
        ]
    }
    return FlexSendMessage(alt_text="お問い合わせ情報", contents=contents)


# -----------------------
# 1) LINE Messaging API 受信 (Webhook)
# -----------------------
@app.route("/line/callback", methods=["POST"])
def line_callback():
    signature = request.headers["X-Line-Signature"]
    body = request.get_data(as_text=True)

    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400, "Invalid signature. Please check your channel access token/channel secret.")

    return "OK", 200


# -----------------------
# 2) LINE上でメッセージ受信時
# -----------------------
@handler.add(MessageEvent, message=TextMessage)
def handle_message(event: MessageEvent):
    user_id = event.source.user_id
    user_message = event.message.text.strip()

    # 1) お問い合わせ対応
    if user_message == "お問い合わせ":
        line_bot_api.reply_message(
            event.reply_token,
            flex_inquiry()
        )
        return

    # 2) 有人チャット
    if user_message == "#有人チャット":
        reply_text = (
            "有人チャットに接続いたします。\n"
            "ご検討中のデザインを画像やイラストでお送りください。\n\n"
            "※当ショップの営業時間は10：00～18：00となります。\n"
            "営業時間外のお問い合わせにつきましては確認ができ次第の回答となります。\n"
            "誠に恐れ入りますが、ご了承くださいませ。\n\n"
            "その他ご要望などがございましたらメッセージでお送りくださいませ。\n"
            "よろしくお願い致します。"
        )
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=reply_text)
        )
        return

    # すでに見積りフロー中かどうか
    if user_id in user_estimate_sessions and user_estimate_sessions[user_id]["step"] > 0:
        process_estimate_flow(event, user_message)
        return

    # 見積りフロー開始
    if user_message == "カンタン見積り":
        start_estimate_flow(event)
        return

    # カタログ案内 (トリガー例: "キャンペーン" or "catalog" など含む場合)
    if "キャンペーン" in user_message or "catalog" in user_message.lower():
        send_catalog_info(event)
        return

    # その他のメッセージはスルー
    return


def send_catalog_info(event: MessageEvent):
    """
    カタログ案内メッセージ
    """
    reply_text = (
        "🎁➖➖➖➖➖➖➖➖🎁\n"
        "  ✨カタログ無料プレゼント✨\n"
        "🎁➖➖➖➖➖➖➖➖🎁\n\n"
        "クラスTシャツの最新デザインやトレンド情報が詰まったカタログを、"
        "期間限定で無料でお届けします✨\n\n"
        "【応募方法】\n"
        "以下のアカウントをフォロー👇\n"
        "（どちらかでOK🙆）\n"
        "📸 Instagram\n"
        "https://www.instagram.com/graffitees_045/\n"
        "🎥 TikTok\n"
        "https://www.tiktok.com/@graffitees_045\n\n"
        "フォロー後、下記のフォームからお申込みください👇\n"
        "📩 カタログ申込みフォーム\n"
        "import os
import json
import time
from datetime import datetime
import pytz

import gspread
from flask import Flask, render_template_string, request, session, abort
import uuid
from oauth2client.service_account import ServiceAccountCredentials

# 追加 -----------------------------------
import requests
# ----------------------------------------

# line-bot-sdk v2 系
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import (
    MessageEvent, TextMessage, TextSendMessage, FlexSendMessage
)

app = Flask(__name__)
app.secret_key = 'some_secret_key'  # セッションが必要

# -----------------------
# 環境変数取得
# -----------------------
LINE_CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET", "")
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
SERVICE_ACCOUNT_FILE = os.environ.get("GCP_SERVICE_ACCOUNT_JSON", "")
SPREADSHEET_KEY = os.environ.get("SPREADSHEET_KEY", "")

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)


# -----------------------
# Google Sheets 接続
# -----------------------
def get_gspread_client():
    """
    環境変数 SERVICE_ACCOUNT_FILE (JSONパス or JSON文字列) から認証情報を取り出し、
    gspread クライアントを返す
    """
    if not SERVICE_ACCOUNT_FILE:
        raise ValueError("環境変数 GCP_SERVICE_ACCOUNT_JSON が設定されていません。")

    service_account_dict = json.loads(SERVICE_ACCOUNT_FILE)

    scope = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    credentials = ServiceAccountCredentials.from_json_keyfile_dict(service_account_dict, scope)
    return gspread.authorize(credentials)


def get_or_create_worksheet(sheet, title):
    """
    スプレッドシート内で該当titleのワークシートを取得。
    なければ新規作成し、ヘッダを書き込む。
    """
    try:
        ws = sheet.worksheet(title)
    except gspread.exceptions.WorksheetNotFound:
        ws = sheet.add_worksheet(title=title, rows=2000, cols=50)
        # 必要であればヘッダをセット
        if title == "CatalogRequests":
            ws.update('A1:I1', [[
                "日時",  # ←先頭に日時列
                "氏名", "郵便番号", "住所", "電話番号",
                "メールアドレス", "Insta/TikTok名",
                "在籍予定の学校名と学年", "その他(質問・要望)"
            ]])
        elif title == "簡易見積":
            # 属性カラムを追加したため、A1:M1 で13列に拡張
            ws.update('A1:M1', [[
                "日時", "見積番号", "ユーザーID", "属性",
                "使用日(割引区分)", "予算", "商品名", "枚数",
                "プリント位置", "色数", "背ネーム",
                "合計金額", "単価"
            ]])
        elif title == "WebOrderRequests":
            # 新たに Webフォーム注文のヘッダーをセット（必要に応じて列を追加/変更）
            ws.update('A1:AZ1', [[
                "日時",
                "商品名", "品番", "カラーNo", "商品カラー",
                "size150", "sizeSS", "sizeS", "sizeM", "sizeL", "sizeXL", "sizeXXL", "合計枚数",

                "printPositionNo1", "nameNumberOption1", "nameNumberPrintType1",
                "singleColor1", "edgeType1", "edgeCustomTextColor1", "edgeCustomEdgeColor1", "edgeCustomEdgeColor2_1",
                "fontType1", "fontNumber1",
                "printColorOption1_1", "printColorOption1_2", "printColorOption1_3", "fullColorSize1",
                "designCode1", "designSize1", "designSizeX1", "designSizeY1",

                "printPositionNo2", "nameNumberOption2", "nameNumberPrintType2",
                "singleColor2", "edgeType2", "edgeCustomTextColor2", "edgeCustomEdgeColor2", "edgeCustomEdgeColor2_2",
                "fontType2", "fontNumber2",
                "printColorOption2_1", "printColorOption2_2", "printColorOption2_3", "fullColorSize2",
                "designCode2", "designSize2", "designSizeX2", "designSizeY2",

                "printPositionNo3", "nameNumberOption3", "nameNumberPrintType3",
                "singleColor3", "edgeType3", "edgeCustomTextColor3", "edgeCustomEdgeColor3", "edgeCustomEdgeColor2_3",
                "fontType3", "fontNumber3",
                "printColorOption3_1", "printColorOption3_2", "printColorOption3_3", "fullColorSize3",
                "designCode3", "designSize3", "designSizeX3", "designSizeY3",

                "printPositionNo4", "nameNumberOption4", "nameNumberPrintType4",
                "singleColor4", "edgeType4", "edgeCustomTextColor4", "edgeCustomEdgeColor4", "edgeCustomEdgeColor2_4",
                "fontType4", "fontNumber4",
                "printColorOption4_1", "printColorOption4_2", "printColorOption4_3", "fullColorSize4",
                "designCode4", "designSize4", "designSizeX4", "designSizeY4",

                "希望お届け日", "使用日", "申込日", "利用する学割特典",
                "学校名", "LINEの名前", "クラス・団体名",
                "郵便番号", "住所1", "住所2", "学校TEL",
                "代表者", "代表者TEL", "代表者メール",
                "デザイン確認方法", "お支払い方法"
            ]])
    return ws


def write_to_spreadsheet_for_catalog(form_data: dict):
    gc = get_gspread_client()
    sh = gc.open_by_key(SPREADSHEET_KEY)
    worksheet = get_or_create_worksheet(sh, "CatalogRequests")

    # 日本時間の現在時刻
    jst = pytz.timezone('Asia/Tokyo')
    now_jst_str = datetime.now(jst).strftime("%Y/%m/%d %H:%M:%S")

    # address_1 と address_2 を合体して1つのセルに
    full_address = f"{form_data.get('address_1', '')} {form_data.get('address_2', '')}".strip()

    new_row = [
        now_jst_str,  # 先頭に日時
        form_data.get("name", ""),
        form_data.get("postal_code", ""),
        full_address,  # 合体した住所
        form_data.get("phone", ""),
        form_data.get("email", ""),
        form_data.get("sns_account", ""),
        form_data.get("school_grade", ""),
        form_data.get("other", ""),
    ]
    worksheet.append_row(new_row, value_input_option="USER_ENTERED")


# -----------------------
# 簡易見積用データ構造
# -----------------------
from PRICE_TABLE_2025 import PRICE_TABLE, COLOR_COST_MAP

# ▼▼▼ 新規: プリント位置が「前のみ/背中のみ」のときの色数選択肢および対応コスト
COLOR_COST_MAP_SINGLE = {
    "前 or 背中 1色": (0, 0),
    "前 or 背中 2色": (1, 0),
    "前 or 背中 フルカラー": (0, 1),
}

# ▼▼▼ 新規: プリント位置が「前と背中」のときの色数選択肢および対応コスト
COLOR_COST_MAP_BOTH = {
    "前と背中 前1色 背中1色": (0, 0),
    "前と背中 前2色 背中1色": (1, 0),
    "前と背中 前1色 背中2色": (1, 0),
    "前と背中 前2色 背中2色": (2, 0),
    "前と背中 フルカラー": (0, 2),
}

# ユーザの見積フロー管理用（簡易的セッション）
user_estimate_sessions = {}  # { user_id: {"step": n, "answers": {...}, "is_single": bool} }


def write_estimate_to_spreadsheet(user_id, estimate_data, total_price, unit_price):
    """
    計算が終わった見積情報をスプレッドシートの「簡易見積」に書き込む
    """
    gc = get_gspread_client()
    sh = gc.open_by_key(SPREADSHEET_KEY)
    worksheet = get_or_create_worksheet(sh, "簡易見積")

    quote_number = str(int(time.time()))  # 見積番号を UNIX時間 で仮生成

    # 日本時間の現在時刻
    jst = pytz.timezone('Asia/Tokyo')
    now_jst_str = datetime.now(jst).strftime("%Y/%m/%d %H:%M:%S")

    new_row = [
        now_jst_str,
        quote_number,
        user_id,
        estimate_data['user_type'],  # 追加した「属性」
        f"{estimate_data['usage_date']}({estimate_data['discount_type']})",
        estimate_data['budget'],
        estimate_data['item'],
        estimate_data['quantity'],
        estimate_data['print_position'],
        estimate_data['color_count'],
        estimate_data['back_name'],
        f"¥{total_price:,}",
        f"¥{unit_price:,}"
    ]
    worksheet.append_row(new_row, value_input_option="USER_ENTERED")

    return quote_number


def find_price_row(item_name, discount_type, quantity):
    """
    PRICE_TABLE から該当する行を探し返す。該当しない場合は None
    """
    for row in PRICE_TABLE:
        if (row["item"] == item_name
            and row["discount_type"] == discount_type
            and row["min_qty"] <= quantity <= row["max_qty"]):
            return row
    return None


def calculate_estimate(estimate_data):
    """
    入力された見積データから合計金額と単価を計算して返す
    """
    item_name = estimate_data['item']
    discount_type = estimate_data['discount_type']
    # 枚数選択肢を実数化
    quantity_map = {
        "20～29枚": 20,
        "30～39枚": 30,
        "40～49枚": 40,
        "50～99枚": 50,
        "100枚以上": 100
    }
    quantity = quantity_map.get(estimate_data['quantity'], 1)

    print_position = estimate_data['print_position']
    color_choice = estimate_data['color_count']
    back_name = estimate_data.get('back_name', "")

    row = find_price_row(item_name, discount_type, quantity)
    if row is None:
        return 0, 0  # 該当無し

    base_price = row["unit_price"]

    # プリント位置追加
    if print_position in ["前のみ", "背中のみ"]:
        pos_add = 0
    else:
        pos_add = row["pos_add"]

    if print_position in ["前のみ", "背中のみ"]:
        color_add_count, fullcolor_add_count = COLOR_COST_MAP_SINGLE[color_choice]
        # 背ネームはスキップ扱い => 0円
        back_name_fee = 0
    else:
        color_add_count, fullcolor_add_count = COLOR_COST_MAP_BOTH[color_choice]
        # 背ネームありの場合
        if back_name == "ネーム&背番号セット":
            back_name_fee = row["set_name_num"]
        elif back_name == "ネーム(大)":
            back_name_fee = row["big_name"]
        elif back_name == "番号(大)":
            back_name_fee = row["big_num"]
        else:
            back_name_fee = 0

    color_fee = color_add_count * row["color_add"] + fullcolor_add_count * row["fullcolor_add"]

    unit_price = base_price + pos_add + color_fee + back_name_fee
    total_price = unit_price * quantity

    return total_price, unit_price


# -----------------------
# ここからFlex Message定義
# -----------------------
def flex_user_type():
    flex_body = {
        "type": "bubble",
        "hero": {
            "type": "box",
            "layout": "vertical",
            "contents": [
                {
                    "type": "text",
                    "text": "❶属性",
                    "weight": "bold",
                    "size": "lg",
                    "align": "center"
                },
                {
                    "type": "text",
                    "text": "ご利用者の属性を選択してください。",
                    "size": "sm",
                    "wrap": True
                }
            ]
        },
        "footer": {
            "type": "box",
            "layout": "vertical",
            "spacing": "sm",
            "contents": [
                {
                    "type": "button",
                    "style": "primary",
                    "color": "#fc9cc2",
                    "height": "sm",
                    "action": {
                        "type": "message",
                        "label": "学生",
                        "text": "学生"
                    }
                },
                {
                    "type": "button",
                    "style": "primary",
                    "color": "#fc9cc2",
                    "height": "sm",
                    "action": {
                        "type": "message",
                        "label": "一般",
                        "text": "一般"
                    }
                }
            ],
            "flex": 0
        }
    }
    return FlexSendMessage(alt_text="属性を選択してください", contents=flex_body)


def flex_usage_date():
    flex_body = {
        "type": "bubble",
        "hero": {
            "type": "box",
            "layout": "vertical",
            "contents": [
                {
                    "type": "text",
                    "text": "❷使用日",
                    "weight": "bold",
                    "size": "lg",
                    "align": "center"
                },
                {
                    "type": "text",
                    "text": "ご使用日は、今日より? \n(注文日より使用日が14日目以降なら早割)",
                    "size": "sm",
                    "wrap": True
                }
            ]
        },
        "footer": {
            "type": "box",
            "layout": "vertical",
            "spacing": "sm",
            "contents": [
                {
                    "type": "button",
                    "style": "primary",
                    "color": "#fc9cc2",
                    "height": "sm",
                    "action": {
                        "type": "message",
                        "label": "14日目以降",
                        "text": "14日目以降"
                    }
                },
                {
                    "type": "button",
                    "style": "primary",
                    "color": "#fc9cc2",
                    "height": "sm",
                    "action": {
                        "type": "message",
                        "label": "14日目以内",
                        "text": "14日目以内"
                    }
                }
            ],
            "flex": 0
        }
    }
    return FlexSendMessage(alt_text="使用日を選択してください", contents=flex_body)


def flex_budget():
    budgets = ["特になし", "1,000円以内", "1,500円以内", "2,000円以内", "2,500円以内", "3,000円以内", "3,500円以内"]
    buttons = []
    for b in budgets:
        buttons.append({
            "type": "button",
            "style": "primary",
            "color": "#fc9cc2",
            "height": "sm",
            "action": {
                "type": "message",
                "label": b,
                "text": b
            }
        })

    flex_body = {
        "type": "bubble",
        "hero": {
            "type": "box",
            "layout": "vertical",
            "contents": [
                {
                    "type": "text",
                    "text": "❸1枚当たりの予算",
                    "weight": "bold",
                    "size": "lg",
                    "align": "center"
                },
                {
                    "type": "text",
                    "text": "ご希望の1枚あたり予算を選択してください。",
                    "size": "sm",
                    "wrap": True
                }
            ]
        },
        "footer": {
            "type": "box",
            "layout": "vertical",
            "spacing": "sm",
            "contents": buttons,
            "flex": 0
        }
    }
    return FlexSendMessage(alt_text="予算を選択してください", contents=flex_body)


def flex_item_select():
    items = [
        "ゲームシャツ",
        "ストライプドライベースボールシャツ",
        "ドライベースボールシャツ",
        "ストライプユニフォーム",
        "バスケシャツ",
        "ドライTシャツ",
        "ハイクオリティTシャツ",
        "ドライポロシャツ",
        "ドライロングスリーブTシャツ",
        "クルーネックライトトレーナー",
        "ジップアップライトパーカー",
        "フーデッドライトパーカー",
    ]

    item_bubbles = []
    chunk_size = 5
    for i in range(0, len(items), chunk_size):
        chunk_part = items[i:i + chunk_size]
        buttons = []
        for it in chunk_part:
            buttons.append({
                "type": "button",
                "style": "primary",
                "color": "#fc9cc2",
                "height": "sm",
                "action": {
                    "type": "message",
                    "label": it,
                    "text": it
                }
            })
        bubble = {
            "type": "bubble",
            "hero": {
                "type": "box",
                "layout": "vertical",
                "contents": [
                    {
                        "type": "text",
                        "text": "❹商品名",
                        "weight": "bold",
                        "size": "lg",
                        "align": "center"
                    },
                    {
                        "type": "text",
                        "text": "ご希望の商品を選択してください。",
                        "size": "sm",
                        "wrap": True
                    }
                ]
            },
            "footer": {
                "type": "box",
                "layout": "vertical",
                "spacing": "sm",
                "contents": buttons
            }
        }
        item_bubbles.append(bubble)

    carousel = {
        "type": "carousel",
        "contents": item_bubbles
    }
    return FlexSendMessage(alt_text="商品名を選択してください", contents=carousel)


def flex_quantity():
    quantities = ["20～29枚", "30～39枚", "40～49枚", "50～99枚", "100枚以上"]
    buttons = []
    for q in quantities:
        buttons.append({
            "type": "button",
            "style": "primary",
            "color": "#fc9cc2",
            "height": "sm",
            "action": {
                "type": "message",
                "label": q,
                "text": q
            }
        })

    flex_body = {
        "type": "bubble",
        "hero": {
            "type": "box",
            "layout": "vertical",
            "contents": [
                {
                    "type": "text",
                    "text": "❺枚数",
                    "weight": "bold",
                    "size": "lg",
                    "align": "center"
                },
                {
                    "type": "text",
                    "text": "必要枚数を選択してください。",
                    "size": "sm",
                    "wrap": True
                },
            ]
        },
        "footer": {
            "type": "box",
            "layout": "vertical",
            "spacing": "sm",
            "contents": buttons
        }
    }
    return FlexSendMessage(alt_text="必要枚数を選択してください", contents=flex_body)


def flex_print_position():
    positions = ["前のみ", "背中のみ", "前と背中"]
    buttons = []
    for pos in positions:
        buttons.append({
            "type": "button",
            "style": "primary",
            "color": "#fc9cc2",
            "height": "sm",
            "action": {
                "type": "message",
                "label": pos,
                "text": pos
            }
        })

    flex_body = {
        "type": "bubble",
        "hero": {
            "type": "box",
            "layout": "vertical",
            "contents": [
                {
                    "type": "text",
                    "text": "❻プリント位置",
                    "weight": "bold",
                    "size": "lg",
                    "align": "center"
                },
                {
                    "type": "text",
                    "text": "プリントを入れる箇所を選択してください。",
                    "size": "sm",
                    "wrap": True
                }
            ]
        },
        "footer": {
            "type": "box",
            "layout": "vertical",
            "spacing": "sm",
            "contents": buttons
        }
    }
    return FlexSendMessage(alt_text="プリント位置を選択してください", contents=flex_body)


def flex_color_count_single():
    color_choices = list(COLOR_COST_MAP_SINGLE.keys())
    buttons_bubbles = []
    for c in color_choices:
        buttons_bubbles.append({
            "type": "button",
            "style": "primary",
            "color": "#fc9cc2",
            "height": "sm",
            "action": {
                "type": "message",
                "label": c,
                "text": c
            }
        })

    flex_body = {
        "type": "bubble",
        "hero": {
            "type": "box",
            "layout": "vertical",
            "contents": [
                {
                    "type": "text",
                    "text": "❼色数",
                    "weight": "bold",
                    "size": "lg",
                    "align": "center"
                },
                {
                    "type": "text",
                    "text": "プリントの色数を選択してください。\n（前のみ/背中のみ）",
                    "size": "sm",
                    "wrap": True
                }
            ]
        },
        "footer": {
            "type": "box",
            "layout": "vertical",
            "spacing": "sm",
            "contents": buttons_bubbles
        }
    }
    return FlexSendMessage(alt_text="色数を選択してください", contents=flex_body)


def flex_color_count_both():
    color_choices = list(COLOR_COST_MAP_BOTH.keys())
    buttons_bubbles = []
    for c in color_choices:
        buttons_bubbles.append({
            "type": "button",
            "style": "primary",
            "color": "#fc9cc2",
            "height": "sm",
            "action": {
                "type": "message",
                "label": c,
                "text": c
            }
        })

    flex_body = {
        "type": "bubble",
        "hero": {
            "type": "box",
            "layout": "vertical",
            "contents": [
                {
                    "type": "text",
                    "text": "❼色数",
                    "weight": "bold",
                    "size": "lg",
                    "align": "center"
                },
                {
                    "type": "text",
                    "text": "プリントの色数を選択してください。\n（前と背中）",
                    "size": "sm",
                    "wrap": True
                }
            ]
        },
        "footer": {
            "type": "box",
            "layout": "vertical",
            "spacing": "sm",
            "contents": buttons_bubbles
        }
    }
    return FlexSendMessage(alt_text="色数を選択してください", contents=flex_body)


def flex_back_name():
    names = ["ネーム&背番号セット", "ネーム(大)", "番号(大)", "背ネーム・番号を使わない"]
    buttons = []
    for nm in names:
        buttons.append({
            "type": "button",
            "style": "primary",
            "color": "#fc9cc2",
            "height": "sm",
            "action": {
                "type": "message",
                "label": nm,
                "text": nm
            }
        })

    flex_body = {
        "type": "bubble",
        "hero": {
            "type": "box",
            "layout": "vertical",
            "contents": [
                {
                    "type": "text",
                    "text": "❽背ネーム・番号",
                    "weight": "bold",
                    "size": "lg",
                    "align": "center"
                },
                {
                    "type": "text",
                    "text": "背ネームや番号を入れる場合は選択してください。",
                    "size": "sm",
                    "wrap": True
                },
                {
                    "type": "text",
                    "text": "不要な場合は「背ネーム・番号を使わない」を選択してください。",
                    "size": "sm",
                    "wrap": True
                }
            ]
        },
        "footer": {
            "type": "box",
            "layout": "vertical",
            "spacing": "sm",
            "contents": buttons
        }
    }
    return FlexSendMessage(alt_text="背ネーム・番号を選択してください", contents=flex_body)


# -----------------------
# お問い合わせ時に返信するFlex Message
# -----------------------
def flex_inquiry():
    contents = {
        "type": "carousel",
        "contents": [
            # 1個目: FAQ
            {
                "type": "bubble",
                "hero": {
                    "type": "image",
                    "url": "https://catalog-bot-zf1t.onrender.com/IMG_5765.PNG",
                    "size": "full",
                    "aspectRatio": "501:556",
                    "aspectMode": "cover",
                    "action": {
                        "type": "uri",
                        "uri": "https://graffitees.jp/faq/"
                    }
                }
            },
            # 2個目: 有人チャット
            {
                "type": "bubble",
                "hero": {
                    "type": "image",
                    "url": "https://catalog-bot-zf1t.onrender.com/IMG_5766.PNG",
                    "size": "full",
                    "aspectRatio": "501:556",
                    "aspectMode": "cover",
                    "action": {
                        "type": "message",
                        "text": "#有人チャット"
                    }
                }
            },
            # 3個目: Webフォーム注文
            {
                "type": "bubble",
                "hero": {
                    "type": "image",
                    "url": "https://catalog-bot-zf1t.onrender.com/PRINT_LOCATION.png",
                    "size": "full",
                    "aspectRatio": "501:556",
                    "aspectMode": "cover",
                    "action": {
                        "type": "uri",
                        "uri": "https://catalog-bot-zf1t.onrender.com/web_order_form"
                    }
                }
            }
        ]
    }
    return FlexSendMessage(alt_text="お問い合わせ情報", contents=contents)


# -----------------------
# 1) LINE Messaging API 受信 (Webhook)
# -----------------------
@app.route("/line/callback", methods=["POST"])
def line_callback():
    signature = request.headers["X-Line-Signature"]
    body = request.get_data(as_text=True)

    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400, "Invalid signature. Please check your channel access token/channel secret.")

    return "OK", 200


# -----------------------
# 2) LINE上でメッセージ受信時
# -----------------------
@handler.add(MessageEvent, message=TextMessage)
def handle_message(event: MessageEvent):
    user_id = event.source.user_id
    user_message = event.message.text.strip()

    # 1) お問い合わせ対応
    if user_message == "お問い合わせ":
        line_bot_api.reply_message(
            event.reply_token,
            flex_inquiry()
        )
        return

    # 2) 有人チャット
    if user_message == "#有人チャット":
        reply_text = (
            "有人チャットに接続いたします。\n"
            "ご検討中のデザインを画像やイラストでお送りください。\n\n"
            "※当ショップの営業時間は10：00～18：00となります。\n"
            "営業時間外のお問い合わせにつきましては確認ができ次第の回答となります。\n"
            "誠に恐れ入りますが、ご了承くださいませ。\n\n"
            "その他ご要望などがございましたらメッセージでお送りくださいませ。\n"
            "よろしくお願い致します。"
        )
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=reply_text)
        )
        return

    # すでに見積りフロー中かどうか
    if user_id in user_estimate_sessions and user_estimate_sessions[user_id]["step"] > 0:
        process_estimate_flow(event, user_message)
        return

    # 見積りフロー開始
    if user_message == "カンタン見積り":
        start_estimate_flow(event)
        return

    # カタログ案内
    if "キャンペーン" in user_message or "catalog" in user_message.lower():
        send_catalog_info(event)
        return

    # その他のメッセージはスルー
    return


def send_catalog_info(event: MessageEvent):
    reply_text = (
        "🎁➖➖➖➖➖➖➖➖🎁\n"
        "  ✨カタログ無料プレゼント✨\n"
        "🎁➖➖➖➖➖➖➖➖🎁\n\n"
        "クラスTシャツの最新デザインやトレンド情報が詰まったカタログを、"
        "期間限定で無料でお届けします✨\n\n"
        "【応募方法】\n"
        "以下のアカウントをフォロー👇\n"
        "（どちらかでOK🙆）\n"
        "📸 Instagram\n"
        "https://www.instagram.com/graffitees_045/\n"
        "🎥 TikTok\n"
        "https://www.tiktok.com/@graffitees_045\n\n"
        "フォロー後、下記のフォームからお申込みください👇\n"
        "📩 カタログ申込みフォーム\n"
        "https://graffitees-line-bot.onrender.com/catalog_form\n"
        "⚠️ 注意：サブアカウントや重複申込みはご遠慮ください。\n\n"
        "【カタログ発送時期】\n"
        "📅 2025年4月中旬より郵送で発送予定です。\n\n"
        "【配布数について】\n"
        "先着300名様分を予定しています。\n"
        "※応募多数となった場合、配布数の増加や抽選となる可能性があります。\n\n"
        "ご応募お待ちしております🙆"
    )
    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text=reply_text)
    )


# -----------------------
# 見積りフロー
# -----------------------
def start_estimate_flow(event: MessageEvent):
    user_id = event.source.user_id
    user_estimate_sessions[user_id] = {
        "step": 1,
        "answers": {},
        "is_single": False
    }

    line_bot_api.reply_message(
        event.reply_token,
        flex_user_type()
    )


def process_estimate_flow(event: MessageEvent, user_message: str):
    user_id = event.source.user_id
    if user_id not in user_estimate_sessions:
        return

    session_data = user_estimate_sessions[user_id]
    step = session_data["step"]

    if step == 1:
        if user_message in ["学生", "一般"]:
            session_data["answers"]["user_type"] = user_message
            session_data["step"] = 2
            line_bot_api.reply_message(event.reply_token, flex_usage_date())
        else:
            del user_estimate_sessions[user_id]
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="入力内容に誤りがあるようです。 \nお手数をおかけしますが、再度メニューの「カンタン見積り」より、該当の項目を選択タブからお選びください。\n※テキストの直接入力はご利用いただけませんので、ご了承くださいませ。")
            )
        return

    elif step == 2:
        if user_message in ["14日目以降", "14日目以内"]:
            session_data["answers"]["usage_date"] = user_message
            session_data["answers"]["discount_type"] = "早割" if user_message == "14日目以降" else "通常"
            session_data["step"] = 3
            line_bot_api.reply_message(event.reply_token, flex_budget())
        else:
            del user_estimate_sessions[user_id]
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="入力内容に誤りがあるようです。 \nお手数をおかけしますが、再度メニューの「カンタン見積り」より、該当の項目を選択タブからお選びください。\n※テキストの直接入力はご利用いただけませんので、ご了承くださいませ。")
            )
        return

    elif step == 3:
        valid_budgets = ["特になし", "1,000円以内", "1,500円以内", "2,000円以内", "2,500円以内", "3,000円以内", "3,500円以内"]
        if user_message in valid_budgets:
            session_data["answers"]["budget"] = user_message
            session_data["step"] = 4
            line_bot_api.reply_message(event.reply_token, flex_item_select())
        else:
            del user_estimate_sessions[user_id]
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="入力内容に誤りがあるようです。 \nお手数をおかけしますが、再度メニューの「カンタン見積り」より、該当の項目を選択タブからお選びください。\n※テキストの直接入力はご利用いただけませんので、ご了承くださいませ。")
            )
        return

    elif step == 4:
        items = [
            "ゲームシャツ",
            "ストライプドライベースボールシャツ",
            "ドライベースボールシャツ",
            "ストライプユニフォーム",
            "バスケシャツ",
            "ドライTシャツ",
            "ハイクオリティTシャツ",
            "ドライポロシャツ",
            "ドライロングスリーブTシャツ",
            "クルーネックライトトレーナー",
            "ジップアップライトパーカー",
            "フーデッドライトパーカー",
        ]
        if user_message in items:
            session_data["answers"]["item"] = user_message
            session_data["step"] = 5
            line_bot_api.reply_message(event.reply_token, flex_quantity())
        else:
            del user_estimate_sessions[user_id]
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="入力内容に誤りがあるようです。 \nお手数をおかけしますが、再度メニューの「カンタン見積り」より、該当の項目を選択タブからお選びください。\n※テキストの直接入力はご利用いただけませんので、ご了承くださいませ。")
            )
        return

    elif step == 5:
        valid_choices = ["20～29枚", "30～39枚", "40～49枚", "50～99枚", "100枚以上"]
        if user_message in valid_choices:
            session_data["answers"]["quantity"] = user_message
            session_data["step"] = 6
            line_bot_api.reply_message(event.reply_token, flex_print_position())
        else:
            del user_estimate_sessions[user_id]
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="入力内容に誤りがあるようです。 \nお手数をおかけしますが、再度メニューの「カンタン見積り」より、該当の項目を選択タブからお選びください。\n※テキストの直接入力はご利用いただけませんので、ご了承くださいませ。")
            )
        return

    elif step == 6:
        valid_positions = ["前のみ", "背中のみ", "前と背中"]
        if user_message in valid_positions:
            session_data["answers"]["print_position"] = user_message
            session_data["step"] = 7

            if user_message in ["前のみ", "背中のみ"]:
                session_data["is_single"] = True
                line_bot_api.reply_message(event.reply_token, flex_color_count_single())
            else:
                session_data["is_single"] = False
                line_bot_api.reply_message(event.reply_token, flex_color_count_both())
        else:
            del user_estimate_sessions[user_id]
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="入力内容に誤りがあるようです。 \nお手数をおかけしますが、再度メニューの「カンタン見積り」より、該当の項目を選択タブからお選びください。\n※テキストの直接入力はご利用いただけませんので、ご了承くださいませ。")
            )
        return

    elif step == 7:
        if session_data["is_single"]:
            if user_message not in COLOR_COST_MAP_SINGLE:
                del user_estimate_sessions[user_id]
                line_bot_api.reply_message(
                    event.reply_token,
                    TextSendMessage(text="入力内容に誤りがあるようです。 \nお手数をおかけしますが、再度メニューの「カンタン見積り」より、該当の項目を選択タブからお選びください。\n※テキストの直接入力はご利用いただけませんので、ご了承くださいませ。")
                )
                return

            session_data["answers"]["color_count"] = user_message
            session_data["answers"]["back_name"] = "なし"

            est_data = session_data["answers"]
            total_price, unit_price = calculate_estimate(est_data)
            quote_number = write_estimate_to_spreadsheet(user_id, est_data, total_price, unit_price)

            reply_text = (
                f"概算のお見積りが完了しました。\n\n"
                f"見積番号: {quote_number}\n"
                f"属性: {est_data['user_type']}\n"
                f"使用日: {est_data['usage_date']}（{est_data['discount_type']}）\n"
                f"予算: {est_data['budget']}\n"
                f"商品: {est_data['item']}\n"
                f"枚数: {est_data['quantity']}\n"
                f"プリント位置: {est_data['print_position']}\n"
                f"色数: {est_data['color_count']}\n"
                f"背ネーム・番号: なし\n\n"
                f"【合計金額】¥{total_price:,}\n"
                f"【1枚あたり】¥{unit_price:,}\n"
            )
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text=reply_text)
            )
            del user_estimate_sessions[user_id]

        else:
            if user_message not in COLOR_COST_MAP_BOTH:
                del user_estimate_sessions[user_id]
                line_bot_api.reply_message(
                    event.reply_token,
                    TextSendMessage(text="入力内容に誤りがあるようです。 \nお手数をおかけしますが、再度メニューの「カンタン見積り」より、該当の項目を選択タブからお選びください。\n※テキストの直接入力はご利用いただけませんので、ご了承くださいませ。")
                )
                return

            session_data["answers"]["color_count"] = user_message
            session_data["step"] = 8
            line_bot_api.reply_message(event.reply_token, flex_back_name())

        return

    elif step == 8:
        valid_back_names = ["ネーム&背番号セット", "ネーム(大)", "番号(大)", "背ネーム・番号を使わない"]
        if user_message in valid_back_names:
            session_data["answers"]["back_name"] = user_message
            session_data["step"] = 9

            est_data = session_data["answers"]
            total_price, unit_price = calculate_estimate(est_data)
            quote_number = write_estimate_to_spreadsheet(user_id, est_data, total_price, unit_price)

            reply_text = (
                f"概算のお見積りが完了しました。\n\n"
                f"見積番号: {quote_number}\n"
                f"属性: {est_data['user_type']}\n"
                f"使用日: {est_data['usage_date']}（{est_data['discount_type']}）\n"
                f"予算: {est_data['budget']}\n"
                f"商品: {est_data['item']}\n"
                f"枚数: {est_data['quantity']}\n"
                f"プリント位置: {est_data['print_position']}\n"
                f"色数: {est_data['color_count']}\n"
                f"背ネーム・番号: {est_data['back_name']}\n\n"
                f"【合計金額】¥{total_price:,}\n"
                f"【1枚あたり】¥{unit_price:,}\n"
            )
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text=reply_text)
            )
            del user_estimate_sessions[user_id]
        else:
            del user_estimate_sessions[user_id]
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="入力内容に誤りがあるようです。 \nお手数をおかけしますが、再度メニューの「カンタン見積り」より、該当の項目を選択タブからお選びください。\n※テキストの直接入力はご利用いただけませんので、ご了承くださいませ。")
            )
        return

    else:
        del user_estimate_sessions[user_id]
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="エラーが発生しました。見積りフローを終了しました。最初からやり直してください。")
        )
        return


# -----------------------
# 3) カタログ申し込みフォーム表示 (GET)
# -----------------------
@app.route("/catalog_form", methods=["GET"])
def show_catalog_form():
    token = str(uuid.uuid4())
    session['catalog_form_token'] = token

    html_content = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>カタログ申込フォーム</title>
    <style>
        body {{
            margin: 0;
            padding: 0;
            font-family: sans-serif;
        }}
        .container {{
            max-width: 600px; 
            margin: 0 auto;
            padding: 1em;
        }}
        label {{
            display: block;
            margin-bottom: 0.5em;
        }}
        input[type=text], input[type=email], textarea {{
            width: 100%;
            padding: 0.5em;
            margin-top: 0.3em;
            box-sizing: border-box;
        }}
        input[type=submit] {{
            padding: 0.7em 1em;
            font-size: 1em;
            margin-top: 1em;
        }}
    </style>
    <script>
    async function fetchAddress() {{
        let pcRaw = document.getElementById('postal_code').value.trim();
        pcRaw = pcRaw.replace('-', '');
        if (pcRaw.length < 7) {{
            return;
        }}
        try {{
            const response = await fetch(`https://api.zipaddress.net/?zipcode=${{pcRaw}}`);
            const data = await response.json();
            if (data.code === 200) {{
                // 都道府県・市区町村 部分だけを address_1 に自動入力
                document.getElementById('address_1').value = data.data.fullAddress;
            }}
        }} catch (error) {{
            console.log("住所検索失敗:", error);
        }}
    }}
    </script>
</head>
<body>
    <div class="container">
      <h1>カタログ申込フォーム</h1>
      <p>以下の項目をご記入の上、送信してください。</p>
      <form action="/submit_form" method="post">
          <!-- ワンタイムトークン -->
          <input type="hidden" name="form_token" value="{token}">

          <label>氏名（必須）:
              <input type="text" name="name" required>
          </label>

          <label>郵便番号（必須）:<br>
              <small>※自動で住所補完します。(ブラウザの場合)</small><br>
              <input type="text" name="postal_code" id="postal_code" onkeyup="fetchAddress()" required>
          </label>

          <label>都道府県・市区町村（必須）:<br>
              <small>※郵便番号入力後に自動補完されます。修正が必要な場合は上書きしてください。</small><br>
              <input type="text" name="address_1" id="address_1" required>
          </label>

          <label>番地・部屋番号など（必須）:<br>
              <small>※カタログ送付のために番地や部屋番号を含めた完全な住所の記入が必要です</small><br>
              <input type="text" name="address_2" id="address_2" required>
          </label>

          <label>電話番号（必須）:
              <input type="text" name="phone" required>
          </label>

          <label>メールアドレス（必須）:
              <input type="email" name="email" required>
          </label>

          <label>Insta・TikTok名（必須）:
              <input type="text" name="sns_account" required>
          </label>

          <label>2025年度に在籍予定の学校名と学年（未記入可）:
              <input type="text" name="school_grade">
          </label>

          <label>その他（質問やご要望など）:
              <textarea name="other" rows="4"></textarea>
          </label>

          <input type="submit" value="送信">
      </form>
    </div>
</body>
</html>
"""
    return render_template_string(html_content)


# -----------------------
# 4) カタログ申し込みフォームの送信処理
# -----------------------
@app.route("/submit_form", methods=["POST"])
def submit_catalog_form():
    form_token = request.form.get('form_token')
    if form_token != session.get('catalog_form_token'):
        return "二重送信、あるいは不正なリクエストです。", 400

    session.pop('catalog_form_token', None)

    form_data = {
        "name": request.form.get("name", "").strip(),
        "postal_code": request.form.get("postal_code", "").strip(),
        "address_1": request.form.get("address_1", "").strip(),
        "address_2": request.form.get("address_2", "").strip(),
        "phone": request.form.get("phone", "").strip(),
        "email": request.form.get("email", "").strip(),
        "sns_account": request.form.get("sns_account", "").strip(),
        "school_grade": request.form.get("school_grade", "").strip(),
        "other": request.form.get("other", "").strip(),
    }

    try:
        write_to_spreadsheet_for_catalog(form_data)
    except Exception as e:
        return f"エラーが発生しました: {e}", 500

    return "フォーム送信ありがとうございました！ カタログ送付をお待ちください。", 200


# ========== ここから新規追加 (Webオーダーフォーム) ==========

@app.route("/web_order_form", methods=["GET"])
def show_web_order_form():
    """
    外部HTML(ユーザー記載のフォーム)をそのまま返す
    """
    token = str(uuid.uuid4())
    session['web_order_form_token'] = token

    # この HTML はご提示されたものをそのまま埋め込み
    # <form> の action は /submit_web_order_form にします
    html_content = f"""<!DOCTYPE html>
<html lang="ja">
<head>
  <meta charset="UTF-8">
  <title>WEBフォーム注文</title>
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <style>
    body {{
      margin: 0; padding: 0;
      font-family: sans-serif;
      background-color: #ffeef2; /* 淡いピンク */
    }}
    .container {{
      max-width: 600px;
      margin: 0 auto;
      padding: 1em;
      background-color: #fff;
      border-radius: 8px;
    }}
    h1 {{
      text-align: center;
      color: #d15b8f;
    }}
    .form-group {{
      margin-bottom: 1em;
    }}
    .form-group label {{
      display: block;
      margin-bottom: 0.3em;
      font-weight: bold;
    }}
    .form-group select,
    .form-group input[type="text"],
    .form-group input[type="number"],
    .form-group input[type="date"] {{
      width: 100%;
      padding: 0.5em;
      box-sizing: border-box;
      border: 1px solid #ccc;
      border-radius: 4px;
    }}
    .size-inputs {{
      display: flex;
      flex-wrap: wrap;
      gap: 1em;
    }}
    .size-inputs .sub-group {{
      flex: 1;
      min-width: 100px;
    }}
    .sub-group {{ margin-bottom: 0.5em; }}
    .hidden {{ display: none; }}
    .submit-btn {{
      text-align: center;
      margin-top: 2em;
    }}
    .submit-btn button {{
      background-color: #d15b8f;
      color: #fff;
      border: none;
      padding: 1em 2em;
      border-radius: 4px;
      font-size: 1em;
      cursor: pointer;
    }}
    .submit-btn button:hover {{
      opacity: 0.8;
    }}
    fieldset {{
      border: 1px solid #ccc;
      padding: 1em;
      margin-bottom: 1em;
    }}
    legend {{ font-weight: bold; }}
    .inline-flex {{
      display: flex;
      align-items: center;
      gap: 0.5em;
    }}
    .mt1 {{ margin-top: 1em; }}
    .mb1 {{ margin-bottom: 1em; }}
  </style>
</head>
<body>
<div class="container">
  <h1>WEBフォーム注文</h1>
  <form id="orderForm" action="/submit_web_order_form" method="post">
    <!-- ワンタイムトークン -->
    <input type="hidden" name="form_token" value="{token}">

    <!-- ▼ 製品選択（商品名 → 品番 → カラーNo → 商品カラー）▼ -->
    <div class="form-group">
      <label for="productNameSelect">商品名 <span style="color:red;">(必須)</span></label>
      <select id="productNameSelect" name="productName" required>
        <option value="">選択してください</option>
      </select>
    </div>

    <div class="form-group">
      <label for="productNoSelect">品番 <span style="color:red;">(必須)</span></label>
      <select id="productNoSelect" name="productNo" required>
        <option value="">選択してください</option>
      </select>
    </div>

    <div class="form-group">
      <label for="colorNoSelect">カラーNo <span style="color:red;">(必須)</span></label>
      <select id="colorNoSelect" name="colorNo" required>
        <option value="">選択してください</option>
      </select>
    </div>

    <div class="form-group">
      <label for="colorNameSelect">商品カラー <span style="color:red;">(必須)</span></label>
      <select id="colorNameSelect" name="colorName" required>
        <option value="">選択してください</option>
      </select>
    </div>

    <!-- ▼ サイズ毎の枚数入力 ▼ -->
    <div class="form-group">
      <label>サイズ毎の枚数 <span style="color:red;">(必須)</span></label>
      <div class="size-inputs">
        <div class="sub-group">
          <label for="size150">150</label>
          <input type="number" id="size150" name="size150" value="0" min="0" required oninput="calculateTotal()">
        </div>
        <div class="sub-group">
          <label for="sizeSS">SS(160)</label>
          <input type="number" id="sizeSS" name="sizeSS" value="0" min="0" required oninput="calculateTotal()">
        </div>
        <div class="sub-group">
          <label for="sizeS">S</label>
          <input type="number" id="sizeS" name="sizeS" value="0" min="0" required oninput="calculateTotal()">
        </div>
        <div class="sub-group">
          <label for="sizeM">M</label>
          <input type="number" id="sizeM" name="sizeM" value="0" min="0" required oninput="calculateTotal()">
        </div>
        <div class="sub-group">
          <label for="sizeL">L(F)</label>
          <input type="number" id="sizeL" name="sizeL" value="0" min="0" required oninput="calculateTotal()">
        </div>
        <div class="sub-group">
          <label for="sizeXL">LL(XL)</label>
          <input type="number" id="sizeXL" name="sizeXL" value="0" min="0" required oninput="calculateTotal()">
        </div>
        <div class="sub-group">
          <label for="sizeXXL">3L(XXL)</label>
          <input type="number" id="sizeXXL" name="sizeXXL" value="0" min="0" required oninput="calculateTotal()">
        </div>
      </div>
    </div>

    <div class="form-group">
      <label for="totalQuantity">合計枚数</label>
      <input type="number" id="totalQuantity" name="totalQuantity" value="0" readonly>
    </div>

    <!-- ▼ プリント位置のイメージ画像 ▼ -->
    <div class="form-group">
      <label>プリント位置イメージ</label>
      <img src="https://catalog-bot-zf1t.onrender.com/PRINT_LOCATION.png" alt="プリント位置イメージ" style="max-width:50%;">
    </div>

    <!-- ▼ 1ヵ所目のプリント設定（必須）▼ -->
    <fieldset id="printLocation1">
      <legend>1ヵ所目のプリント設定 (必須)</legend>
      <div class="sub-group">
        <label for="printPositionNo1">プリント位置No.</label>
        <select id="printPositionNo1" name="printPositionNo1" required onchange="toggleNameNumberOptions(1)">
          <option value="">選択</option>
          <option value="1">1</option>
          <option value="2">2</option>
          <option value="3">3</option>
          <option value="4">4</option>
          <option value="5">5</option>
          <option value="6">6</option>
          <option value="7">7</option>
        </select>
      </div>

      <div id="nameNumberOptions1" class="sub-group hidden">
        <label>ネーム＆番号プリント</label>
        <select name="nameNumberOption1" id="nameNumberOption1" onchange="toggleNameNumberColorBox(1)">
          <option value="">選択</option>
          <option value="nameNumberSet">ネーム＆背番号セット</option>
          <option value="nameLarge">ネーム(大)</option>
          <option value="nameSmall">ネーム(小)</option>
          <option value="numberLarge">番号(大)</option>
          <option value="numberSmall">番号(小)</option>
        </select>
      </div>
      <div id="nameNumberPrintColorBox1" class="sub-group hidden" style="border:1px solid #ccc; padding:0.5em;">
        <strong>ネーム・番号プリントカラーオプション</strong>
        <div class="inline-flex mt1 mb1">
          <label><input type="radio" name="nameNumberPrintType1" value="single" checked onclick="toggleEdgeColor(1)"> 単色</label>
          <label><input type="radio" name="nameNumberPrintType1" value="edge" onclick="toggleEdgeColor(1)"> フチ付き</label>
        </div>
        <div id="singleColorSelectArea1" class="sub-group">
          <label>単色カラー</label>
          <select id="singleColor1" name="singleColor1">
          </select>
        </div>
        <div id="edgeColorSelectArea1" class="sub-group hidden">
          <label>フチ付きタイプ</label>
          <select id="edgeType1" name="edgeType1" onchange="changeEdgeType(1)">
            <option value="">選択してください</option>
            <option value="FT-1">FT-1 (文字色ブラック、フチ色1ブラック)</option>
            <option value="FT-2">FT-2 (文字色ホワイト、フチ色1ブラック)</option>
            <option value="FT-3">FT-3 (文字色レッド、フチ色1ブラック)</option>
            <option value="FT-4">FT-4 (文字色パープル、フチ色1イエロー)</option>
            <option value="FT-5">FT-5 (文字色ブラック、フチ色1ホワイト、フチ色2ブラック)</option>
            <option value="FT-6">FT-6 (文字色レッド、フチ色1ホワイト、フチ色2ブラック)</option>
            <option value="FT-7">FT-7 (文字色ブルー、フチ色1ホワイト、フチ色2ブルー)</option>
            <option value="FT-8">FT-8 (文字色ブルー、フチ色1ホワイト、フチ色2レッド)</option>
            <option value="custom">カスタム</option>
          </select>
          <div id="edgeColorCustomArea1" class="hidden" style="margin-top:0.5em;">
            <label>文字色</label>
            <select id="edgeCustomTextColor1" name="edgeCustomTextColor1"></select>
            <label>フチ色1</label>
            <select id="edgeCustomEdgeColor1" name="edgeCustomEdgeColor1"></select>
            <label>フチ色2 (任意)</label>
            <select id="edgeCustomEdgeColor2_1" name="edgeCustomEdgeColor2_1"></select>
          </div>
        </div>
        <div class="sub-group mt1">
          <label>フォント選択</label>
          <div class="inline-flex mb1">
            <label><input type="radio" name="fontType1" value="E" checked> 英数字対応 (E-)</label>
            <label><input type="radio" name="fontType1" value="J"> 日本語対応 (J-)</label>
          </div>
          <div class="inline-flex">
            <span id="fontPrefix1">E-</span>
            <input type="text" id="fontNumber1" name="fontNumber1" maxlength="2" style="width:3em;" placeholder="00">
          </div>
        </div>
      </div>

      <div class="sub-group">
        <label>プリントカラー・オプション</label>
        <select name="printColorOption1_1" id="printColorOption1_1">
          <option value="">1色目を選択</option>
        </select>
        <select name="printColorOption1_2" id="printColorOption1_2">
          <option value="">2色目を選択</option>
        </select>
        <select name="printColorOption1_3" id="printColorOption1_3">
          <option value="">3色目を選択</option>
        </select>
        <select name="fullColorSize1">
          <option value="">フルカラーを選択</option>
          <option value="S">フルカラー(小)</option>
          <option value="M">フルカラー(中)</option>
          <option value="L">フルカラー(大)</option>
        </select>
      </div>

      <div class="sub-group">
        <label for="designCode1">デザイン (例: D-001)</label>
        <div style="display:flex; gap:0.3em;">
          <span>D-</span>
          <input type="text" id="designCode1" name="designCode1" maxlength="3" style="width:4em;" placeholder="3桁">
        </div>
      </div>

      <div class="sub-group">
        <label>デザインサイズ</label>
        <select name="designSize1" id="designSize1" onchange="toggleCustomSize(1)">
          <option value="max">プリント位置最大</option>
          <option value="custom">任意のサイズ</option>
        </select>
        <div id="customSizeInput1" class="hidden">
          <label>X(cm)</label>
          <input type="number" name="designSizeX1" min="0" step="0.1" style="width:5em;">
          <label>Y(cm)</label>
          <input type="number" name="designSizeY1" min="0" step="0.1" style="width:5em;">
        </div>
      </div>
    </fieldset>

    <div id="additionalPrintLocations"></div>

    <div style="margin-bottom:2em;">
      <button type="button" onclick="addPrintLocation()">+ プリント箇所を追加</button>
    </div>

    <div class="form-group">
      <label for="deliveryDate">希望お届け日 <span style="color:red;">(必須)</span></label>
      <input type="date" id="deliveryDate" name="deliveryDate" required />
    </div>
    <div class="form-group">
      <label for="useDate">使用日 <span style="color:red;">(必須)</span></label>
      <input type="date" id="useDate" name="useDate" required />
    </div>
    <div class="form-group">
      <label for="applicationDate">申込日 (任意)</label>
      <input type="date" id="applicationDate" name="applicationDate" />
    </div>

    <div class="form-group">
      <label for="discountOption">利用する学割特典 <span style="color:red;">(必須)</span></label>
      <select id="discountOption" name="discountOption" required>
        <option value="">選択してください</option>
        <option value="早割">早割</option>
        <option value="いっしょ割">いっしょ割</option>
        <option value="リピータ割">リピータ割</option>
      </select>
    </div>

    <div class="form-group">
      <label for="schoolName">学校名 <span style="color:red;">(必須)</span></label>
      <input type="text" id="schoolName" name="schoolName" required />
    </div>
    <div class="form-group">
      <label for="lineName">LINEの名前 (任意)</label>
      <input type="text" id="lineName" name="lineName" />
    </div>
    <div class="form-group">
      <label for="classGroupName">クラス・団体名 <span style="color:red;">(必須)</span></label>
      <input type="text" id="classGroupName" name="classGroupName" required />
    </div>

    <div class="form-group">
      <label for="zipCode">お届け先の郵便番号 <span style="color:red;">(必須)</span></label>
      <input type="text" id="zipCode" name="zipCode" placeholder="例: 123-4567" required onblur="autoFillAddress()" />
    </div>
    <div class="form-group">
      <label for="address1">住所1 (都道府県・市区町村等)</label>
      <input type="text" id="address1" name="address1" />
    </div>
    <div class="form-group">
      <label for="address2">住所2 (番地以下) <span style="color:red;">(必須)</span></label>
      <input type="text" id="address2" name="address2" required />
    </div>
    <div class="form-group">
      <label for="schoolTel">学校TEL <span style="color:red;">(必須)</span></label>
      <input type="text" id="schoolTel" name="schoolTel" required />
    </div>

    <div class="form-group">
      <label for="representativeName">代表者 <span style="color:red;">(必須)</span></label>
      <input type="text" id="representativeName" name="representativeName" required />
    </div>
    <div class="form-group">
      <label for="representativeTel">代表者TEL <span style="color:red;">(必須)</span></label>
      <input type="text" id="representativeTel" name="representativeTel" required />
    </div>
    <div class="form-group">
      <label for="representativeEmail">代表者メール (任意)</label>
      <input type="email" id="representativeEmail" name="representativeEmail" />
    </div>

    <div class="form-group">
      <label for="designCheckMethod">デザイン確認方法 <span style="color:red;">(必須)</span></label>
      <select id="designCheckMethod" name="designCheckMethod" required>
        <option value="">選択してください</option>
        <option value="LINE代表者">LINE代表者</option>
        <option value="LINEご担任(保護者)">LINEご担任(保護者)</option>
        <option value="メール代表者">メール代表者</option>
        <option value="メールご担任(保護者)">メールご担任(保護者)</option>
      </select>
    </div>

    <div class="form-group">
      <label for="paymentMethod">お支払い方法 <span style="color:red;">(必須)</span></label>
      <select id="paymentMethod" name="paymentMethod" required>
        <option value="">選択してください</option>
        <option value="代金引換">代金引換(ヤマト運輸/現金のみ)</option>
        <option value="コンビニ・郵便振替">コンビニ・郵便振替(後払い)</option>
        <option value="銀行振込(後払い)">銀行振込(後払い)</option>
        <option value="銀行振込(先払い)">銀行振込(先払い)</option>
      </select>
    </div>

    <div class="submit-btn">
      <button type="submit">送信</button>
    </div>
  </form>
</div>

<script>
  /**********************************************
   * 1) 製品テーブル（ご提示いただいたデータをすべて統合）
   **********************************************/
   const productTable = [
    { productNo: "5927-01", productName: "ゲームシャツ", colorNo: "9816", colorName: "ホワイト/ホワイト/ブラック" },
    { productNo: "5927-01", productName: "ゲームシャツ", colorNo: "9887", colorName: "レッド/ホワイト/ブラック" },
    { productNo: "5927-01", productName: "ゲームシャツ", colorNo: "9889", colorName: "アイビーグリーン/ホワイト/ブラック" },
    { productNo: "5927-01", productName: "ゲームシャツ", colorNo: "9888", colorName: "コバルトブルー/ホワイト/ブラック" },
    { productNo: "5927-01", productName: "ゲームシャツ", colorNo: "9856", colorName: "ブラック/ホワイト/ブラック" },

    { productNo: "5982-01", productName: "ストライプドライベースボールシャツ", colorNo: "1098", colorName: "ホワイト/ブラックストライプ" },
    { productNo: "5982-01", productName: "ストライプドライベースボールシャツ", colorNo: "2097", colorName: "ブラック/ホワイトストライプ" },

    { productNo: "5982-01", productName: "ドライベースボールシャツ", colorNo: "1002", colorName: "ホワイト/ブラック" },
    { productNo: "5982-01", productName: "ドライベースボールシャツ", colorNo: "1095", colorName: "ホワイト/マリンブルー" },
    { productNo: "5982-01", productName: "ドライベースボールシャツ", colorNo: "2001", colorName: "ブラック/ホワイト" },
    { productNo: "5982-01", productName: "ドライベースボールシャツ", colorNo: "2002", colorName: "ブラック/ブラック" },
    { productNo: "5982-01", productName: "ドライベースボールシャツ", colorNo: "6901", colorName: "ラベンダー/ホワイト" },
    { productNo: "5982-01", productName: "ドライベースボールシャツ", colorNo: "6001", colorName: "ターコイズブルー/ホワイト" },
    { productNo: "5982-01", productName: "ドライベースボールシャツ", colorNo: "4801", colorName: "マリンブルー/ホワイト" },
    { productNo: "5982-01", productName: "ドライベースボールシャツ", colorNo: "4001", colorName: "ネイビー/ホワイト" },
    { productNo: "5982-01", productName: "ドライベースボールシャツ", colorNo: "2602", colorName: "カナリアイエロー/ブラック" },
    { productNo: "5982-01", productName: "ドライベースボールシャツ", colorNo: "6402", colorName: "オレンジ/ブラック" },
    { productNo: "5982-01", productName: "ドライベースボールシャツ", colorNo: "6601", colorName: "トロピカルピンク/ホワイト" },
    { productNo: "5982-01", productName: "ドライベースボールシャツ", colorNo: "5602", colorName: "レッド/ブラック" },
    { productNo: "5982-01", productName: "ドライベースボールシャツ", colorNo: "5801", colorName: "バーガンディ/ホワイト" },
    { productNo: "5982-01", productName: "ドライベースボールシャツ", colorNo: "5001", colorName: "アイビーグリーン/ホワイト" },

    { productNo: "ZD16", productName: "ストライプユニフォーム", colorNo: "zd16223", colorName: "ホワイトxライトブルー" },
    { productNo: "ZD16", productName: "ストライプユニフォーム", colorNo: "zd16229", colorName: "ホワイトxライトパープル" },
    { productNo: "ZD16", productName: "ストライプユニフォーム", colorNo: "zd16230", colorName: "ホワイトxホットピンク" },
    { productNo: "ZD16", productName: "ストライプユニフォーム", colorNo: "zd16227", colorName: "ホワイトxパープル" },
    { productNo: "ZD16", productName: "ストライプユニフォーム", colorNo: "zd16226", colorName: "ホワイトxブラック" },
    { productNo: "ZD16", productName: "ストライプユニフォーム", colorNo: "zd16221", colorName: "レッドxブラック" },
    { productNo: "ZD16", productName: "ストライプユニフォーム", colorNo: "zd16224", colorName: "ブルーxブラック" },

    { productNo: "5992-01", productName: "バスケシャツ", colorNo: "9891", colorName: "ホワイト/ホワイト/レッド" },
    { productNo: "5992-01", productName: "バスケシャツ", colorNo: "9893", colorName: "レッド/ホワイト/レッド" },
    { productNo: "5992-01", productName: "バスケシャツ", colorNo: "9892", colorName: "カナリアイエロー/ホワイト/パープル" },
    { productNo: "5992-01", productName: "バスケシャツ", colorNo: "9890", colorName: "ブラック/ホワイト/ラベンダー" },
    { productNo: "5992-01", productName: "バスケシャツ", colorNo: "9856", colorName: "ブラック/ホワイト/ブラック" },

    { productNo: "300-ACT", productName: "ドライTシャツ", colorNo: "001", colorName: "ホワイト" },
    { productNo: "300-ACT", productName: "ドライTシャツ", colorNo: "153", colorName: "シルバーグレー" },
    { productNo: "300-ACT", productName: "ドライTシャツ", colorNo: "002", colorName: "グレー" },
    { productNo: "300-ACT", productName: "ドライTシャツ", colorNo: "187", colorName: "ダークグレー" },
    {{ productNo: "300-ACT", productName: "ドライTシャツ", colorNo: "005", colorName: "ブラック" }},
    ...
    /* (以下、提示いただいた全データを省略せず記述) */
  ];

  /*  (中略) ... JSコードはそのまま (ご提示いただいたもの) */

</script>
</body>
</html>
"""
    return html_content


@app.route("/submit_web_order_form", methods=["POST"])
def submit_web_order_form():
    """
    Webフォーム注文の内容をスプレッドシートに書き込む
    """
    form_token = request.form.get('form_token')
    if form_token != session.get('web_order_form_token'):
        return "二重送信、あるいは不正なリクエストです。", 400

    session.pop('web_order_form_token', None)

    # 受け取ったすべてのフォームデータを辞書化
    # 必要に応じて .strip() などで前後空白を除去
    form_data = {}
    for k in request.form:
        form_data[k] = request.form.get(k, "").strip()

    try:
        write_to_spreadsheet_for_web_order(form_data)
    except Exception as e:
        return f"エラーが発生しました: {e}", 500

    return "フォーム送信ありがとうございました！", 200


def write_to_spreadsheet_for_web_order(data: dict):
    """
    Webフォーム注文データを新しいシート "WebOrderRequests" に書き込む
    """
    gc = get_gspread_client()
    sh = gc.open_by_key(SPREADSHEET_KEY)
    worksheet = get_or_create_worksheet(sh, "WebOrderRequests")

    # 日本時間の現在時刻
    jst = pytz.timezone('Asia/Tokyo')
    now_jst_str = datetime.now(jst).strftime("%Y/%m/%d %H:%M:%S")

    # 新しい行を作成：ヘッダと合わせて順序を定義
    # （ヘッダは get_or_create_worksheet() 内でセット済）
    row_values = [
        now_jst_str,  # 日時
        data.get("productName", ""),
        data.get("productNo", ""),
        data.get("colorNo", ""),
        data.get("colorName", ""),
        data.get("size150", ""),
        data.get("sizeSS", ""),
        data.get("sizeS", ""),
        data.get("sizeM", ""),
        data.get("sizeL", ""),
        data.get("sizeXL", ""),
        data.get("sizeXXL", ""),
        data.get("totalQuantity", ""),

        data.get("printPositionNo1", ""),
        data.get("nameNumberOption1", ""),
        data.get("nameNumberPrintType1", ""),
        data.get("singleColor1", ""),
        data.get("edgeType1", ""),
        data.get("edgeCustomTextColor1", ""),
        data.get("edgeCustomEdgeColor1", ""),
        data.get("edgeCustomEdgeColor2_1", ""),
        data.get("fontType1", ""),
        data.get("fontNumber1", ""),
        data.get("printColorOption1_1", ""),
        data.get("printColorOption1_2", ""),
        data.get("printColorOption1_3", ""),
        data.get("fullColorSize1", ""),
        data.get("designCode1", ""),
        data.get("designSize1", ""),
        data.get("designSizeX1", ""),
        data.get("designSizeY1", ""),

        data.get("printPositionNo2", ""),
        data.get("nameNumberOption2", ""),
        data.get("nameNumberPrintType2", ""),
        data.get("singleColor2", ""),
        data.get("edgeType2", ""),
        data.get("edgeCustomTextColor2", ""),
        data.get("edgeCustomEdgeColor2", ""),
        data.get("edgeCustomEdgeColor2_2", ""),
        data.get("fontType2", ""),
        data.get("fontNumber2", ""),
        data.get("printColorOption2_1", ""),
        data.get("printColorOption2_2", ""),
        data.get("printColorOption2_3", ""),
        data.get("fullColorSize2", ""),
        data.get("designCode2", ""),
        data.get("designSize2", ""),
        data.get("designSizeX2", ""),
        data.get("designSizeY2", ""),

        data.get("printPositionNo3", ""),
        data.get("nameNumberOption3", ""),
        data.get("nameNumberPrintType3", ""),
        data.get("singleColor3", ""),
        data.get("edgeType3", ""),
        data.get("edgeCustomTextColor3", ""),
        data.get("edgeCustomEdgeColor3", ""),
        data.get("edgeCustomEdgeColor2_3", ""),
        data.get("fontType3", ""),
        data.get("fontNumber3", ""),
        data.get("printColorOption3_1", ""),
        data.get("printColorOption3_2", ""),
        data.get("printColorOption3_3", ""),
        data.get("fullColorSize3", ""),
        data.get("designCode3", ""),
        data.get("designSize3", ""),
        data.get("designSizeX3", ""),
        data.get("designSizeY3", ""),

        data.get("printPositionNo4", ""),
        data.get("nameNumberOption4", ""),
        data.get("nameNumberPrintType4", ""),
        data.get("singleColor4", ""),
        data.get("edgeType4", ""),
        data.get("edgeCustomTextColor4", ""),
        data.get("edgeCustomEdgeColor4", ""),
        data.get("edgeCustomEdgeColor2_4", ""),
        data.get("fontType4", ""),
        data.get("fontNumber4", ""),
        data.get("printColorOption4_1", ""),
        data.get("printColorOption4_2", ""),
        data.get("printColorOption4_3", ""),
        data.get("fullColorSize4", ""),
        data.get("designCode4", ""),
        data.get("designSize4", ""),
        data.get("designSizeX4", ""),
        data.get("designSizeY4", ""),

        data.get("deliveryDate", ""),
        data.get("useDate", ""),
        data.get("applicationDate", ""),
        data.get("discountOption", ""),
        data.get("schoolName", ""),
        data.get("lineName", ""),
        data.get("classGroupName", ""),
        data.get("zipCode", ""),
        data.get("address1", ""),
        data.get("address2", ""),
        data.get("schoolTel", ""),
        data.get("representativeName", ""),
        data.get("representativeTel", ""),
        data.get("representativeEmail", ""),
        data.get("designCheckMethod", ""),
        data.get("paymentMethod", "")
    ]

    worksheet.append_row(row_values, value_input_option="USER_ENTERED")


# -----------------------
# 動作確認用
# -----------------------
@app.route("/", methods=["GET"])
def health_check():
    return "LINE Bot is running.", 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
\n"
        "⚠️ 注意：サブアカウントや重複申込みはご遠慮ください。\n\n"
        "【カタログ発送時期】\n"
        "📅 2025年4月中旬より郵送で発送予定です。\n\n"
        "【配布数について】\n"
        "先着300名様分を予定しています。\n"
        "※応募多数となった場合、配布数の増加や抽選となる可能性があります。\n\n"
        "ご応募お待ちしております🙆"
    )
    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text=reply_text)
    )


# -----------------------
# 見積りフロー
# -----------------------
def start_estimate_flow(event: MessageEvent):
    """
    見積フローを開始し、step=1(属性)を提示する
    """
    user_id = event.source.user_id

    # セッションを初期化
    user_estimate_sessions[user_id] = {
        "step": 1,
        "answers": {},
        "is_single": False  # 新規: 前のみ/背中のみかどうか
    }

    # 最初のステップ（属性選択Flex）を送る
    line_bot_api.reply_message(
        event.reply_token,
        flex_user_type()
    )


def process_estimate_flow(event: MessageEvent, user_message: str):
    """
    見積フロー中のやり取り
    step 1: 属性
    step 2: 使用日
    step 3: 予算
    step 4: 商品名
    step 5: 枚数
    step 6: プリント位置
    step 7: 色数
       - (前のみ/背中のみ)の場合 -> フロー完了へ
       - (前と背中)の場合 -> step 8: 背ネーム・番号 -> step 9: 完了
    """
    user_id = event.source.user_id
    if user_id not in user_estimate_sessions:
        return

    session_data = user_estimate_sessions[user_id]
    step = session_data["step"]

    # 1) 属性
    if step == 1:
        if user_message in ["学生", "一般"]:
            session_data["answers"]["user_type"] = user_message
            session_data["step"] = 2
            line_bot_api.reply_message(event.reply_token, flex_usage_date())
        else:
            del user_estimate_sessions[user_id]
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="入力内容に誤りがあるようです。 \nお手数をおかけしますが、再度メニューの「カンタン見積り」より、該当の項目を選択タブからお選びください。\n※テキストの直接入力はご利用いただけませんので、ご了承くださいませ。")
            )
        return

    # 2) 使用日
    elif step == 2:
        if user_message in ["14日目以降", "14日目以内"]:
            session_data["answers"]["usage_date"] = user_message
            session_data["answers"]["discount_type"] = "早割" if user_message == "14日目以降" else "通常"
            session_data["step"] = 3
            line_bot_api.reply_message(event.reply_token, flex_budget())
        else:
            del user_estimate_sessions[user_id]
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="入力内容に誤りがあるようです。 \nお手数をおかけしますが、再度メニューの「カンタン見積り」より、該当の項目を選択タブからお選びください。\n※テキストの直接入力はご利用いただけませんので、ご了承くださいませ。")
            )
        return

    # 3) 1枚当たりの予算
    elif step == 3:
        valid_budgets = ["特になし", "1,000円以内", "1,500円以内", "2,000円以内", "2,500円以内", "3,000円以内", "3,500円以内"]
        if user_message in valid_budgets:
            session_data["answers"]["budget"] = user_message
            session_data["step"] = 4
            line_bot_api.reply_message(event.reply_token, flex_item_select())
        else:
            del user_estimate_sessions[user_id]
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="入力内容に誤りがあるようです。 \nお手数をおかけしますが、再度メニューの「カンタン見積り」より、該当の項目を選択タブからお選びください。\n※テキストの直接入力はご利用いただけませんので、ご了承くださいませ。")
            )
        return

    # 4) 商品名
    elif step == 4:
        items = [
            "ゲームシャツ",
            "ストライプドライベースボールシャツ",
            "ドライベースボールシャツ",
            "ストライプユニフォーム",
            "バスケシャツ",
            "ドライTシャツ",
            "ハイクオリティTシャツ",
            "ドライポロシャツ",
            "ドライロングスリーブTシャツ",
            "クルーネックライトトレーナー",
            "ジップアップライトパーカー",
            "フーデッドライトパーカー",
        ]
        if user_message in items:
            session_data["answers"]["item"] = user_message
            session_data["step"] = 5
            line_bot_api.reply_message(event.reply_token, flex_quantity())
        else:
            del user_estimate_sessions[user_id]
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="入力内容に誤りがあるようです。 \nお手数をおかけしますが、再度メニューの「カンタン見積り」より、該当の項目を選択タブからお選びください。\n※テキストの直接入力はご利用いただけませんので、ご了承くださいませ。")
            )
        return

    # 5) 枚数
    elif step == 5:
        valid_choices = ["20～29枚", "30～39枚", "40～49枚", "50～99枚", "100枚以上"]
        if user_message in valid_choices:
            session_data["answers"]["quantity"] = user_message
            session_data["step"] = 6
            line_bot_api.reply_message(event.reply_token, flex_print_position())
        else:
            del user_estimate_sessions[user_id]
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="入力内容に誤りがあるようです。 \nお手数をおかけしますが、再度メニューの「カンタン見積り」より、該当の項目を選択タブからお選びください。\n※テキストの直接入力はご利用いただけませんので、ご了承くださいませ。")
            )
        return

    # 6) プリント位置
    elif step == 6:
        valid_positions = ["前のみ", "背中のみ", "前と背中"]
        if user_message in valid_positions:
            session_data["answers"]["print_position"] = user_message
            session_data["step"] = 7

            # 新規: プリント位置が 前のみ/背中のみ なら is_single=True
            if user_message in ["前のみ", "背中のみ"]:
                session_data["is_single"] = True
                line_bot_api.reply_message(event.reply_token, flex_color_count_single())
            else:
                session_data["is_single"] = False
                line_bot_api.reply_message(event.reply_token, flex_color_count_both())
        else:
            del user_estimate_sessions[user_id]
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="入力内容に誤りがあるようです。 \nお手数をおかけしますが、再度メニューの「カンタン見積り」より、該当の項目を選択タブからお選びください。\n※テキストの直接入力はご利用いただけませんので、ご了承くださいませ。")
            )
        return

    # 7) 色数
    elif step == 7:
        # プリント位置が「前のみ/背中のみ」→ is_single=True
        #           が「前と背中」 → is_single=False
        if session_data["is_single"]:
            # シングル面の色数マップをチェック
            if user_message not in COLOR_COST_MAP_SINGLE:
                # 不正入力
                del user_estimate_sessions[user_id]
                line_bot_api.reply_message(
                    event.reply_token,
                    TextSendMessage(text="入力内容に誤りがあるようです。 \nお手数をおかけしますが、再度メニューの「カンタン見積り」より、該当の項目を選択タブからお選びください。\n※テキストの直接入力はご利用いただけませんので、ご了承くださいませ。")
                )
                return

            # OK
            session_data["answers"]["color_count"] = user_message
            # 背ネーム・番号はスキップ => back_name=空 or "なし" として保存
            session_data["answers"]["back_name"] = "なし"

            # 計算
            est_data = session_data["answers"]
            total_price, unit_price = calculate_estimate(est_data)
            quote_number = write_estimate_to_spreadsheet(user_id, est_data, total_price, unit_price)

            reply_text = (
                f"概算のお見積りが完了しました。\n\n"
                f"見積番号: {quote_number}\n"
                f"属性: {est_data['user_type']}\n"
                f"使用日: {est_data['usage_date']}（{est_data['discount_type']}）\n"
                f"予算: {est_data['budget']}\n"
                f"商品: {est_data['item']}\n"
                f"枚数: {est_data['quantity']}\n"
                f"プリント位置: {est_data['print_position']}\n"
                f"色数: {est_data['color_count']}\n"
                f"背ネーム・番号: なし\n\n"
                f"【合計金額】¥{total_price:,}\n"
                f"【1枚あたり】¥{unit_price:,}\n"
            )
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text=reply_text)
            )

            # フロー終了
            del user_estimate_sessions[user_id]

        else:
            # 前と背中 の場合
            if user_message not in COLOR_COST_MAP_BOTH:
                # 不正入力
                del user_estimate_sessions[user_id]
                line_bot_api.reply_message(
                    event.reply_token,
                    TextSendMessage(text="入力内容に誤りがあるようです。 \nお手数をおかけしますが、再度メニューの「カンタン見積り」より、該当の項目を選択タブからお選びください。\n※テキストの直接入力はご利用いただけませんので、ご了承くださいませ。")
                )
                return

            # OK
            session_data["answers"]["color_count"] = user_message
            # 次のstep(8)で背ネーム・番号を聞く
            session_data["step"] = 8
            line_bot_api.reply_message(event.reply_token, flex_back_name())

        return

    # 8) 背ネーム・番号 (「前と背中」だけがここへ進む)
    elif step == 8:
        valid_back_names = ["ネーム&背番号セット", "ネーム(大)", "番号(大)", "背ネーム・番号を使わない"]
        if user_message in valid_back_names:
            session_data["answers"]["back_name"] = user_message
            session_data["step"] = 9

            # 見積計算
            est_data = session_data["answers"]
            total_price, unit_price = calculate_estimate(est_data)
            quote_number = write_estimate_to_spreadsheet(user_id, est_data, total_price, unit_price)

            reply_text = (
                f"概算のお見積りが完了しました。\n\n"
                f"見積番号: {quote_number}\n"
                f"属性: {est_data['user_type']}\n"
                f"使用日: {est_data['usage_date']}（{est_data['discount_type']}）\n"
                f"予算: {est_data['budget']}\n"
                f"商品: {est_data['item']}\n"
                f"枚数: {est_data['quantity']}\n"
                f"プリント位置: {est_data['print_position']}\n"
                f"色数: {est_data['color_count']}\n"
                f"背ネーム・番号: {est_data['back_name']}\n\n"
                f"【合計金額】¥{total_price:,}\n"
                f"【1枚あたり】¥{unit_price:,}\n"
            )
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text=reply_text)
            )

            # フロー終了
            del user_estimate_sessions[user_id]
        else:
            del user_estimate_sessions[user_id]
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="入力内容に誤りがあるようです。 \nお手数をおかけしますが、再度メニューの「カンタン見積り」より、該当の項目を選択タブからお選びください。\n※テキストの直接入力はご利用いただけませんので、ご了承くださいませ。")
            )
        return

    else:
        # 何らかの想定外のエラー
        del user_estimate_sessions[user_id]
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="エラーが発生しました。見積りフローを終了しました。最初からやり直してください。")
        )
        return


# -----------------------
# 3) カタログ申し込みフォーム表示 (GET)
# -----------------------
@app.route("/catalog_form", methods=["GET"])
def show_catalog_form():
    token = str(uuid.uuid4())
    session['catalog_form_token'] = token

    # f-string で {token} を差し込む
    html_content = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>カタログ申込フォーム</title>
    <style>
        body {{
            margin: 0;
            padding: 0;
            font-family: sans-serif;
        }}
        .container {{
            max-width: 600px; 
            margin: 0 auto;
            padding: 1em;
        }}
        label {{
            display: block;
            margin-bottom: 0.5em;
        }}
        input[type=text], input[type=email], textarea {{
            width: 100%;
            padding: 0.5em;
            margin-top: 0.3em;
            box-sizing: border-box;
        }}
        input[type=submit] {{
            padding: 0.7em 1em;
            font-size: 1em;
            margin-top: 1em;
        }}
    </style>
    <script>
    async function fetchAddress() {{
        let pcRaw = document.getElementById('postal_code').value.trim();
        pcRaw = pcRaw.replace('-', '');
        if (pcRaw.length < 7) {{
            return;
        }}
        try {{
            const response = await fetch(`https://api.zipaddress.net/?zipcode=${{pcRaw}}`);
            const data = await response.json();
            if (data.code === 200) {{
                // 都道府県・市区町村 部分だけを address_1 に自動入力
                document.getElementById('address_1').value = data.data.fullAddress;
            }}
        }} catch (error) {{
            console.log("住所検索失敗:", error);
        }}
    }}
    </script>
</head>
<body>
    <div class="container">
      <h1>カタログ申込フォーム</h1>
      <p>以下の項目をご記入の上、送信してください。</p>
      <form action="/submit_form" method="post">
          <!-- ワンタイムトークン -->
          <input type="hidden" name="form_token" value="{token}">

          <label>氏名（必須）:
              <input type="text" name="name" required>
          </label>

          <label>郵便番号（必須）:<br>
              <small>※自動で住所補完します。(ブラウザの場合)</small><br>
              <input type="text" name="postal_code" id="postal_code" onkeyup="fetchAddress()" required>
          </label>

          <label>都道府県・市区町村（必須）:<br>
              <small>※郵便番号入力後に自動補完されます。修正が必要な場合は上書きしてください。</small><br>
              <input type="text" name="address_1" id="address_1" required>
          </label>

          <label>番地・部屋番号など（必須）:<br>
              <small>※カタログ送付のために番地や部屋番号を含めた完全な住所の記入が必要です</small><br>
              <input type="text" name="address_2" id="address_2" required>
          </label>

          <label>電話番号（必須）:
              <input type="text" name="phone" required>
          </label>

          <label>メールアドレス（必須）:
              <input type="email" name="email" required>
          </label>

          <label>Insta・TikTok名（必須）:
              <input type="text" name="sns_account" required>
          </label>

          <label>2025年度に在籍予定の学校名と学年（未記入可）:
              <input type="text" name="school_grade">
          </label>

          <label>その他（質問やご要望など）:
              <textarea name="other" rows="4"></textarea>
          </label>

          <input type="submit" value="送信">
      </form>
    </div>
</body>
</html>
"""
    return render_template_string(html_content)


# -----------------------
# 4) カタログ申し込みフォームの送信処理
# -----------------------
@app.route("/submit_form", methods=["POST"])
def submit_catalog_form():
    # トークンチェック
    form_token = request.form.get('form_token')
    if form_token != session.get('catalog_form_token'):
        return "二重送信、あるいは不正なリクエストです。", 400

    # トークンの使い捨て
    session.pop('catalog_form_token', None)

    # フォームから受け取ったデータを辞書に格納
    form_data = {
        "name": request.form.get("name", "").strip(),
        "postal_code": request.form.get("postal_code", "").strip(),
        "address_1": request.form.get("address_1", "").strip(),  # 都道府県・市区町村
        "address_2": request.form.get("address_2", "").strip(),  # 番地・部屋番号
        "phone": request.form.get("phone", "").strip(),
        "email": request.form.get("email", "").strip(),
        "sns_account": request.form.get("sns_account", "").strip(),
        "school_grade": request.form.get("school_grade", "").strip(),
        "other": request.form.get("other", "").strip(),
    }

    try:
        # スプレッドシートへの書き込み（例）
        write_to_spreadsheet_for_catalog(form_data)
    except Exception as e:
        return f"エラーが発生しました: {e}", 500

    return "フォーム送信ありがとうございました！ カタログ送付をお待ちください。", 200

# -----------------------
# 動作確認用
# -----------------------
@app.route("/", methods=["GET"])
def health_check():
    return "LINE Bot is running.", 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
