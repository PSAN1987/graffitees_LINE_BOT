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

load_dotenv()

CHANNEL_ACCESS_TOKEN = os.getenv('CHANNEL_ACCESS_TOKEN')
CHANNEL_SECRET = os.getenv('CHANNEL_SECRET')

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

line_bot_api = LineBotApi(CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(CHANNEL_SECRET)

# ---------------------------------------
# ユーザーの状態管理 (簡易) - 実際にはDB推奨
# ---------------------------------------
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


###################################
# PRICE_TABLE & calc_total_price
###################################
PRICE_TABLE = [
    # 省略せず、Excel 全行をリスト化
    # （ここではドライTシャツ等、一部サンプルのみ）
    ("ドライTシャツ", 10, 14, "早割", 1830, 850, 850, 550),
    ("ドライTシャツ", 10, 14, "通常", 2030, 850, 850, 550),
    # ...
    ("ジップアップライトパーカー", 100, 500, "通常", 2910, 300, 300, 550),
]

def calc_total_price(
    product_name: str,
    quantity: int,
    early_discount_str: str,  # "14日前以上" => "早割", それ以外 => "通常"
    print_position: str,      # "前" / "背中" / "前と背中"
    color_option: str         # same_color_add / different_color_add / full_color_add
) -> int:
    """
    商品名・枚数・早割有無・色数オプションなどを元に合計金額を計算する。
    PRICE_TABLE から該当行を検索 → 単価・オプション料金を加算
    """
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

    # color_option に応じたオプション費用加算
    if color_option == "same_color_add":
        option_cost += color_price * quantity
    elif color_option == "different_color_add":
        option_cost += pos_price * quantity
    elif color_option == "full_color_add":
        option_cost += full_price * quantity

    # 例: "背中" や "前と背中" の追加料金があればここで加算
    # （仕様次第で調整）
    total = base + option_cost
    return total

###################################
# Flex (モード選択)
###################################
def create_mode_selection_flex():
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

###################################
# 「簡易見積」用フロー (既存)
###################################
def create_quick_estimate_intro_flex():
    bubble = BubbleContainer(
        body=BoxComponent(
            layout='vertical',
            contents=[
                TextComponent(
                    text='簡易見積に必要な項目を順番に確認します。\n'
                         '1. 学校/団体名\n'
                         '2. お届け先(都道府県)\n'
                         '3. 早割確認\n'
                         '4. 1枚当たりの予算\n'
                         '5. 商品名\n'
                         '6. 枚数\n'
                         '7. プリント位置\n'
                         '8. 使用する色数',
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
        alt_text='簡易見積モードへようこそ',
        contents=bubble
    )

def create_early_discount_flex():
    bubble = BubbleContainer(
        body=BoxComponent(
            layout='vertical',
            contents=[
                TextComponent(text='使用日から14日前以上か14日前以内か選択してください。')
            ]
        ),
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
    """商品名を 2バブルに分けた Carousel"""
    bubble1 = BubbleContainer(
        body=BoxComponent(
            layout='vertical',
            contents=[
                TextComponent(text='商品を選択してください(1/2)', weight='bold', size='md')
            ]
        ),
        footer=BoxComponent(
            layout='vertical',
            contents=[
                ButtonComponent(style='primary', action=PostbackAction(label='ドライTシャツ', data='ドライTシャツ')),
                ButtonComponent(style='primary', action=PostbackAction(label='ヘビーウェイトTシャツ', data='ヘビーウェイトTシャツ')),
                ButtonComponent(style='primary', action=PostbackAction(label='ドライポロシャツ', data='ドライポロシャツ')),
                ButtonComponent(style='primary', action=PostbackAction(label='ドライメッシュビブス', data='ドライメッシュビブス')),
                ButtonComponent(style='primary', action=PostbackAction(label='ドライベースボールシャツ', data='ドライベースボールシャツ')),
                ButtonComponent(style='primary', action=PostbackAction(label='ドライロングスリープTシャツ', data='ドライロングスリープTシャツ')),
                ButtonComponent(style='primary', action=PostbackAction(label='ドライハーフパンツ', data='ドライハーフパンツ'))
            ]
        )
    )
    bubble2 = BubbleContainer(
        body=BoxComponent(
            layout='vertical',
            contents=[
                TextComponent(text='商品を選択してください(2/2)', weight='bold', size='md')
            ]
        ),
        footer=BoxComponent(
            layout='vertical',
            contents=[
                ButtonComponent(style='primary', action=PostbackAction(label='ヘビーウェイトロングスリープTシャツ', data='ヘビーウェイトロングスリープTシャツ')),
                ButtonComponent(style='primary', action=PostbackAction(label='クルーネックライトトレーナー', data='クルーネックライトトレーナー')),
                ButtonComponent(style='primary', action=PostbackAction(label='フーデッドライトパーカー', data='フーデッドライトパーカー')),
                ButtonComponent(style='primary', action=PostbackAction(label='スタンダードトレーナー', data='スタンダードトレーナー')),
                ButtonComponent(style='primary', action=PostbackAction(label='スタンダードWフードパーカー', data='スタンダードWフードパーカー')),
                ButtonComponent(style='primary', action=PostbackAction(label='ジップアップライトパーカー', data='ジップアップライトパーカー'))
            ]
        )
    )
    carousel = CarouselContainer(contents=[bubble1, bubble2])
    return FlexSendMessage(alt_text='商品を選択してください', contents=carousel)

def create_print_position_flex():
    bubble = BubbleContainer(
        body=BoxComponent(
            layout='vertical',
            contents=[
                TextComponent(text='プリントする位置を選択してください', weight='bold')
            ]
        ),
        footer=BoxComponent(
            layout='vertical',
            contents=[
                ButtonComponent(style='primary', action=PostbackAction(label='前', data='front')),
                ButtonComponent(style='primary', action=PostbackAction(label='背中', data='back')),
                ButtonComponent(style='primary', action=PostbackAction(label='前と背中', data='front_back'))
            ]
        )
    )
    return FlexSendMessage(alt_text='プリント位置選択', contents=bubble)

def create_color_options_flex():
    bubble = BubbleContainer(
        body=BoxComponent(
            layout='vertical',
            contents=[
                TextComponent(text='使用する色数(前・背中)を選択してください', weight='bold'),
                TextComponent(text='(複数選択実装は省略)', size='sm')
            ]
        ),
        footer=BoxComponent(
            layout='vertical',
            contents=[
                ButtonComponent(style='primary', action=PostbackAction(label='同じ位置にプリントカラー追加', data='same_color_add')),
                ButtonComponent(style='primary', action=PostbackAction(label='別の場所にプリント位置追加', data='different_color_add')),
                ButtonComponent(style='primary', action=PostbackAction(label='フルカラーに追加', data='full_color_add'))
            ]
        )
    )
    return FlexSendMessage(alt_text='使用する色数を選択', contents=bubble)

###################################
# メッセージハンドラ
###################################
@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_id = event.source.user_id
    user_input = event.message.text.strip()
    logger.info(f"[DEBUG] user_input: '{user_input}'")

    # 「モード選択」と入力で3モードボタンを返す
    if user_input == "モード選択":
        flex = create_mode_selection_flex()
        line_bot_api.reply_message(event.reply_token, flex)
        return

    # 簡易見積モード中かどうか
    if user_id in user_states:
        st = user_states[user_id].get("state")

        # 1. 学校名
        if st == "await_school_name":
            user_states[user_id]["school_name"] = user_input
            user_states[user_id]["state"] = "await_prefecture"
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="学校名を保存しました。\n次にお届け先(都道府県)を入力してください。")
            )
            return

        # 2. 都道府県
        if st == "await_prefecture":
            user_states[user_id]["prefecture"] = user_input
            user_states[user_id]["state"] = "await_early_discount"
            discount_flex = create_early_discount_flex()
            line_bot_api.reply_message(event.reply_token, discount_flex)
            return

        # 4. 予算
        if st == "await_budget":
            user_states[user_id]["budget"] = user_input
            user_states[user_id]["state"] = "await_product"
            product_flex = create_product_selection_carousel()
            line_bot_api.reply_message(event.reply_token, product_flex)
            return

        # 6. 枚数
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

    # 通常メッセージ
    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"あなたのメッセージ: {user_input}"))


###################################
# ポストバックハンドラ
###################################
@handler.add(PostbackEvent)
def handle_postback(event):
    user_id = event.source.user_id
    data = event.postback.data
    logger.info(f"[DEBUG] Postback data: {data}")

    # 簡易見積モードへ
    if data == "quick_estimate":
        intro = create_quick_estimate_intro_flex()
        line_bot_api.reply_message(event.reply_token, intro)
        return

    # 簡易見積開始
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

    # WEBフォームから注文
    if data == "web_order":
        # ユーザーの user_id をクエリに付けてフォームURLを提示
        form_url = f"https://graffitees-line-bot.onrender.com/webform?user_id={user_id}"
        msg = (
            "WEBフォームから注文ですね！\n"
            f"こちらのフォームから入力してください:\n{form_url}"
        )
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=msg))
        return

    # 注文用紙から注文
    if data == "paper_order":
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="注文用紙から注文は未実装です。"))
        return

    # 簡易見積フロー (既にモード開始済みかどうか)
    if user_id not in user_states:
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="簡易見積モードではありません。"))
        return

    st = user_states[user_id]["state"]

    # 3. 早割確認
    if st == "await_early_discount":
        if data == "14days_plus":
            user_states[user_id]["early_discount"] = "14日前以上"
        elif data == "14days_minus":
            user_states[user_id]["early_discount"] = "14日前以内"
        else:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="早割の選択が不明です。"))
            return
        user_states[user_id]["state"] = "await_budget"
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="早割を保存しました。\n1枚あたりの予算を入力してください。"))
        return

    # 5. 商品名
    if st == "await_product":
        user_states[user_id]["product"] = data
        user_states[user_id]["state"] = "await_quantity"
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=f"{data} を選択しました。\n枚数を入力してください。")
        )
        return

    # 7. プリント位置
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
        flex = create_color_options_flex()
        line_bot_api.reply_message(event.reply_token, flex)
        return

    # 8. カラーオプション
    if st == "await_color_options":
        c_opts = ["same_color_add", "different_color_add", "full_color_add"]
        if data not in c_opts:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="色数の選択が不明です。"))
            return
        user_states[user_id]["color_options"] = data

        # 入力完了 → 見積計算
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

    # 想定外
    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"不明なアクション: {data}"))


###################################
# WEBフォームの実装
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
  <form action="/webform_submit" method="POST">
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

    <p>プリントサイズ(前):
      <select name="print_size_front">
        <option value="おまかせ (最大:横28cm x 縦35cm以内)">おまかせ (最大:横28cm x 縦35cm以内)</option>
        <option value="ヨコcm x タテcmくらい">ヨコcm x タテcmくらい(詳細は入力してください)</option>
      </select>
      <input type="text" name="print_size_front_detail" placeholder="(例) 横20cm x 縦25cmなど">
    </p>
    <p>プリントカラー(前): <input type="text" name="print_color_front" placeholder="全てのカラーをご記入ください。計XX色"></p>
    <p>フォントNo.(前): <input type="text" name="font_no_front"></p>
    <p>プリントデザインサンプル(前): <input type="text" name="design_sample_front"></p>
    <p>プリントデザインイメージデータ(前): <input type="text" name="design_image_front"></p>

    <p>プリントサイズ(後):
      <select name="print_size_back">
        <option value="おまかせ (最大:横28cm x 縦35cm以内)">おまかせ (最大:横28cm x 縦35cm以内)</option>
        <option value="ヨコcm x タテcmくらい">ヨコcm x タテcmくらい(詳細は入力してください)</option>
      </select>
      <input type="text" name="print_size_back_detail" placeholder="(例) 横20cm x 縦25cmなど">
    </p>
    <p>プリントカラー(後): <input type="text" name="print_color_back" placeholder="全てのカラーをご記入ください。計XX色"></p>
    <p>フォントNo.(後): <input type="text" name="font_no_back"></p>
    <p>プリントデザインサンプル(後): <input type="text" name="design_sample_back"></p>
    <p>プリントデザインイメージデータ(後): <input type="text" name="design_image_back"></p>

    <p>プリントサイズ(その他):
      <select name="print_size_other">
        <option value="おまかせ (最大:横28cm x 縦35cm以内)">おまかせ (最大:横28cm x 縦35cm以内)</option>
        <option value="ヨコcm x タテcmくらい">ヨコcm x タテcmくらい(詳細は入力してください)</option>
      </select>
      <input type="text" name="print_size_other_detail" placeholder="(例) 横10cm x 縦10cmなど">
    </p>
    <p>プリントカラー(その他): <input type="text" name="print_color_other" placeholder="全てのカラーをご記入ください。計XX色"></p>
    <p>フォントNo.(その他): <input type="text" name="font_no_other"></p>
    <p>プリントデザインサンプル(その他): <input type="text" name="design_sample_other"></p>
    <p>プリントデザインイメージデータ(その他): <input type="text" name="design_image_other"></p>

    <p><button type="submit">送信</button></p>
  </form>
</body>
</html>
"""

@app.route("/webform", methods=["GET"])
def webform_show():
    user_id = request.args.get("user_id", "")
    return render_template_string(FORM_HTML, user_id=user_id)

@app.route("/webform_submit", methods=["POST"])
def webform_submit():
    form = request.form
    user_id = form.get("user_id", "")

    # 入力データを辞書にまとめる (必要項目すべて)
    order_data = {
        "application_date": form.get("application_date"),
        "delivery_date": form.get("delivery_date"),
        "use_date": form.get("use_date"),
        "discount_option": form.get("discount_option"),
        "school_name": form.get("school_name"),
        "line_account": form.get("line_account"),
        "group_name": form.get("group_name"),
        "school_address": form.get("school_address"),
        "school_tel": form.get("school_tel"),
        "teacher_name": form.get("teacher_name"),
        "teacher_tel": form.get("teacher_tel"),
        "teacher_email": form.get("teacher_email"),
        "representative": form.get("representative"),
        "rep_tel": form.get("rep_tel"),
        "rep_email": form.get("rep_email"),
        "design_confirm": form.get("design_confirm"),
        "payment_method": form.get("payment_method"),
        "product_name": form.get("product_name"),
        "product_color": form.get("product_color"),
        "size_ss": form.get("size_ss"),
        "size_s": form.get("size_s"),
        "size_m": form.get("size_m"),
        "size_l": form.get("size_l"),
        "size_ll": form.get("size_ll"),
        "size_lll": form.get("size_lll"),

        # プリントサイズ等(前/後/その他)
        "print_size_front": form.get("print_size_front"),
        "print_size_front_detail": form.get("print_size_front_detail"),
        "print_color_front": form.get("print_color_front"),
        "font_no_front": form.get("font_no_front"),
        "design_sample_front": form.get("design_sample_front"),
        "design_image_front": form.get("design_image_front"),

        "print_size_back": form.get("print_size_back"),
        "print_size_back_detail": form.get("print_size_back_detail"),
        "print_color_back": form.get("print_color_back"),
        "font_no_back": form.get("font_no_back"),
        "design_sample_back": form.get("design_sample_back"),
        "design_image_back": form.get("design_image_back"),

        "print_size_other": form.get("print_size_other"),
        "print_size_other_detail": form.get("print_size_other_detail"),
        "print_color_other": form.get("print_color_other"),
        "font_no_other": form.get("font_no_other"),
        "design_sample_other": form.get("design_sample_other"),
        "design_image_other": form.get("design_image_other"),
    }

    # DBに保存する等。ここでは簡易に user_states に格納
    user_states[user_id] = {
        "state": "web_order_submitted",
        "order_data": order_data
    }

    # 送信完了 → プッシュ通知
    try:
        line_bot_api.push_message(
            to=user_id,
            messages=TextSendMessage(
                text=(
                    "WEBフォームを送信いただきありがとうございました！\n"
                    f"学校名: {order_data['school_name']}\n"
                    f"商品名: {order_data['product_name']}\n"
                    "後ほど担当者からご連絡いたします。"
                )
            )
        )
    except Exception as e:
        logger.error(f"Push message failed: {e}")

    return "フォーム送信完了。LINEにも通知しました。"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=True)
