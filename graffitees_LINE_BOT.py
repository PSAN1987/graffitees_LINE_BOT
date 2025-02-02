import os
import psycopg2
import requests
from dotenv import load_dotenv
from flask import Flask, request, abort, render_template_string
import logging
import traceback
import json

# ★ line-bot-sdk v2 系 ★
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
    CarouselContainer,
    BoxComponent,
    TextComponent,
    ButtonComponent
)

#############################
# (A) 既存の環境変数など読み込み
#############################
load_dotenv()

CHANNEL_ACCESS_TOKEN = os.getenv('CHANNEL_ACCESS_TOKEN')
CHANNEL_SECRET = os.getenv('CHANNEL_SECRET')

# ★ S3 などにアップロードするための環境変数例
AWS_ACCESS_KEY_ID = os.getenv('AWS_ACCESS_KEY_ID')
AWS_SECRET_ACCESS_KEY = os.getenv('AWS_SECRET_ACCESS_KEY')
S3_BUCKET_NAME = os.getenv('S3_BUCKET_NAME')

DATABASE_NAME = os.getenv('DATABASE_NAME')
DATABASE_USER = os.getenv('DATABASE_USER')
DATABASE_PASSWORD = os.getenv('DATABASE_PASSWORD')
DATABASE_HOST = os.getenv('DATABASE_HOST')
DATABASE_PORT = os.getenv('DATABASE_PORT')

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

line_bot_api = LineBotApi(CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(CHANNEL_SECRET)

# ---------------------------------------
# (B) ユーザーの状態管理 (簡易) - DB等推奨
# ---------------------------------------
user_states = {}

###################################
# (C) DB接続 (PostgreSQL想定)
###################################
def get_db_connection():
    """PostgreSQLに接続してconnectionを返す"""
    return psycopg2.connect(
        dbname=DATABASE_NAME,
        user=DATABASE_USER,
        password=DATABASE_PASSWORD,
        host=DATABASE_HOST,
        port=DATABASE_PORT
    )

###################################
# (D) S3にファイルをアップロード
###################################
import boto3
from werkzeug.utils import secure_filename
import uuid

def upload_file_to_s3(file_storage, s3_bucket, prefix="uploads/"):
    """
    file_storage: FlaskのFileStorageオブジェクト (request.files['...'])
    s3_bucket: アップ先のS3バケット名
    prefix: S3上のパスのプレフィックス
    戻り値: アップロード後のS3ファイルURL (空なら None)
    """
    if not file_storage or file_storage.filename == "":
        return None

    s3 = boto3.client(
        's3',
        aws_access_key_id=AWS_ACCESS_KEY_ID,
        aws_secret_access_key=AWS_SECRET_ACCESS_KEY
    )

    filename = secure_filename(file_storage.filename)
    unique_id = str(uuid.uuid4())
    s3_key = prefix + unique_id + "_" + filename

    s3.upload_fileobj(file_storage, s3_bucket, s3_key)

    url = f"https://{s3_bucket}.s3.amazonaws.com/{s3_key}"
    return url

###################################
# (E) 価格表と計算ロジック
###################################
PRICE_TABLE = [
    ("ドライTシャツ", 10, 14, "早割", 1830, 850, 850, 550),
    ("ドライTシャツ", 10, 14, "通常", 2030, 850, 850, 550),
    # ... 本来は全行
]

def calc_total_price(
    product_name: str,
    quantity: int,
    early_discount_str: str,  # "14日前以上" => "早割", それ以外 => "通常"
    print_position: str,
    color_option: str
) -> int:
    if early_discount_str == "14日前以上":
        discount_type = "早割"
    else:
        discount_type = "通常"

    row = None
    for item in PRICE_TABLE:
        (p_name, min_q, max_q, d_type, unit_price, color_price, pos_price, full_price) = item
        if p_name == product_name and d_type == discount_type and min_q <= quantity <= max_q:
            row = item
            break

    if not row:
        return 0

    (_, _, _, _, unit_price, color_price, pos_price, full_price) = row
    base = unit_price * quantity
    option_cost = 0

    if color_option == "same_color_add":
        option_cost += color_price * quantity
    elif color_option == "different_color_add":
        option_cost += pos_price * quantity
    elif color_option == "full_color_add":
        option_cost += full_price * quantity

    total = base + option_cost
    return total

###################################
# (F) Flex Message: モード選択
###################################
def create_mode_selection_flex():
    bubble = BubbleContainer(
        body=BoxComponent(
            layout='vertical',
            contents=[
                TextComponent(text='モードを選択してください!', weight='bold', size='lg')
            ]
        ),
        footer=BoxComponent(
            layout='vertical',
            contents=[
                ButtonComponent(style='primary', action=PostbackAction(label='簡易見積', data='quick_estimate')),
                ButtonComponent(style='primary', action=PostbackAction(label='WEBフォームから注文', data='web_order')),
                ButtonComponent(style='primary', action=PostbackAction(label='注文用紙から注文', data='paper_order'))
            ]
        )
    )
    return FlexSendMessage(alt_text='モードを選択してください', contents=bubble)

###################################
# (G) 簡易見積フロー (既存機能)
###################################
def create_quick_estimate_intro_flex():
    bubble = BubbleContainer(
        body=BoxComponent(
            layout='vertical',
            contents=[
                TextComponent(
                    text=(
                        '簡易見積に必要な項目を順番に確認します。\n'
                        '1. 学校/団体名\n'
                        '2. お届け先(都道府県)\n'
                        '3. 早割確認\n'
                        '4. 1枚当たりの予算\n'
                        '5. 商品名\n'
                        '6. 枚数\n'
                        '7. プリント位置\n'
                        '8. 使用する色数'
                    ),
                    wrap=True
                )
            ]
        ),
        footer=BoxComponent(
            layout='vertical',
            contents=[
                ButtonComponent(style='primary', action=PostbackAction(label='入力を開始する', data='start_quick_estimate_input'))
            ]
        )
    )
    return FlexSendMessage(alt_text='簡易見積モードへようこそ', contents=bubble)

def create_early_discount_flex():
    bubble = BubbleContainer(
        body=BoxComponent(layout='vertical', contents=[
            TextComponent(text='使用日から14日前以上 or 14日前以内を選択してください。', wrap=True)
        ]),
        footer=BoxComponent(
            layout='vertical',
            contents=[
                ButtonComponent(style='primary', action=PostbackAction(label='14日前以上', data='14days_plus')),
                ButtonComponent(style='primary', action=PostbackAction(label='14日前以内', data='14days_minus'))
            ]
        )
    )
    return FlexSendMessage(alt_text='早割確認', contents=bubble)

def create_product_selection_carousel():
    bubble1 = BubbleContainer(
        body=BoxComponent(layout='vertical', contents=[
            TextComponent(text='商品を選択してください(1/2)', weight='bold', size='md')
        ]),
        footer=BoxComponent(layout='vertical', contents=[
            ButtonComponent(style='primary', action=PostbackAction(label='ドライTシャツ', data='ドライTシャツ')),
            ButtonComponent(style='primary', action=PostbackAction(label='ヘビーウェイトTシャツ', data='ヘビーウェイトTシャツ')),
            ButtonComponent(style='primary', action=PostbackAction(label='ドライポロシャツ', data='ドライポロシャツ')),
            ButtonComponent(style='primary', action=PostbackAction(label='ドライメッシュビブス', data='ドライメッシュビブス')),
            ButtonComponent(style='primary', action=PostbackAction(label='ドライベースボールシャツ', data='ドライベースボールシャツ')),
            ButtonComponent(style='primary', action=PostbackAction(label='ドライロングスリープTシャツ', data='ドライロングスリープTシャツ')),
            ButtonComponent(style='primary', action=PostbackAction(label='ドライハーフパンツ', data='ドライハーフパンツ'))
        ])
    )
    bubble2 = BubbleContainer(
        body=BoxComponent(layout='vertical', contents=[
            TextComponent(text='商品を選択してください(2/2)', weight='bold', size='md')
        ]),
        footer=BoxComponent(layout='vertical', contents=[
            ButtonComponent(style='primary', action=PostbackAction(label='ヘビーウェイトロングスリープTシャツ', data='ヘビーウェイトロングスリープTシャツ')),
            ButtonComponent(style='primary', action=PostbackAction(label='クルーネックライトトレーナー', data='クルーネックライトトレーナー')),
            ButtonComponent(style='primary', action=PostbackAction(label='フーデッドライトパーカー', data='フーデッドライトパーカー')),
            ButtonComponent(style='primary', action=PostbackAction(label='スタンダードトレーナー', data='スタンダードトレーナー')),
            ButtonComponent(style='primary', action=PostbackAction(label='スタンダードWフードパーカー', data='スタンダードWフードパーカー')),
            ButtonComponent(style='primary', action=PostbackAction(label='ジップアップライトパーカー', data='ジップアップライトパーカー'))
        ])
    )
    carousel = CarouselContainer(contents=[bubble1, bubble2])
    return FlexSendMessage(alt_text='商品を選択してください', contents=carousel)

def create_print_position_flex():
    bubble = BubbleContainer(
        body=BoxComponent(layout='vertical', contents=[
            TextComponent(text='プリントする位置を選択してください', weight='bold')
        ]),
        footer=BoxComponent(layout='vertical', contents=[
            ButtonComponent(style='primary', action=PostbackAction(label='前', data='front')),
            ButtonComponent(style='primary', action=PostbackAction(label='背中', data='back')),
            ButtonComponent(style='primary', action=PostbackAction(label='前と背中', data='front_back'))
        ])
    )
    return FlexSendMessage(alt_text='プリント位置選択', contents=bubble)

def create_color_options_flex():
    bubble = BubbleContainer(
        body=BoxComponent(layout='vertical', contents=[
            TextComponent(text='使用する色数(前・背中)を選択してください', weight='bold'),
            TextComponent(text='(複数選択の実装は省略)', size='sm')
        ]),
        footer=BoxComponent(layout='vertical', contents=[
            ButtonComponent(style='primary', action=PostbackAction(label='同じ位置にプリントカラー追加', data='same_color_add')),
            ButtonComponent(style='primary', action=PostbackAction(label='別の場所にプリント位置追加', data='different_color_add')),
            ButtonComponent(style='primary', action=PostbackAction(label='フルカラーに追加', data='full_color_add'))
        ])
    )
    return FlexSendMessage(alt_text='使用する色数を選択', contents=bubble)

###################################
# (H) Flaskルート: HealthCheck
###################################
@app.route("/", methods=["GET"])
def health_check():
    return "OK", 200

###################################
# (I) Flaskルート: LINE Callback
###################################
@app.route("/callback", methods=["POST"])
def callback():
    signature = request.headers.get("X-Line-Signature", "")
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

###################################
# (J) LINEハンドラ: TextMessage
###################################
@handler.add(MessageEvent, message=TextMessage)
def handle_text_message(event):
    user_id = event.source.user_id
    user_input = event.message.text.strip()
    logger.info(f"[DEBUG] user_input: '{user_input}'")

    if user_input == "モード選択":
        flex = create_mode_selection_flex()
        line_bot_api.reply_message(event.reply_token, flex)
        return

    if user_id in user_states:
        st = user_states[user_id].get("state")
        if st == "await_school_name":
            user_states[user_id]["school_name"] = user_input
            user_states[user_id]["state"] = "await_prefecture"
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="学校名を保存しました。\n次にお届け先(都道府県)を入力してください。")
            )
            return

        if st == "await_prefecture":
            user_states[user_id]["prefecture"] = user_input
            user_states[user_id]["state"] = "await_early_discount"
            discount_flex = create_early_discount_flex()
            line_bot_api.reply_message(event.reply_token, discount_flex)
            return

        if st == "await_budget":
            user_states[user_id]["budget"] = user_input
            user_states[user_id]["state"] = "await_product"
            product_flex = create_product_selection_carousel()
            line_bot_api.reply_message(event.reply_token, product_flex)
            return

        if st == "await_quantity":
            user_states[user_id]["quantity"] = user_input
            user_states[user_id]["state"] = "await_print_position"
            pos_flex = create_print_position_flex()
            line_bot_api.reply_message(event.reply_token, pos_flex)
            return

        # 想定外
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=f"現在の状態({st})でテキスト入力は想定外です。")
        )
        return

    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text=f"あなたのメッセージ: {user_input}")
    )

###################################
# (K) LINEハンドラ: PostbackEvent
###################################
@handler.add(PostbackEvent)
def handle_postback(event):
    user_id = event.source.user_id
    data = event.postback.data
    logger.info(f"[DEBUG] Postback data: {data}")

    if data == "quick_estimate":
        intro = create_quick_estimate_intro_flex()
        line_bot_api.reply_message(event.reply_token, intro)
        return

    if data == "start_quick_estimate_input":
        user_states[user_id] = {
            "state": "await_school_name",
            "school_name": None,
            "prefecture": None,
            "early_discount": None,
            "budget": None,
            "product": None,
            "quantity": None,
            "print_position": None,
            "color_options": None
        }
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="まずは学校または団体名を入力してください。")
        )
        return

    if data == "web_order":
        form_url = f"https://graffitees-line-bot.onrender.com/webform?user_id={user_id}"
        msg = (f"WEBフォームから注文ですね！\nこちらから入力してください。\n{form_url}")
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=msg))
        return

    if data == "paper_order":
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="注文用紙から注文は未実装です。"))
        return

    if user_id not in user_states:
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="簡易見積モードではありません。"))
        return

    st = user_states[user_id]["state"]

    if st == "await_early_discount":
        if data == "14days_plus":
            user_states[user_id]["early_discount"] = "14日前以上"
        elif data == "14days_minus":
            user_states[user_id]["early_discount"] = "14日前以内"
        else:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="早割選択が不明です。"))
            return
        user_states[user_id]["state"] = "await_budget"
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="早割を保存しました。\n1枚あたりの予算を入力してください。"))
        return

    if st == "await_product":
        user_states[user_id]["product"] = data
        user_states[user_id]["state"] = "await_quantity"
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=f"{data} を選択しました。\n枚数を入力してください。")
        )
        return

    if st == "await_print_position":
        if data == "front":
            user_states[user_id]["print_position"] = "前"
        elif data == "back":
            user_states[user_id]["print_position"] = "背中"
        elif data == "front_back":
            user_states[user_id]["print_position"] = "前と背中"
        else:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="プリント位置の指定が不明です。"))
            return
        user_states[user_id]["state"] = "await_color_options"
        color_flex = create_color_options_flex()
        line_bot_api.reply_message(event.reply_token, color_flex)
        return

    if st == "await_color_options":
        if data not in ["same_color_add", "different_color_add", "full_color_add"]:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="色数の選択が不明です。"))
            return

        user_states[user_id]["color_options"] = data
        s = user_states[user_id]
        summary = (
            f"学校/団体名: {s['school_name']}\n"
            f"都道府県: {s['prefecture']}\n"
            f"早割確認: {s['early_discount']}\n"
            f"予算: {s['budget']}\n"
            f"商品名: {s['product']}\n"
            f"枚数: {s['quantity']}\n"
            f"プリント位置: {s['print_position']}\n"
            f"使用する色数: {s['color_options']}"
        )

        qty = int(s['quantity'])
        early_disc = s['early_discount']
        product = s['product']
        pos = s['print_position']
        color_opt = s['color_options']
        total_price = calc_total_price(product, qty, early_disc, pos, color_opt)

        del user_states[user_id]
        reply_text = (
            "全項目の入力が完了しました。\n\n" + summary +
            "\n\n--- 見積計算結果 ---\n"
            f"合計金額: ¥{total_price:,}\n"
            "（概算です。詳細は別途ご相談ください）"
        )
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))
        return

    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"不明なアクション: {data}"))

###################################
# (L) WEBフォームの実装
###################################
FORM_HTML = """
<!DOCTYPE html>
<html>
<head>
  <meta charset="UTF-8">
  <title>WEBフォームから注文</title>
</head>
<body>
  <h1>WEBフォームから注文</h1>
  <!-- 画像アップロードに対応するため、enctypeをmultipart/form-data に設定 -->
  <form action="/webform_submit" method="POST" enctype="multipart/form-data">
    <input type="hidden" name="user_id" value="{{ user_id }}" />

    <p>申込日: <input type="date" name="application_date"></p>
    <p>配達日: <input type="date" name="delivery_date"></p>
    <p>使用日: <input type="date" name="use_date"></p>

    <p>利用する学割特典:
      <select name="discount_option">
        <option value="早割">早割</option>
        <option value="タダ割">タダ割</option>
        <option value="いっしょ割り">いっしょ割り</option>
      </select>
    </p>

    <p>学校名: <input type="text" name="school_name"></p>
    <p>LINEアカウント名: <input type="text" name="line_account"></p>
    <p>団体名: <input type="text" name="group_name"></p>
    <p>学校住所: <input type="text" name="school_address"></p>
    <p>学校TEL: <input type="text" name="school_tel"></p>
    <p>担任名: <input type="text" name="teacher_name"></p>
    <p>担任携帯: <input type="text" name="teacher_tel"></p>
    <p>担任メール: <input type="email" name="teacher_email"></p>
    <p>代表者: <input type="text" name="representative"></p>
    <p>代表者TEL: <input type="text" name="rep_tel"></p>
    <p>代表者メール: <input type="email" name="rep_email"></p>

    <p>デザイン確認方法:
      <select name="design_confirm">
        <option value="LINE代表者">LINE代表者</option>
        <option value="LINEご担任(保護者)">LINEご担任(保護者)</option>
        <option value="メール代表者">メール代表者</option>
        <option value="メールご担任(保護者)">メールご担任(保護者)</option>
      </select>
    </p>

    <p>お支払い方法:
      <select name="payment_method">
        <option value="代金引換(ヤマト運輸/現金のみ)">代金引換(ヤマト運輸/現金のみ)</option>
        <option value="後払い(コンビニ/郵便振替)">後払い(コンビニ/郵便振替)</option>
        <option value="後払い(銀行振込)">後払い(銀行振込)</option>
        <option value="先払い(銀行振込)">先払い(銀行振込)</option>
      </select>
    </p>

    <p>商品名:
      <select name="product_name">
        <option value="ドライTシャツ">ドライTシャツ</option>
        <option value="ヘビーウェイトTシャツ">ヘビーウェイトTシャツ</option>
        <option value="ドライポロシャツ">ドライポロシャツ</option>
        <option value="ドライメッシュビブス">ドライメッシュビブス</option>
        <option value="ドライベースボールシャツ">ドライベースボールシャツ</option>
        <option value="ドライロングスリープTシャツ">ドライロングスリープTシャツ</option>
        <option value="ドライハーフパンツ">ドライハーフパンツ</option>
        <option value="ヘビーウェイトロングスリープTシャツ">ヘビーウェイトロングスリープTシャツ</option>
        <option value="クルーネックライトトレーナー">クルーネックライトトレーナー</option>
        <option value="フーデッドライトパーカー">フーデッドライトパーカー</option>
        <option value="スタンダードトレーナー">スタンダードトレーナー</option>
        <option value="スタンダードWフードパーカー">スタンダードWフードパーカー</option>
        <option value="ジップアップライトパーカー">ジップアップライトパーカー</option>
      </select>
    </p>
    <p>商品カラー: <input type="text" name="product_color"></p>
    <p>サイズ(SS): <input type="number" name="size_ss"></p>
    <p>サイズ(S): <input type="number" name="size_s"></p>
    <p>サイズ(M): <input type="number" name="size_m"></p>
    <p>サイズ(L): <input type="number" name="size_l"></p>
    <p>サイズ(LL): <input type="number" name="size_ll"></p>
    <p>サイズ(LLL): <input type="number" name="size_lll"></p>

    <p>プリントデザインイメージデータ(前): <input type="file" name="design_image_front"></p>
    <p>プリントデザインイメージデータ(後): <input type="file" name="design_image_back"></p>
    <p>プリントデザインイメージデータ(その他): <input type="file" name="design_image_other"></p>

    <p><button type="submit">送信</button></p>
  </form>
</body>
</html>
"""

@app.route("/webform", methods=["GET"])
def show_webform():
    user_id = request.args.get("user_id", "")
    return render_template_string(FORM_HTML, user_id=user_id)

###################################
# (M) 空文字を None にする関数
###################################
def none_if_empty_str(val: str):
    """文字列入力が空なら None, そうでなければ文字列を返す"""
    if not val:  # '' or None
        return None
    return val

def none_if_empty_date(val: str):
    """日付カラム用: 空なら None、そうでなければそのまま文字列として渡す (Postgresがdate型に変換)"""
    if not val:
        return None
    return val

def none_if_empty_int(val: str):
    """数値カラム用: 空なら None, それ以外はintに変換"""
    if not val:
        return None
    return int(val)

###################################
# (N) /webform_submit: フォーム送信受け取り
###################################
@app.route("/webform_submit", methods=["POST"])
def webform_submit():
    form = request.form
    files = request.files
    user_id = form.get("user_id", "")

    # ---------- テキスト項目を取得 (空文字はNone化) ----------
    application_date = none_if_empty_date(form.get("application_date"))
    delivery_date = none_if_empty_date(form.get("delivery_date"))
    use_date = none_if_empty_date(form.get("use_date"))

    discount_option = none_if_empty_str(form.get("discount_option"))
    school_name = none_if_empty_str(form.get("school_name"))
    line_account = none_if_empty_str(form.get("line_account"))
    group_name = none_if_empty_str(form.get("group_name"))
    school_address = none_if_empty_str(form.get("school_address"))
    school_tel = none_if_empty_str(form.get("school_tel"))
    teacher_name = none_if_empty_str(form.get("teacher_name"))
    teacher_tel = none_if_empty_str(form.get("teacher_tel"))
    teacher_email = none_if_empty_str(form.get("teacher_email"))
    representative = none_if_empty_str(form.get("representative"))
    rep_tel = none_if_empty_str(form.get("rep_tel"))
    rep_email = none_if_empty_str(form.get("rep_email"))

    design_confirm = none_if_empty_str(form.get("design_confirm"))
    payment_method = none_if_empty_str(form.get("payment_method"))
    product_name = none_if_empty_str(form.get("product_name"))
    product_color = none_if_empty_str(form.get("product_color"))

    # サイズは数値カラムの場合、intかNone
    size_ss = none_if_empty_int(form.get("size_ss"))
    size_s = none_if_empty_int(form.get("size_s"))
    size_m = none_if_empty_int(form.get("size_m"))
    size_l = none_if_empty_int(form.get("size_l"))
    size_ll = none_if_empty_int(form.get("size_ll"))
    size_lll = none_if_empty_int(form.get("size_lll"))

    # ---------- 画像ファイル ----------
    img_front = files.get("design_image_front")
    img_back = files.get("design_image_back")
    img_other = files.get("design_image_other")

    # S3にアップロード → URL取得
    front_url = upload_file_to_s3(img_front, S3_BUCKET_NAME, prefix="uploads/")
    back_url = upload_file_to_s3(img_back, S3_BUCKET_NAME, prefix="uploads/")
    other_url = upload_file_to_s3(img_other, S3_BUCKET_NAME, prefix="uploads/")

    # DBに保存
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            sql = """
            INSERT INTO orders (
                user_id,
                application_date,
                delivery_date,
                use_date,
                discount_option,
                school_name,
                line_account,
                group_name,
                school_address,
                school_tel,
                teacher_name,
                teacher_tel,
                teacher_email,
                representative,
                rep_tel,
                rep_email,
                design_confirm,
                payment_method,
                product_name,
                product_color,
                size_ss,
                size_s,
                size_m,
                size_l,
                size_ll,
                size_lll,
                design_image_front_url,
                design_image_back_url,
                design_image_other_url,
                created_at
            ) VALUES (
                %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s,
                %s, %s, %s, %s, NOW()
            )
            RETURNING id
            """
            params = (
                user_id,
                application_date,
                delivery_date,
                use_date,
                discount_option,
                school_name,
                line_account,
                group_name,
                school_address,
                school_tel,
                teacher_name,
                teacher_tel,
                teacher_email,
                representative,
                rep_tel,
                rep_email,
                design_confirm,
                payment_method,
                product_name,
                product_color,
                size_ss,
                size_s,
                size_m,
                size_l,
                size_ll,
                size_lll,
                front_url,
                back_url,
                other_url
            )
            cur.execute(sql, params)
            new_id = cur.fetchone()[0]
        conn.commit()
        logger.info(f"Inserted order id={new_id}")

    # フォーム送信完了 → Push通知
    push_text = (
        "WEBフォームの注文を受け付けました！\n"
        f"学校名: {school_name}\n"
        f"商品名: {product_name}\n"
        "後ほど担当者からご連絡いたします。"
    )
    try:
        line_bot_api.push_message(to=user_id, messages=TextSendMessage(text=push_text))
    except Exception as e:
        logger.error(f"Push message failed: {e}")

    return "フォーム送信完了。LINEに通知を送りました。"


###################################
# (O) 例: CSV出力関数 (任意)
###################################
import csv

def export_orders_to_csv():
    """DBの orders テーブルをCSV形式で出力する例(ローカルファイル書き込み想定)"""
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM orders ORDER BY id")
            rows = cur.fetchall()
            col_names = [desc[0] for desc in cur.description]

    file_path = "orders_export.csv"
    with open(file_path, mode="w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(col_names)
        for row in rows:
            writer.writerow(row)
    logger.info(f"CSV Export Done: {file_path}")

###################################
# Flask起動
###################################
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=True)
