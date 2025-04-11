import os
import json
import time
from datetime import datetime
import pytz

import gspread
from flask import Flask, render_template_string, request, session
import uuid
from oauth2client.service_account import ServiceAccountCredentials

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

    new_row = [
        now_jst_str,  # 先頭に日時を追加
        form_data.get("name", ""),
        form_data.get("postal_code", ""),
        form_data.get("address", ""),
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
        "https://catalog-bot-1.onrender.com/catalog_form\n"
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
                TextSendMessage(text="入力内容が正しくありません。見積りフローを終了しました。")
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
                TextSendMessage(text="入力内容が正しくありません。見積りフローを終了しました。")
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
                TextSendMessage(text="入力内容が正しくありません。見積りフローを終了しました。")
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
                TextSendMessage(text="入力内容が正しくありません。見積りフローを終了しました。")
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
                TextSendMessage(text="入力内容が正しくありません。見積りフローを終了しました。")
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
                TextSendMessage(text="入力内容が正しくありません。見積りフローを終了しました。")
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
                    TextSendMessage(text="入力内容が正しくありません。見積りフローを終了しました。")
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
                    TextSendMessage(text="入力内容が正しくありません。見積りフローを終了しました。")
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
                f"お見積りが完了しました。\n\n"
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
                TextSendMessage(text="入力内容が正しくありません。見積りフローを終了しました。")
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
    # ユニークなトークンを生成して session に記録
    token = str(uuid.uuid4())
    session['catalog_form_token'] = token

    # ここで f-string を用いて {token} を実際の値に差し込む
    html_content = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>カタログ申し込みフォーム</title>
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
                document.getElementById('address').value = data.data.fullAddress;
            }}
        }} catch (error) {{
            console.log("住所検索失敗:", error);
        }}
    }}
    </script>
</head>
<body>
    <div class="container">
      <h1>カタログ申し込みフォーム</h1>
      <p>以下の項目をご記入の上、送信してください。</p>
      <!-- フォームは1つだけにまとめる -->
      <form action="/submit_form" method="post">
          <!-- ここにワンタイムトークンを仕込みます -->
          <input type="hidden" name="form_token" value="{token}">

          <label>氏名（必須）:
              <input type="text" name="name" required>
          </label>

          <label>郵便番号（必須）:<br>
              <small>※ハイフン無し7桁で入力すると自動で住所補完します</small><br>
              <input type="text" name="postal_code" id="postal_code" onkeyup="fetchAddress()" required>
          </label>

          <label>住所（必須）:<br>
              <small>※カタログ送付のために番地や部屋番号を含めた完全な住所の記入が必要です</small><br>
              <input type="text" name="address" id="address" required>
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
    # 送信されたトークンをチェック
    form_token = request.form.get('form_token')
    if form_token != session.get('catalog_form_token'):
        return "二重送信、あるいは不正なリクエストです。", 400

    # ここでトークンを使い捨てにする
    session.pop('catalog_form_token', None)

    form_data = {
        "name": request.form.get("name", "").strip(),
        "postal_code": request.form.get("postal_code", "").strip(),
        "address": request.form.get("address", "").strip(),
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

# -----------------------
# 動作確認用
# -----------------------
@app.route("/", methods=["GET"])
def health_check():
    return "LINE Bot is running.", 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
