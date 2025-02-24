import os
import time
import psycopg2
import requests
from dotenv import load_dotenv
from flask import Flask, request, abort, render_template_string
import logging
import traceback
import json
os.environ['TZ'] = 'Asia/Tokyo'
time.tzset()

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
    ButtonComponent,
    ImageMessage
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

# ▼▼ 追加 (Google Vision, OpenAI用) ▼▼
GOOGLE_APPLICATION_CREDENTIALS = os.getenv('GOOGLE_APPLICATION_CREDENTIALS')
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
# ▲▲ 追加 ▲▲

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
# (E) 価格表と計算ロジック (既存)
###################################
PRICE_TABLE = [
    # product,  minQty, maxQty, discountType, unitPrice, addColor, addPosition, addFullColor
    # （ドライTシャツ～ジップアップライトパーカーの全件は既存どおり）
    ("ドライTシャツ", 10, 14, "早割", 1830, 850, 850, 550),
    ("ドライTシャツ", 10, 14, "通常", 2030, 850, 850, 550),
    ("ドライTシャツ", 15, 19, "早割", 1470, 650, 650, 550),
    ("ドライTシャツ", 15, 19, "通常", 1670, 650, 650, 550),
    ("ドライTシャツ", 20, 29, "早割", 1230, 450, 450, 550),
    ("ドライTシャツ", 20, 29, "通常", 1430, 450, 450, 550),
    ("ドライTシャツ", 30, 39, "早割", 1060, 350, 350, 550),
    ("ドライTシャツ", 30, 39, "通常", 1260, 350, 350, 550),
    ("ドライTシャツ", 40, 49, "早割", 980, 350, 350, 550),
    ("ドライTシャツ", 40, 49, "通常", 1180, 350, 350, 550),
    ("ドライTシャツ", 50, 99, "早割", 890, 350, 350, 550),
    ("ドライTシャツ", 50, 99, "通常", 1090, 350, 350, 550),
    ("ドライTシャツ", 100, 500, "早割", 770, 300, 300, 550),
    ("ドライTシャツ", 100, 500, "通常", 970, 300, 300, 550),

    ("ヘビーウェイトTシャツ", 10, 14, "早割", 1970, 850, 850, 550),
    ("ヘビーウェイトTシャツ", 10, 14, "通常", 2170, 850, 850, 550),
    ("ヘビーウェイトTシャツ", 15, 19, "早割", 1610, 650, 650, 550),
    ("ヘビーウェイトTシャツ", 15, 19, "通常", 1810, 650, 650, 550),
    ("ヘビーウェイトTシャツ", 20, 29, "早割", 1370, 450, 450, 550),
    ("ヘビーウェイトTシャツ", 20, 29, "通常", 1570, 450, 450, 550),
    ("ヘビーウェイトTシャツ", 30, 39, "早割", 1200, 350, 350, 550),
    ("ヘビーウェイトTシャツ", 30, 39, "通常", 1400, 350, 350, 550),
    ("ヘビーウェイトTシャツ", 40, 49, "早割", 1120, 350, 350, 550),
    ("ヘビーウェイトTシャツ", 40, 49, "通常", 1320, 350, 350, 550),
    ("ヘビーウェイトTシャツ", 50, 99, "早割", 1030, 350, 350, 550),
    ("ヘビーウェイトTシャツ", 50, 99, "通常", 1230, 350, 350, 550),
    ("ヘビーウェイトTシャツ", 100, 500, "早割", 910, 300, 300, 550),
    ("ヘビーウェイトTシャツ", 100, 500, "通常", 1100, 300, 300, 550),

    ("ドライポロシャツ", 10, 14, "早割", 2170, 850, 850, 550),
    ("ドライポロシャツ", 10, 14, "通常", 2370, 850, 850, 550),
    ("ドライポロシャツ", 15, 19, "早割", 1810, 650, 650, 550),
    ("ドライポロシャツ", 15, 19, "通常", 2010, 650, 650, 550),
    ("ドライポロシャツ", 20, 29, "早割", 1570, 450, 450, 550),
    ("ドライポロシャツ", 20, 29, "通常", 1770, 450, 450, 550),
    ("ドライポロシャツ", 30, 39, "早割", 1400, 350, 350, 550),
    ("ドライポロシャツ", 30, 39, "通常", 1600, 350, 350, 550),
    ("ドライポロシャツ", 40, 49, "早割", 1320, 350, 350, 550),
    ("ドライポロシャツ", 40, 49, "通常", 1520, 350, 350, 550),
    ("ドライポロシャツ", 50, 99, "早割", 1230, 350, 350, 550),
    ("ドライポロシャツ", 50, 99, "通常", 1430, 350, 350, 550),
    ("ドライポロシャツ", 100, 500, "早割", 1110, 300, 300, 550),
    ("ドライポロシャツ", 100, 500, "通常", 1310, 300, 300, 550),

    ("ドライメッシュビブス", 10, 14, "早割", 2170, 850, 850, 550),
    ("ドライメッシュビブス", 10, 14, "通常", 2370, 850, 850, 550),
    ("ドライメッシュビブス", 15, 19, "早割", 1810, 650, 650, 550),
    ("ドライメッシュビブス", 15, 19, "通常", 2010, 650, 650, 550),
    ("ドライメッシュビブス", 20, 29, "早割", 1570, 450, 450, 550),
    ("ドライメッシュビブス", 20, 29, "通常", 1770, 450, 450, 550),
    ("ドライメッシュビブス", 30, 39, "早割", 1400, 350, 350, 550),
    ("ドライメッシュビブス", 30, 39, "通常", 1600, 350, 350, 550),
    ("ドライメッシュビブス", 40, 49, "早割", 1320, 350, 350, 550),
    ("ドライメッシュビブス", 40, 49, "通常", 1520, 350, 350, 550),
    ("ドライメッシュビブス", 50, 99, "早割", 1230, 350, 350, 550),
    ("ドライメッシュビブス", 50, 99, "通常", 1430, 350, 350, 550),
    ("ドライメッシュビブス", 100, 500, "早割", 1100, 300, 300, 550),
    ("ドライメッシュビブス", 100, 500, "通常", 1310, 300, 300, 550),

    ("ドライベースボールシャツ", 10, 14, "早割", 2470, 850, 850, 550),
    ("ドライベースボールシャツ", 10, 14, "通常", 2670, 850, 850, 550),
    ("ドライベースボールシャツ", 15, 19, "早割", 2110, 650, 650, 550),
    ("ドライベースボールシャツ", 15, 19, "通常", 2310, 650, 650, 550),
    ("ドライベースボールシャツ", 20, 29, "早割", 1870, 450, 450, 550),
    ("ドライベースボールシャツ", 20, 29, "通常", 2070, 450, 450, 550),
    ("ドライベースボールシャツ", 30, 39, "早割", 1700, 350, 350, 550),
    ("ドライベースボールシャツ", 30, 39, "通常", 1900, 350, 350, 550),
    ("ドライベースボールシャツ", 40, 49, "早割", 1620, 350, 350, 550),
    ("ドライベースボールシャツ", 40, 49, "通常", 1820, 350, 350, 550),
    ("ドライベースボールシャツ", 50, 99, "早割", 1530, 350, 350, 550),
    ("ドライベースボールシャツ", 50, 99, "通常", 1730, 350, 350, 550),
    ("ドライベースボールシャツ", 100, 500, "早割", 1410, 300, 300, 550),
    ("ドライベースボールシャツ", 100, 500, "通常", 1610, 300, 300, 550),

    ("ドライロングスリープTシャツ", 10, 14, "早割", 2030, 850, 850, 550),
    ("ドライロングスリープTシャツ", 10, 14, "通常", 2230, 850, 850, 550),
    ("ドライロングスリープTシャツ", 15, 19, "早割", 1670, 650, 650, 550),
    ("ドライロングスリープTシャツ", 15, 19, "通常", 1870, 650, 650, 550),
    ("ドライロングスリープTシャツ", 20, 29, "早割", 1430, 450, 450, 550),
    ("ドライロングスリープTシャツ", 20, 29, "通常", 1630, 450, 450, 550),
    ("ドライロングスリープTシャツ", 30, 39, "早割", 1260, 350, 350, 550),
    ("ドライロングスリープTシャツ", 30, 39, "通常", 1460, 350, 350, 550),
    ("ドライロングスリープTシャツ", 40, 49, "早割", 1180, 350, 350, 550),
    ("ドライロングスリープTシャツ", 40, 49, "通常", 1380, 350, 350, 550),
    ("ドライロングスリープTシャツ", 50, 99, "早割", 1090, 350, 350, 550),
    ("ドライロングスリープTシャツ", 50, 99, "通常", 1290, 350, 350, 550),
    ("ドライロングスリープTシャツ", 100, 500, "早割", 970, 300, 300, 550),
    ("ドライロングスリープTシャツ", 100, 500, "通常", 1170, 300, 300, 550),

    ("ドライハーフパンツ", 10, 14, "早割", 2270, 850, 850, 550),
    ("ドライハーフパンツ", 10, 14, "通常", 2470, 850, 850, 550),
    ("ドライハーフパンツ", 15, 19, "早割", 1910, 650, 650, 550),
    ("ドライハーフパンツ", 15, 19, "通常", 2110, 650, 650, 550),
    ("ドライハーフパンツ", 20, 29, "早割", 1670, 450, 450, 550),
    ("ドライハーフパンツ", 20, 29, "通常", 1870, 450, 450, 550),
    ("ドライハーフパンツ", 30, 39, "早割", 1500, 350, 350, 550),
    ("ドライハーフパンツ", 30, 39, "通常", 1700, 350, 350, 550),
    ("ドライハーフパンツ", 40, 49, "早割", 1420, 350, 350, 550),
    ("ドライハーフパンツ", 40, 49, "通常", 1620, 350, 350, 550),
    ("ドライハーフパンツ", 50, 99, "早割", 1330, 350, 350, 550),
    ("ドライハーフパンツ", 50, 99, "通常", 1530, 350, 350, 550),
    ("ドライハーフパンツ", 100, 500, "早割", 1210, 300, 300, 550),
    ("ドライハーフパンツ", 100, 500, "通常", 1410, 300, 300, 550),

    ("ヘビーウェイトロングスリープTシャツ", 10, 14, "早割", 2330, 850, 850, 550),
    ("ヘビーウェイトロングスリープTシャツ", 10, 14, "通常", 2530, 850, 850, 550),
    ("ヘビーウェイトロングスリープTシャツ", 15, 19, "早割", 1970, 650, 650, 550),
    ("ヘビーウェイトロングスリープTシャツ", 15, 19, "通常", 2170, 650, 650, 550),
    ("ヘビーウェイトロングスリープTシャツ", 20, 29, "早割", 1730, 450, 450, 550),
    ("ヘビーウェイトロングスリープTシャツ", 20, 29, "通常", 1930, 450, 450, 550),
    ("ヘビーウェイトロングスリープTシャツ", 30, 39, "早割", 1560, 350, 350, 550),
    ("ヘビーウェイトロングスリープTシャツ", 30, 39, "通常", 1760, 350, 350, 550),
    ("ヘビーウェイトロングスリープTシャツ", 40, 49, "早割", 1480, 350, 350, 550),
    ("ヘビーウェイトロングスリープTシャツ", 40, 49, "通常", 1680, 350, 350, 550),
    ("ヘビーウェイトロングスリープTシャツ", 50, 99, "早割", 1390, 350, 350, 550),
    ("ヘビーウェイトロングスリープTシャツ", 50, 99, "通常", 1590, 350, 350, 550),
    ("ヘビーウェイトロングスリープTシャツ", 100, 500, "早割", 1270, 300, 300, 550),
    ("ヘビーウェイトロングスリープTシャツ", 100, 500, "通常", 1470, 300, 300, 550),

    ("クルーネックライトトレーナー", 10, 14, "早割", 2870, 850, 850, 550),
    ("クルーネックライトトレーナー", 10, 14, "通常", 3070, 850, 850, 550),
    ("クルーネックライトトレーナー", 15, 19, "早割", 2510, 650, 650, 550),
    ("クルーネックライトトレーナー", 15, 19, "通常", 2710, 650, 650, 550),
    ("クルーネックライトトレーナー", 20, 29, "早割", 2270, 450, 450, 550),
    ("クルーネックライトトレーナー", 20, 29, "通常", 2470, 450, 450, 550),
    ("クルーネックライトトレーナー", 30, 39, "早割", 2100, 350, 350, 550),
    ("クルーネックライトトレーナー", 30, 39, "通常", 2300, 350, 350, 550),
    ("クルーネックライトトレーナー", 40, 49, "早割", 2020, 350, 350, 550),
    ("クルーネックライトトレーナー", 40, 49, "通常", 2220, 350, 350, 550),
    ("クルーネックライトトレーナー", 50, 99, "早割", 1930, 350, 350, 550),
    ("クルーネックライトトレーナー", 50, 99, "通常", 2130, 350, 350, 550),
    ("クルーネックライトトレーナー", 100, 500, "早割", 1810, 300, 300, 550),
    ("クルーネックライトトレーナー", 100, 500, "通常", 2010, 300, 300, 550),

    ("フーデッドライトパーカー", 10, 14, "早割", 3270, 850, 850, 550),
    ("フーデッドライトパーカー", 10, 14, "通常", 3470, 850, 850, 550),
    ("フーデッドライトパーカー", 15, 19, "早割", 2910, 650, 650, 550),
    ("フーデッドライトパーカー", 15, 19, "通常", 3110, 650, 650, 550),
    ("フーデッドライトパーカー", 20, 29, "早割", 2670, 450, 450, 550),
    ("フーデッドライトパーカー", 20, 29, "通常", 2870, 450, 450, 550),
    ("フーデッドライトパーカー", 30, 39, "早割", 2500, 350, 350, 550),
    ("フーデッドライトパーカー", 30, 39, "通常", 2700, 350, 350, 550),
    ("フーデッドライトパーカー", 40, 49, "早割", 2420, 350, 350, 550),
    ("フーデッドライトパーカー", 40, 49, "通常", 2620, 350, 350, 550),
    ("フーデッドライトパーカー", 50, 99, "早割", 2330, 350, 350, 550),
    ("フーデッドライトパーカー", 50, 99, "通常", 2530, 350, 350, 550),
    ("フーデッドライトパーカー", 100, 500, "早割", 2210, 300, 300, 550),
    ("フーデッドライトパーカー", 100, 500, "通常", 2410, 300, 300, 550),

    ("スタンダードトレーナー", 10, 14, "早割", 3280, 850, 850, 550),
    ("スタンダードトレーナー", 10, 14, "通常", 3480, 850, 850, 550),
    ("スタンダードトレーナー", 15, 19, "早割", 2920, 650, 650, 550),
    ("スタンダードトレーナー", 15, 19, "通常", 3120, 650, 650, 550),
    ("スタンダードトレーナー", 20, 29, "早割", 2680, 450, 450, 550),
    ("スタンダードトレーナー", 20, 29, "通常", 2880, 450, 450, 550),
    ("スタンダードトレーナー", 30, 39, "早割", 2510, 350, 350, 550),
    ("スタンダードトレーナー", 30, 39, "通常", 2710, 350, 350, 550),
    ("スタンダードトレーナー", 40, 49, "早割", 2430, 350, 350, 550),
    ("スタンダードトレーナー", 40, 49, "通常", 2630, 350, 350, 550),
    ("スタンダードトレーナー", 50, 99, "早割", 2340, 350, 350, 550),
    ("スタンダードトレーナー", 50, 99, "通常", 2540, 350, 350, 550),
    ("スタンダードトレーナー", 100, 500, "早割", 2220, 300, 300, 550),
    ("スタンダードトレーナー", 100, 500, "通常", 2420, 300, 300, 550),

    ("スタンダードWフードパーカー", 10, 14, "早割", 4040, 850, 850, 550),
    ("スタンダードWフードパーカー", 10, 14, "通常", 4240, 850, 850, 550),
    ("スタンダードWフードパーカー", 15, 19, "早割", 3680, 650, 650, 550),
    ("スタンダードWフードパーカー", 15, 19, "通常", 3880, 650, 650, 550),
    ("スタンダードWフードパーカー", 20, 29, "早割", 3440, 450, 450, 550),
    ("スタンダードWフードパーカー", 20, 29, "通常", 3640, 450, 450, 550),
    ("スタンダードWフードパーカー", 30, 39, "早割", 3270, 350, 350, 550),
    ("スタンダードWフードパーカー", 30, 39, "通常", 3470, 350, 350, 550),
    ("スタンダードWフードパーカー", 40, 49, "早割", 3190, 350, 350, 550),
    ("スタンダードWフードパーカー", 40, 49, "通常", 3390, 350, 350, 550),
    ("スタンダードWフードパーカー", 50, 99, "早割", 3100, 350, 350, 550),
    ("スタンダードWフードパーカー", 50, 99, "通常", 3300, 350, 350, 550),
    ("スタンダードWフードパーカー", 100, 500, "早割", 2980, 300, 300, 550),
    ("スタンダードWフードパーカー", 100, 500, "通常", 3180, 300, 300, 550),

    ("ジップアップライトパーカー", 10, 14, "早割", 3770, 850, 850, 550),
    ("ジップアップライトパーカー", 10, 14, "通常", 3970, 850, 850, 550),
    ("ジップアップライトパーカー", 15, 19, "早割", 3410, 650, 650, 550),
    ("ジップアップライトパーカー", 15, 19, "通常", 3610, 650, 650, 550),
    ("ジップアップライトパーカー", 20, 29, "早割", 3170, 450, 450, 550),
    ("ジップアップライトパーカー", 20, 29, "通常", 3370, 450, 450, 550),
    ("ジップアップライトパーカー", 30, 39, "早割", 3000, 350, 350, 550),
    ("ジップアップライトパーカー", 30, 39, "通常", 3200, 350, 350, 550),
    ("ジップアップライトパーカー", 40, 49, "早割", 2920, 350, 350, 550),
    ("ジップアップライトパーカー", 40, 49, "通常", 3120, 350, 350, 550),
    ("ジップアップライトパーカー", 50, 99, "早割", 2830, 350, 350, 550),
    ("ジップアップライトパーカー", 50, 99, "通常", 3030, 350, 350, 550),
    ("ジップアップライトパーカー", 100, 500, "早割", 2710, 300, 300, 550),
    ("ジップアップライトパーカー", 100, 500, "通常", 2910, 300, 300, 550),
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

    # ▼▼ 「注文用紙から注文」で写真待ちの状態でテキストを受け取った場合のガード ▼▼
    if user_id in user_states and user_states[user_id].get("state") == "await_order_form_photo":
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="注文用紙の写真を送ってください。テキストはまだ受け付けていません。")
        )
        return
    # ▲▲

    if user_id in user_states:
        st = user_states[user_id].get("state")
        # 以下、既存のステートマシン処理
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

    # どのステートでもない通常メッセージ
    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text=f"あなたのメッセージ: {user_input}")
    )

###################################
# (J') LINEハンドラ: ImageMessage
###################################
@handler.add(MessageEvent, message=ImageMessage)
def handle_image_message(event):
    user_id = event.source.user_id

    # 状態が "await_order_form_photo" 以外の場合はスルー
    if user_id not in user_states or user_states[user_id].get("state") != "await_order_form_photo":
        return

    # 画像取得
    message_id = event.message.id
    message_content = line_bot_api.get_message_content(message_id)
    
    # 一時的にローカル保存する
    temp_filename = f"temp_{user_id}_{message_id}.jpg"
    with open(temp_filename, "wb") as fd:
        for chunk in message_content.iter_content():
            fd.write(chunk)

    # ローカルに保存した画像を使って Google Vision API OCR 処理
    ocr_text = google_vision_ocr(temp_filename)
    logger.info(f"[DEBUG] OCR result: {ocr_text}")

    # OpenAI API を呼び出して、webフォーム各項目に対応しそうな値を推定
    form_estimated_data = openai_extract_form_data(ocr_text)
    logger.info(f"[DEBUG] form_estimated_data from OpenAI: {form_estimated_data}")

    # 推定結果をユーザーごとの状態に保持しておき、フォーム表示の際に使う
    user_states[user_id]["paper_form_data"] = form_estimated_data
    # ステート終了
    del user_states[user_id]["state"]

    # ユーザーにフォームURLを案内し、修正・送信を促す
    paper_form_url = f"https://{request.host}/paper_order_form?user_id={user_id}"
    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(
            text=(
                "注文用紙の写真から情報を読み取りました。\n"
                "こちらのフォームに自動入力しましたので、内容をご確認・修正の上送信してください。\n"
                f"{paper_form_url}"
            )
        )
    )

    # ローカルファイル削除(任意)
    try:
        os.remove(temp_filename)
    except Exception:
        pass

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
        form_url = f"https://{request.host}/webform?user_id={user_id}"
        msg = (f"WEBフォームから注文ですね！\nこちらから入力してください。\n{form_url}")
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=msg))
        return

    if data == "paper_order":
        user_states[user_id] = {
            "state": "await_order_form_photo"
        }
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="注文用紙の写真を送ってください。\n(スマホで撮影したものでもOKです)")
        )
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

        # ▼▼ ここで簡易見積結果をまとめ、DBにINSERT + 見積番号発行 ▼▼
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
        
        # 1枚あたりの単価(ざっくり整数に)
        if qty > 0:
            unit_price = total_price // qty
        else:
            unit_price = 0

        # 見積番号を発行（例: "Q" + UNIXタイム）
        import time
        quote_number = f"Q{int(time.time())}"

        # DBにINSERTして保存
        insert_estimate(
            user_id,
            s['school_name'],
            s['prefecture'],
            s['early_discount'],
            s['budget'],
            product,
            qty,
            s['print_position'],
            color_opt,
            total_price,
            unit_price,
            quote_number
        )

        # これ以上ステートを追わないので削除
        del user_states[user_id]

        # ▼▼ ユーザーに送るメッセージ(一括)＋モード選択画面 ▼▼
        reply_text = (
            "全項目の入力が完了しました。\n\n" + summary +
            "\n\n--- 見積計算結果 ---\n"
            f"見積番号: {quote_number}\n"
            f"合計金額: ¥{total_price:,}\n"
            f"1枚あたりの単価: ¥{unit_price:,}\n"
            "ご注文に進まれる場合はWEBフォームから注文\n"
            "もしくは注文用紙から注文を選択してください。"
        )
        line_bot_api.reply_message(
            event.reply_token,
            [
                TextSendMessage(text=reply_text),
                create_mode_selection_flex()
            ]
        )
        return

    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"不明なアクション: {data}"))

# ▼▼ 追加: estimatesテーブルにINSERTする関数 ▼▼
def insert_estimate(
    user_id,
    school_name,
    prefecture,
    early_discount,
    budget,
    product,
    quantity,
    print_position,
    color_options,
    total_price,
    unit_price,
    quote_number
):
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            sql = """
            INSERT INTO estimates (
                user_id,
                school_name,
                prefecture,
                early_discount,
                budget,
                product,
                quantity,
                print_position,
                color_options,
                total_price,
                unit_price,
                quote_number,
                order_placed,
                reminder_count,
                created_at
            ) VALUES (
                %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s,
                %s, %s, false, 0, NOW()
            )
            """
            params = (
                user_id,
                school_name,
                prefecture,
                early_discount,
                budget,
                product,
                quantity,
                print_position,
                color_options,
                total_price,
                unit_price,
                quote_number
            )
            cur.execute(sql, params)
        conn.commit()

###################################
# (L) WEBフォーム (修正)
###################################
FORM_HTML = """
<!DOCTYPE html>
<html>
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0, user-scalable=no">
  <style>
    body {
      margin: 16px;
      font-family: sans-serif;
      font-size: 16px;
      line-height: 1.5;
    }
    h1 {
      margin-bottom: 24px;
      font-size: 1.2em;
    }
    form {
      max-width: 600px;
      margin: 0 auto;
    }
    input[type="text"],
    input[type="number"],
    input[type="email"],
    input[type="date"],
    select,
    button {
      display: block;
      width: 100%;
      box-sizing: border-box;
      margin-bottom: 16px;
      padding: 8px;
      font-size: 16px;
    }
    .radio-group,
    .checkbox-group {
      margin-bottom: 16px;
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
    }
    .radio-group label,
    .checkbox-group label {
      display: flex;
      align-items: center;
    }
    h3 {
      margin-top: 24px;
      margin-bottom: 8px;
      font-size: 1.1em;
    }
    p.instruction {
      font-size: 14px;
      color: #555;
    }

    /* ▼▼ Tシャツ描画用のスタイル ▼▼ */
    .tshirt-container {
      width: 300px;    /* Tシャツ画像の表示幅(お好みで) */
      margin-bottom: 16px;
      position: relative; /* 絶対配置などに対応できるように */
    }
    svg {
      width: 100%;
      height: auto;
      display: block;
    }
    .tshirt-shape {
      fill: #f5f5f5;   /* Tシャツの色(薄いグレー) */
      stroke: #aaa;    /* 縁取り */
      stroke-width: 2;
    }
    /* クリック領域(①～⑭)となる円や四角 */
    .click-area {
      fill: white;
      stroke: black;
      cursor: pointer;
      transition: 0.2s;
    }
    .click-area:hover {
      fill: orange;    /* ホバー時に変化 */
    }
    .click-area.selected {
      fill: orange;    /* クリック選択後の色 */
    }
    /* 番号ラベルのスタイル (クリックを透過) */
    .area-label {
      pointer-events: none;
      font-size: 12px;
      text-anchor: middle;
      alignment-baseline: middle;
      user-select: none;
    }
  </style>
</head>
<body>
  <h1>WEBフォームから注文</h1>
  <form action="/webform_submit" method="POST" enctype="multipart/form-data">

    <!-- 既存: user_id (LINE user_id) -->
    <input type="hidden" name="user_id" value="{{ user_id }}" />

    <label>申込日:</label>
    <input type="date" name="application_date">

    <label>配達日:</label>
    <input type="date" name="delivery_date">

    <label>使用日:</label>
    <input type="date" name="use_date">

    <label>利用する学割特典:</label>
    <select name="discount_option">
      <option value="早割">早割</option>
      <option value="タダ割">タダ割</option>
      <option value="いっしょ割り">いっしょ割り</option>
    </select>

    <label>学校名:</label>
    <input type="text" name="school_name">

    <label>LINEアカウント名:</label>
    <input type="text" name="line_account">

    <label>団体名:</label>
    <input type="text" name="group_name">

    <label>学校住所:</label>
    <input type="text" name="school_address">

    <label>学校TEL:</label>
    <input type="text" name="school_tel">

    <label>担任名:</label>
    <input type="text" name="teacher_name">

    <label>担任携帯:</label>
    <input type="text" name="teacher_tel">

    <label>担任メール:</label>
    <input type="email" name="teacher_email">

    <label>代表者:</label>
    <input type="text" name="representative">

    <label>代表者TEL:</label>
    <input type="text" name="rep_tel">

    <label>代表者メール:</label>
    <input type="email" name="rep_email">

    <label>デザイン確認方法:</label>
    <select name="design_confirm">
      <option value="LINE代表者">LINE代表者</option>
      <option value="LINEご担任(保護者)">LINEご担任(保護者)</option>
      <option value="メール代表者">メール代表者</option>
      <option value="メールご担任(保護者)">メールご担任(保護者)</option>
    </select>

    <label>お支払い方法:</label>
    <select name="payment_method">
      <option value="代金引換(ヤマト運輸/現金のみ)">代金引換(ヤマト運輸/現金のみ)</option>
      <option value="後払い(コンビニ/郵便振替)">後払い(コンビニ/郵便振替)</option>
      <option value="後払い(銀行振込)">後払い(銀行振込)</option>
      <option value="先払い(銀行振込)">先払い(銀行振込)</option>
    </select>

    <label>商品名:</label>
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

    <label>商品カラー:</label>
    <input type="text" name="product_color">

    <label>サイズ(SS):</label>
    <input type="number" name="size_ss">

    <label>サイズ(S):</label>
    <input type="number" name="size_s">

    <label>サイズ(M):</label>
    <input type="number" name="size_m">

    <label>サイズ(L):</label>
    <input type="number" name="size_l">

    <label>サイズ(LL):</label>
    <input type="number" name="size_ll">

    <label>サイズ(LLL):</label>
    <input type="number" name="size_lll">


    <!-- ▼▼ 前面プリント ▼▼ -->
    <h3>プリント位置: 前</h3>
    <div class="radio-group">
      <label>
        <input type="radio" name="print_size_front" value="おまかせ (最大:横28cm x 縦35cm以内)" checked>
        おまかせ (最大:横28cm x 縦35cm以内)
      </label>
      <label>
        <input type="radio" name="print_size_front" value="custom">
        ヨコcm x タテcmくらい(入力する):
      </label>
    </div>
    <input type="text" name="print_size_front_custom" placeholder="例: 20cm x 15cm">
    <label>プリントカラー(前):</label>
    <input type="text" name="print_color_front" placeholder="全てのカラーをご記入ください。計xx色">
    <label>フォントNo.(前):</label>
    <input type="text" name="font_no_front" placeholder="例: X-XX">
    <label>プリントサンプル(前):</label>
    <input type="text" name="design_sample_front" placeholder="例: D-XXX">

    <label>プリント位置データ(前) (画像アップロード):</label>
    <input type="file" name="position_data_front">

    <!-- 
      (A) クリックで選択した前面位置(①～⑨)をセットするhidden/readonly入力
      Flask側: request.form.get("front_positions_selected")
    -->
    <input type="text" name="front_positions_selected" id="front_positions_selected"
           placeholder="前面選択: 1~9" readonly>

    <!-- ▼▼ Tシャツ前面SVG (①～⑨) ▼▼ -->
    <div class="tshirt-container">
      <svg viewBox="0 0 300 300">
        <!-- ざっくりした前面Tシャツ形 -->
        <path class="tshirt-shape" d="
          M 70,20
          L 230,20
          Q 240,30 230,40
          L 230,70
          L 280,70
          L 280,110
          L 230,110
          L 230,250
          L 70,250
          L 70,110
          L 20,110
          L 20,70
          L 70,70
          L 70,40
          Q 60,30 70,20
          Z
        "></path>

        <!-- ①～⑨ (円) -->
        <circle cx="45" cy="60" r="10" class="click-area" data-num="1"></circle>
        <text x="45" y="60" class="area-label">1</text>

        <circle cx="255" cy="60" r="10" class="click-area" data-num="2"></circle>
        <text x="255" y="60" class="area-label">2</text>

        <circle cx="110" cy="90" r="10" class="click-area" data-num="3"></circle>
        <text x="110" y="90" class="area-label">3</text>

        <circle cx="150" cy="90" r="10" class="click-area" data-num="4"></circle>
        <text x="150" y="90" class="area-label">4</text>

        <circle cx="190" cy="90" r="10" class="click-area" data-num="5"></circle>
        <text x="190" y="90" class="area-label">5</text>

        <circle cx="150" cy="130" r="10" class="click-area" data-num="6"></circle>
        <text x="150" y="130" class="area-label">6</text>

        <circle cx="100" cy="210" r="10" class="click-area" data-num="7"></circle>
        <text x="100" y="210" class="area-label">7</text>

        <circle cx="150" cy="210" r="10" class="click-area" data-num="8"></circle>
        <text x="150" y="210" class="area-label">8</text>

        <circle cx="200" cy="210" r="10" class="click-area" data-num="9"></circle>
        <text x="200" y="210" class="area-label">9</text>
      </svg>
    </div>


    <!-- ▼▼ 背面プリント ▼▼ -->
    <h3>プリント位置: 後</h3>
    <div class="radio-group">
      <label>
        <input type="radio" name="print_size_back" value="おまかせ (最大:横28cm x 縦35cm以内)" checked>
        おまかせ (最大:横28cm x 縦35cm以内)
      </label>
      <label>
        <input type="radio" name="print_size_back" value="custom">
        ヨコcm x タテcmくらい(入力する):
      </label>
    </div>
    <input type="text" name="print_size_back_custom" placeholder="例: 20cm x 15cm">
    <label>プリントカラー(後):</label>
    <input type="text" name="print_color_back" placeholder="全てのカラーをご記入ください。計xx色">
    <label>フォントNo.(後):</label>
    <input type="text" name="font_no_back" placeholder="例: X-XX">
    <label>プリントサンプル(後):</label>
    <input type="text" name="design_sample_back" placeholder="例: D-XXX">

    <label>プリント位置データ(後) (画像アップロード):</label>
    <input type="file" name="position_data_back">

    <!--
      (B) クリックで選択した背面位置(⑩～⑭)をセット
      Flask側: request.form.get("back_positions_selected")
    -->
    <input type="text" name="back_positions_selected" id="back_positions_selected"
           placeholder="背面選択: 10~14" readonly>

    <!-- ▼▼ Tシャツ背面SVG (⑩～⑭) ▼▼ -->
    <div class="tshirt-container">
      <svg viewBox="0 0 300 300">
        <path class="tshirt-shape" d="
          M 70,20
          L 230,20
          Q 240,30 230,40
          L 230,70
          L 280,70
          L 280,110
          L 230,110
          L 230,250
          L 70,250
          L 70,110
          L 20,110
          L 20,70
          L 70,70
          L 70,40
          Q 60,30 70,20
          Z
        "></path>

        <!-- ⑩～⑭ -->
        <circle cx="150" cy="60" r="10" class="click-area" data-num="10"></circle>
        <text x="150" y="60" class="area-label">10</text>

        <circle cx="150" cy="120" r="10" class="click-area" data-num="11"></circle>
        <text x="150" y="120" class="area-label">11</text>

        <circle cx="100" cy="210" r="10" class="click-area" data-num="12"></circle>
        <text x="100" y="210" class="area-label">12</text>

        <circle cx="150" cy="210" r="10" class="click-area" data-num="13"></circle>
        <text x="150" y="210" class="area-label">13</text>

        <circle cx="200" cy="210" r="10" class="click-area" data-num="14"></circle>
        <text x="200" y="210" class="area-label">14</text>
      </svg>
    </div>


    <!-- ▼▼ その他プリント位置 ▼▼ -->
    <h3>プリント位置: その他</h3>
    <div class="radio-group">
      <label>
        <input type="radio" name="print_size_other" value="おまかせ (最大:横28cm x 縦35cm以内)" checked>
        おまかせ (最大:横28cm x 縦35cm以内)
      </label>
      <label>
        <input type="radio" name="print_size_other" value="custom">
        ヨコcm x タテcmくらい(入力する):
      </label>
    </div>
    <input type="text" name="print_size_other_custom" placeholder="例: 20cm x 15cm">
    <label>プリントカラー(その他):</label>
    <input type="text" name="print_color_other" placeholder="全てのカラーをご記入ください。計xx色">
    <label>フォントNo.(その他):</label>
    <input type="text" name="font_no_other" placeholder="例: X-XX">
    <label>プリントサンプル(その他):</label>
    <input type="text" name="design_sample_other" placeholder="例: D-XXX">

    <label>プリント位置データ(その他) (画像アップロード):</label>
    <input type="file" name="position_data_other">


    <!-- ★★★ 背ネーム・背番号プリント（複数選択チェックボックス） ★★★ -->
    <h3>背ネーム・背番号プリント</h3>
    <p class="instruction">※複数選択可能</p>
    <div class="checkbox-group">
      <label>
        <input type="checkbox" name="back_name_number_print[]" value="ネーム&背番号セット"> ネーム&背番号セット
      </label>
      <label>
        <input type="checkbox" name="back_name_number_print[]" value="ネーム(大)"> ネーム(大)
      </label>
      <label>
        <input type="checkbox" name="back_name_number_print[]" value="ネーム(小)"> ネーム(小)
      </label>
      <label>
        <input type="checkbox" name="back_name_number_print[]" value="番号(大)"> 番号(大)
      </label>
      <label>
        <input type="checkbox" name="back_name_number_print[]" value="番号(小)"> 番号(小)
      </label>
    </div>

    <!-- ★ 追加デザインイメージアップロード (任意) -->
    <h3>追加のデザインイメージデータ</h3>
    <p class="instruction">プリント位置(前, 左胸, 右胸, 背中, 左袖, 右袖)を選択し、アップロードできます。</p>
    <label>プリント位置:</label>
    <select name="additional_design_position">
      <option value="">選択してください</option>
      <option value="前">前</option>
      <option value="左胸">左胸</option>
      <option value="右胸">右胸</option>
      <option value="背中">背中</option>
      <option value="左袖">左袖</option>
      <option value="右袖">右袖</option>
    </select>
    <label>デザインイメージデータ:</label>
    <input type="file" name="additional_design_image">

    <button type="submit">送信</button>

    <!-- 
      ▼▼ JavaScript: 前面(1～9)・背面(10～14)クリック時のイベント 
          クリック領域要素は .click-area 
    -->
    <script>
      // 前面: 1～9
      const frontSvgContainer = document.querySelectorAll('.tshirt-container')[0];
      const frontAreas = frontSvgContainer.querySelectorAll('.click-area');
      const frontPositionsInput = document.getElementById('front_positions_selected');

      frontAreas.forEach(area => {
        area.addEventListener('click', () => {
          // いったん全部の selected を外す
          frontAreas.forEach(a => a.classList.remove('selected'));
          // クリックしたものだけ selected
          area.classList.add('selected');
          // data-num を input に格納
          const num = area.getAttribute('data-num');
          frontPositionsInput.value = num;
        });
      });

      // 背面: 10～14
      const backSvgContainer = document.querySelectorAll('.tshirt-container')[1];
      const backAreas = backSvgContainer.querySelectorAll('.click-area');
      const backPositionsInput = document.getElementById('back_positions_selected');

      backAreas.forEach(area => {
        area.addEventListener('click', () => {
          backAreas.forEach(a => a.classList.remove('selected'));
          area.classList.add('selected');
          const num = area.getAttribute('data-num');
          backPositionsInput.value = num;
        });
      });
    </script>
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
    if not val:
        return None
    return val

def none_if_empty_date(val: str):
    """日付カラム用: 空なら None、そうでなければ文字列として返す(バリデーション簡略)"""
    if not val:
        return None
    return val

def none_if_empty_int(val: str):
    """数値カラム用: 空なら None, それ以外はintに変換"""
    if not val:
        return None
    return int(val)

###################################
# ▼▼ 新規追加機能: 注文時に価格を計算する関数
###################################
import datetime

def calculate_order_price(
    product_name: str,
    size_ss: int,
    size_s: int,
    size_m: int,
    size_l: int,
    size_ll: int,
    size_lll: int,
    application_date_str: str,
    use_date_str: str,
    print_color_front: str,
    print_color_back: str,
    print_color_other: str,
    design_sample_back: str,
    design_sample_other: str,
    back_name_number_print_options: str
) -> (str, int, int):
    """
    1. 製品名を取得
    2. 各サイズの数を合計して合計数量を取得
    3. 使用日 - 申込日 で 14日以上なら "早割", 14日以内なら "通常"
    4. PRICE_TABLE から unit_price を取得
    5. front/back/other のプリントカラーを判定し、2色以上なら addColor を加算
    7. design_sample_back があれば addPosition、 design_sample_other があれば addPosition
    9. back_name_number_print_options(複数可)に応じて追加料金加算
    10. 合計金額 + 枚数で割った単価を返す
    戻り値: (discount_type, total_price, unit_price)
    """

    # 合計枚数
    total_qty = 0
    for sz in [size_ss, size_s, size_m, size_l, size_ll, size_lll]:
        if sz:
            total_qty += sz

    # 日付差分による 早割 or 通常
    discount_type = "通常"  # デフォルト
    if application_date_str and use_date_str:
        try:
            fmt = "%Y-%m-%d"
            app_date = datetime.datetime.strptime(application_date_str, fmt).date()
            use_date = datetime.datetime.strptime(use_date_str, fmt).date()
            diff = (use_date - app_date).days
            if diff >= 14:
                discount_type = "早割"
            else:
                discount_type = "通常"
        except:
            pass  # 失敗時は通常扱い

    # PRICE_TABLEから unit_price, addColor, addPosition, addFullColor を取得
    row = None
    for item in PRICE_TABLE:
        (p_name, min_q, max_q, d_type, unit_price, add_color, add_position, add_full_color) = item
        if p_name == product_name and d_type == discount_type and (min_q <= total_qty <= max_q):
            row = item
            break

    if not row:
        # 見つからなかった場合は金額0
        return (discount_type, 0, 0)

    (_, _, _, _, unit_price, add_color, add_position, add_full_color) = row

    base_price = unit_price * total_qty
    extra_price = 0

    # プリントカラー数チェック: 2色以上なら add_color 加算
    def count_colors(color_str):
        if not color_str:
            return 0
        parts = [p.strip() for p in color_str.replace("、",",").replace("\n"," ").split(",")]
        colors = []
        for prt in parts:
            sub_parts = prt.split()
            for sub in sub_parts:
                if sub:
                    colors.append(sub)
        return len(colors)

    front_colors_count = count_colors(print_color_front)
    back_colors_count = count_colors(print_color_back)
    other_colors_count = count_colors(print_color_other)

    if front_colors_count >= 2:
        extra_price += add_color * total_qty
    if back_colors_count >= 2:
        extra_price += add_color * total_qty
    if other_colors_count >= 2:
        extra_price += add_color * total_qty

    # design_sample_back があれば addPosition
    if design_sample_back:
        extra_price += add_position * total_qty
    if design_sample_other:
        extra_price += add_position * total_qty

    # 背ネーム・背番号プリントオプション
    name_number_cost_map = {
        "ネーム&背番号セット": 900,
        "ネーム(大)": 550,
        "ネーム(小)": 250,
        "番号(大)": 550,
        "番号(小)": 250
    }
    if back_name_number_print_options:
        opts = [o.strip() for o in back_name_number_print_options.split(",")]
        for opt in opts:
            if opt in name_number_cost_map:
                extra_price += name_number_cost_map[opt] * total_qty

    total_price = base_price + extra_price
    unit_price_calc = total_price // total_qty if total_qty > 0 else 0

    return (discount_type, total_price, unit_price_calc)

###################################
# (N) /webform_submit: フォーム送信
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

    size_ss = none_if_empty_int(form.get("size_ss"))
    size_s = none_if_empty_int(form.get("size_s"))
    size_m = none_if_empty_int(form.get("size_m"))
    size_l = none_if_empty_int(form.get("size_l"))
    size_ll = none_if_empty_int(form.get("size_ll"))
    size_lll = none_if_empty_int(form.get("size_lll"))

    print_size_front = none_if_empty_str(form.get("print_size_front"))
    print_size_front_custom = none_if_empty_str(form.get("print_size_front_custom"))
    print_color_front = none_if_empty_str(form.get("print_color_front"))
    font_no_front = none_if_empty_str(form.get("font_no_front"))
    design_sample_front = none_if_empty_str(form.get("design_sample_front"))

    print_size_back = none_if_empty_str(form.get("print_size_back"))
    print_size_back_custom = none_if_empty_str(form.get("print_size_back_custom"))
    print_color_back = none_if_empty_str(form.get("print_color_back"))
    font_no_back = none_if_empty_str(form.get("font_no_back"))
    design_sample_back = none_if_empty_str(form.get("design_sample_back"))

    print_size_other = none_if_empty_str(form.get("print_size_other"))
    print_size_other_custom = none_if_empty_str(form.get("print_size_other_custom"))
    print_color_other = none_if_empty_str(form.get("print_color_other"))
    font_no_other = none_if_empty_str(form.get("font_no_other"))
    design_sample_other = none_if_empty_str(form.get("design_sample_other"))

    # ---------- 画像ファイル(位置データ) ----------
    pos_data_front = files.get("position_data_front")
    pos_data_back = files.get("position_data_back")
    pos_data_other = files.get("position_data_other")

    front_url = upload_file_to_s3(pos_data_front, S3_BUCKET_NAME, prefix="uploads/")
    back_url = upload_file_to_s3(pos_data_back, S3_BUCKET_NAME, prefix="uploads/")
    other_url = upload_file_to_s3(pos_data_other, S3_BUCKET_NAME, prefix="uploads/")

    # ---------- 追加のデザインイメージデータ ----------
    additional_design_position = none_if_empty_str(form.get("additional_design_position"))
    additional_design_image = files.get("additional_design_image")
    additional_design_image_url = upload_file_to_s3(additional_design_image, S3_BUCKET_NAME, prefix="uploads/")

    # ▼▼ 背ネーム・背番号プリント (複数選択) ▼▼
    selected_back_name_number_print = form.getlist("back_name_number_print[]")
    back_name_number_print_options = ",".join(selected_back_name_number_print) if selected_back_name_number_print else None

    # ★★★ ここで「本注文見積番号」を生成 ★★★
    import time
    order_quote_number = f"O{int(time.time())}"

    # DBに保存 (orders)
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

                print_size_front,
                print_size_front_custom,
                print_color_front,
                font_no_front,
                design_sample_front,
                position_data_front_url,

                print_size_back,
                print_size_back_custom,
                print_color_back,
                font_no_back,
                design_sample_back,
                position_data_back_url,

                print_size_other,
                print_size_other_custom,
                print_color_other,
                font_no_other,
                design_sample_other,
                position_data_other_url,

                additional_design_position,
                additional_design_image_url,

                back_name_number_print_options,

                -- ★ 新たに本注文見積番号を追加 ★
                order_quote_number,

                created_at
            ) VALUES (
                %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s,
                %s,

                %s, %s, %s, %s, %s, %s,

                %s, %s, %s, %s, %s, %s,

                %s, %s, %s, %s, %s, %s,

                %s,
                %s,

                %s,

                %s,

                NOW()
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

                print_size_front,
                print_size_front_custom,
                print_color_front,
                font_no_front,
                design_sample_front,
                front_url,

                print_size_back,
                print_size_back_custom,
                print_color_back,
                font_no_back,
                design_sample_back,
                back_url,

                print_size_other,
                print_size_other_custom,
                print_color_other,
                font_no_other,
                design_sample_other,
                other_url,

                additional_design_position,
                additional_design_image_url,

                back_name_number_print_options,

                order_quote_number,  # 本注文見積番号

            )
            cur.execute(sql, params)
            new_id = cur.fetchone()[0]
        conn.commit()
        logger.info(f"Inserted order id={new_id}, order_quote_number={order_quote_number}")

    # 見積→注文へのコンバージョンを示すため、estimatesテーブル側の order_placed = true に更新しておく例
    mark_estimate_as_ordered(user_id)

    # ★★★ ここで注文価格計算をして、LINEに通知する ★★★
    (discount_type, total_price, unit_price_calc) = calculate_order_price(
        product_name,
        size_ss or 0,
        size_s or 0,
        size_m or 0,
        size_l or 0,
        size_ll or 0,
        size_lll or 0,
        application_date or "",
        use_date or "",
        print_color_front or "",
        print_color_back or "",
        print_color_other or "",
        design_sample_back or "",
        design_sample_other or "",
        back_name_number_print_options or ""
    )

    used_positions = []
    if print_color_front:
        used_positions.append("前")
    if print_color_back:
        used_positions.append("後")
    if print_color_other:
        used_positions.append("その他")

    bn_options = back_name_number_print_options or "なし"

    push_text = (
        f"ご注文ありがとうございます。\n"
        f"学校名: {school_name}\n"
        f"商品名: {product_name}\n"
        f"商品カラー: {product_color}\n"
        f"プリント位置: {', '.join(used_positions) if used_positions else 'なし'}\n"
        f"背ネーム&背番号プリント: {bn_options}\n"
        f"割引種別: {discount_type}\n"
        f"合計金額: ¥{total_price:,}\n"
        f"1枚あたり: ¥{unit_price_calc:,}\n"
        f"本注文見積番号: {order_quote_number}\n"  # ★ここで表示
        "担当者より後ほどご連絡いたします。"
    )
    try:
        line_bot_api.push_message(to=user_id, messages=TextSendMessage(text=push_text))
    except Exception as e:
        logger.error(f"Push message failed: {e}")

    return "フォーム送信完了。LINEに通知を送りました。"

###################################
# (O) 例: CSV出力関数 (任意, 既存)
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
# ▼▼ 追加: Google Vision OCR処理
###################################
def google_vision_ocr(local_image_path: str) -> str:
    """
    Google Cloud Vision APIを用いて画像のOCRを行い、
    抽出されたテキスト全体を文字列で返すサンプル。
    """
    from google.cloud import vision

    client = vision.ImageAnnotatorClient()
    with open(local_image_path, "rb") as image_file:
        content = image_file.read()
    image = vision.Image(content=content)

    response = client.document_text_detection(image=image)
    if response.error.message:
        raise Exception(f"Vision API Error: {response.error.message}")

    full_text = response.full_text_annotation.text
    return full_text

###################################
# ▼▼ 追加: OpenAIでテキスト解析
###################################
import openai

def openai_extract_form_data(ocr_text: str) -> dict:
    """
    OCRテキストから注文フォーム項目を推定し、JSONを返す例。
    （デモ用のため簡易的なプロンプトのみ）
    """
    openai.api_key = OPENAI_API_KEY

    system_prompt = """あなたは注文用紙のOCR結果から必要な項目を抽出するアシスタントです。
    入力として渡されるテキスト（OCR結果）を解析し、次のフォーム項目に合致する値を抽出してJSONで返してください。
    日付項目（application_date, delivery_date, use_date）は必ず YYYY-MM-DD の形式で返してください
    必ず JSON のみを返し、余計な文章は一切出力しないでください。
    キー一覧: [
        "application_date","delivery_date","use_date","discount_option","school_name",
        "line_account","group_name","school_address","school_tel","teacher_name",
        "teacher_tel","teacher_email","representative","rep_tel","rep_email",
        "design_confirm","payment_method","product_name","product_color",
        "size_ss","size_s","size_m","size_l","size_ll","size_lll",
        "print_size_front","print_size_front_custom","print_color_front","font_no_front","design_sample_front",
        "print_size_back","print_size_back_custom","print_color_back","font_no_back","design_sample_back",
        "print_size_other","print_size_other_custom","print_color_other","font_no_other","design_sample_other"
    ]
    """

    user_prompt = f"""
以下OCRテキストです:
{ocr_text}
上記に基づき、フォーム項目に合致する値をJSONのみで返してください。
    """

    response = openai.ChatCompletion.create(
        model="gpt-3.5-turbo",
        temperature=0.2,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ]
    )
    content = response["choices"][0]["message"]["content"]
    logger.info(f"OpenAI raw content: {content}")

    # JSONとしてパースを試みる
    try:
        result = json.loads(content)
    except json.JSONDecodeError:
        result = {}

    return result

###################################
# ▼▼ 注文用紙フロー用フォーム
###################################
PAPER_FORM_HTML = """
<!DOCTYPE html>
<html>
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0, user-scalable=no">
  <style>
    body {
      margin: 16px;
      font-family: sans-serif;
      font-size: 16px;
      line-height: 1.5;
    }
    h1 {
      margin-bottom: 24px;
      font-size: 1.2em;
    }
    form {
      max-width: 600px;
      margin: 0 auto;
    }
    input[type="text"],
    input[type="number"],
    input[type="email"],
    input[type="date"],
    select,
    button {
      display: block;
      width: 100%;
      box-sizing: border-box;
      margin-bottom: 16px;
      padding: 8px;
      font-size: 16px;
    }
    .radio-group,
    .checkbox-group {
      margin-bottom: 16px;
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
    }
    .radio-group label,
    .checkbox-group label {
      display: flex;
      align-items: center;
    }
    h3 {
      margin-top: 24px;
      margin-bottom: 8px;
      font-size: 1.1em;
    }
    p.instruction {
      font-size: 14px;
      color: #555;
    }

    /* ▼▼ Tシャツ描画用のスタイル ▼▼ */
    .tshirt-container {
      width: 300px;    /* Tシャツ画像の表示幅(お好みで) */
      margin-bottom: 16px;
      position: relative; 
    }
    svg {
      width: 100%;
      height: auto;
      display: block;
    }
    .tshirt-shape {
      fill: #f5f5f5;   /* Tシャツの色(薄いグレー) */
      stroke: #aaa;    /* 縁取り */
      stroke-width: 2;
    }
    .click-area {
      fill: white;
      stroke: black;
      cursor: pointer;
      transition: 0.2s;
    }
    .click-area:hover {
      fill: orange;
    }
    .click-area.selected {
      fill: orange;
    }
    .area-label {
      pointer-events: none; /* テキスト自体はクリックの妨げにならない */
      font-size: 12px;
      text-anchor: middle;
      alignment-baseline: middle;
      user-select: none;
    }
  </style>
</head>
<body>
  <h1>注文用紙(写真)からの注文</h1>
  <form action="/paper_order_form_submit" method="POST" enctype="multipart/form-data">
    <input type="hidden" name="user_id" value="{{ user_id }}" />

    <label>申込日:</label>
    <input type="date" name="application_date" value="{{ data['application_date'] or '' }}">

    <label>配達日:</label>
    <input type="date" name="delivery_date" value="{{ data['delivery_date'] or '' }}">

    <label>使用日:</label>
    <input type="date" name="use_date" value="{{ data['use_date'] or '' }}">

    <label>利用する学割特典:</label>
    <select name="discount_option">
      <option value="早割" {% if data['discount_option'] == '早割' %}selected{% endif %}>早割</option>
      <option value="タダ割" {% if data['discount_option'] == 'タダ割' %}selected{% endif %}>タダ割</option>
      <option value="いっしょ割り" {% if data['discount_option'] == 'いっしょ割り' %}selected{% endif %}>いっしょ割り</option>
    </select>

    <label>学校名:</label>
    <input type="text" name="school_name" value="{{ data['school_name'] or '' }}">

    <label>LINEアカウント名:</label>
    <input type="text" name="line_account" value="{{ data['line_account'] or '' }}">

    <label>団体名:</label>
    <input type="text" name="group_name" value="{{ data['group_name'] or '' }}">

    <label>学校住所:</label>
    <input type="text" name="school_address" value="{{ data['school_address'] or '' }}">

    <label>学校TEL:</label>
    <input type="text" name="school_tel" value="{{ data['school_tel'] or '' }}">

    <label>担任名:</label>
    <input type="text" name="teacher_name" value="{{ data['teacher_name'] or '' }}">

    <label>担任携帯:</label>
    <input type="text" name="teacher_tel" value="{{ data['teacher_tel'] or '' }}">

    <label>担任メール:</label>
    <input type="email" name="teacher_email" value="{{ data['teacher_email'] or '' }}">

    <label>代表者:</label>
    <input type="text" name="representative" value="{{ data['representative'] or '' }}">

    <label>代表者TEL:</label>
    <input type="text" name="rep_tel" value="{{ data['rep_tel'] or '' }}">

    <label>代表者メール:</label>
    <input type="email" name="rep_email" value="{{ data['rep_email'] or '' }}">

    <label>デザイン確認方法:</label>
    <select name="design_confirm">
      <option value="LINE代表者" {% if data['design_confirm'] == 'LINE代表者' %}selected{% endif %}>LINE代表者</option>
      <option value="LINEご担任(保護者)" {% if data['design_confirm'] == 'LINEご担任(保護者)' %}selected{% endif %}>LINEご担任(保護者)</option>
      <option value="メール代表者" {% if data['design_confirm'] == 'メール代表者' %}selected{% endif %}>メール代表者</option>
      <option value="メールご担任(保護者)" {% if data['design_confirm'] == 'メールご担任(保護者)' %}selected{% endif %}>メールご担任(保護者)</option>
    </select>

    <label>お支払い方法:</label>
    <select name="payment_method">
      <option value="代金引換(ヤマト運輸/現金のみ)" {% if data['payment_method'] == '代金引換(ヤマト運輸/現金のみ)' %}selected{% endif %}>代金引換(ヤマト運輸/現金のみ)</option>
      <option value="後払い(コンビニ/郵便振替)" {% if data['payment_method'] == '後払い(コンビニ/郵便振替)' %}selected{% endif %}>後払い(コンビニ/郵便振替)</option>
      <option value="後払い(銀行振込)" {% if data['payment_method'] == '後払い(銀行振込)' %}selected{% endif %}>後払い(銀行振込)</option>
      <option value="先払い(銀行振込)" {% if data['payment_method'] == '先払い(銀行振込)' %}selected{% endif %}>先払い(銀行振込)</option>
    </select>

    <label>商品名:</label>
    <select name="product_name">
      <option value="ドライTシャツ" {% if data['product_name'] == 'ドライTシャツ' %}selected{% endif %}>ドライTシャツ</option>
      <option value="ヘビーウェイトTシャツ" {% if data['product_name'] == 'ヘビーウェイトTシャツ' %}selected{% endif %}>ヘビーウェイトTシャツ</option>
      <option value="ドライポロシャツ" {% if data['product_name'] == 'ドライポロシャツ' %}selected{% endif %}>ドライポロシャツ</option>
      <option value="ドライメッシュビブス" {% if data['product_name'] == 'ドライメッシュビブス' %}selected{% endif %}>ドライメッシュビブス</option>
      <option value="ドライベースボールシャツ" {% if data['product_name'] == 'ドライベースボールシャツ' %}selected{% endif %}>ドライベースボールシャツ</option>
      <option value="ドライロングスリープTシャツ" {% if data['product_name'] == 'ドライロングスリープTシャツ' %}selected{% endif %}>ドライロングスリープTシャツ</option>
      <option value="ドライハーフパンツ" {% if data['product_name'] == 'ドライハーフパンツ' %}selected{% endif %}>ドライハーフパンツ</option>
      <option value="ヘビーウェイトロングスリープTシャツ" {% if data['product_name'] == 'ヘビーウェイトロングスリープTシャツ' %}selected{% endif %}>ヘビーウェイトロングスリープTシャツ</option>
      <option value="クルーネックライトトレーナー" {% if data['product_name'] == 'クルーネックライトトレーナー' %}selected{% endif %}>クルーネックライトトレーナー</option>
      <option value="フーデッドライトパーカー" {% if data['product_name'] == 'フーデッドライトパーカー' %}selected{% endif %}>フーデッドライトパーカー</option>
      <option value="スタンダードトレーナー" {% if data['product_name'] == 'スタンダードトレーナー' %}selected{% endif %}>スタンダードトレーナー</option>
      <option value="スタンダードWフードパーカー" {% if data['product_name'] == 'スタンダードWフードパーカー' %}selected{% endif %}>スタンダードWフードパーカー</option>
      <option value="ジップアップライトパーカー" {% if data['product_name'] == 'ジップアップライトパーカー' %}selected{% endif %}>ジップアップライトパーカー</option>
    </select>

    <label>商品カラー:</label>
    <input type="text" name="product_color" value="{{ data['product_color'] or '' }}">

    <label>サイズ(SS):</label>
    <input type="number" name="size_ss" value="{{ data['size_ss'] or '' }}">

    <label>サイズ(S):</label>
    <input type="number" name="size_s" value="{{ data['size_s'] or '' }}">

    <label>サイズ(M):</label>
    <input type="number" name="size_m" value="{{ data['size_m'] or '' }}">

    <label>サイズ(L):</label>
    <input type="number" name="size_l" value="{{ data['size_l'] or '' }}">

    <label>サイズ(LL):</label>
    <input type="number" name="size_ll" value="{{ data['size_ll'] or '' }}">

    <label>サイズ(LLL):</label>
    <input type="number" name="size_lll" value="{{ data['size_lll'] or '' }}">


    <!-- ======================
         ▼▼ 前面プリント ▼▼
         ====================== -->
    <h3>プリント位置: 前</h3>
    <div class="radio-group">
      <label>
        <input type="radio" name="print_size_front"
               value="おまかせ (最大:横28cm x 縦35cm以内)"
               {% if data.get('print_size_front') == 'おまかせ (最大:横28cm x 縦35cm以内)' %}checked{% endif %}>
        おまかせ (最大:横28cm x 縦35cm以内)
      </label>
      <label>
        <input type="radio" name="print_size_front" value="custom"
               {% if data.get('print_size_front') == 'custom' %}checked{% endif %}>
        ヨコcm x タテcmくらい(入力する):
      </label>
    </div>
    <input type="text" name="print_size_front_custom"
           placeholder="例: 20cm x 15cm"
           value="{{ data.get('print_size_front_custom') or '' }}">
    <label>プリントカラー(前):</label>
    <input type="text" name="print_color_front"
           placeholder="全てのカラーをご記入ください。計xx色"
           value="{{ data.get('print_color_front') or '' }}">

    <label>フォントNo.(前):</label>
    <input type="text" name="font_no_front"
           placeholder="例: X-XX"
           value="{{ data.get('font_no_front') or '' }}">

    <label>プリントサンプル(前):</label>
    <input type="text" name="design_sample_front"
           placeholder="例: D-XXX"
           value="{{ data.get('design_sample_front') or '' }}">

    <label>プリント位置データ(前):</label>
    <input type="file" name="position_data_front">

    <!-- (A) 前面①～⑨の選択結果を格納 -->
    <input type="text" name="front_positions_selected" id="front_positions_selected"
           placeholder="前面で選んだ番号(1~9)" readonly
           value="{{ data.get('front_positions_selected') or '' }}">

    <!-- ▼▼ Tシャツ前面: ①～⑨ ▼▼ -->
    <div class="tshirt-container">
      <svg viewBox="0 0 300 300">
        <path class="tshirt-shape" d="
          M 70,20
          L 230,20
          Q 240,30 230,40
          L 230,70
          L 280,70
          L 280,110
          L 230,110
          L 230,250
          L 70,250
          L 70,110
          L 20,110
          L 20,70
          L 70,70
          L 70,40
          Q 60,30 70,20
          Z
        "></path>

        <!-- ①～⑨ (円) -->
        <circle cx="45" cy="60" r="10"
                class="click-area"
                data-num="1"></circle>
        <text x="45" y="60" class="area-label">1</text>

        <circle cx="255" cy="60" r="10"
                class="click-area"
                data-num="2"></circle>
        <text x="255" y="60" class="area-label">2</text>

        <circle cx="110" cy="90" r="10"
                class="click-area"
                data-num="3"></circle>
        <text x="110" y="90" class="area-label">3</text>

        <circle cx="150" cy="90" r="10"
                class="click-area"
                data-num="4"></circle>
        <text x="150" y="90" class="area-label">4</text>

        <circle cx="190" cy="90" r="10"
                class="click-area"
                data-num="5"></circle>
        <text x="190" y="90" class="area-label">5</text>

        <circle cx="150" cy="130" r="10"
                class="click-area"
                data-num="6"></circle>
        <text x="150" y="130" class="area-label">6</text>

        <circle cx="100" cy="210" r="10"
                class="click-area"
                data-num="7"></circle>
        <text x="100" y="210" class="area-label">7</text>

        <circle cx="150" cy="210" r="10"
                class="click-area"
                data-num="8"></circle>
        <text x="150" y="210" class="area-label">8</text>

        <circle cx="200" cy="210" r="10"
                class="click-area"
                data-num="9"></circle>
        <text x="200" y="210" class="area-label">9</text>
      </svg>
    </div>


    <!-- ======================
         ▼▼ 背面プリント ▼▼
         ====================== -->
    <h3>プリント位置: 後</h3>
    <div class="radio-group">
      <label>
        <input type="radio" name="print_size_back"
               value="おまかせ (最大:横28cm x 縦35cm以内)"
               {% if data.get('print_size_back') == 'おまかせ (最大:横28cm x 縦35cm以内)' %}checked{% endif %}>
        おまかせ (最大:横28cm x 縦35cm以内)
      </label>
      <label>
        <input type="radio" name="print_size_back" value="custom"
               {% if data.get('print_size_back') == 'custom' %}checked{% endif %}>
        ヨコcm x タテcmくらい(入力する):
      </label>
    </div>
    <input type="text" name="print_size_back_custom"
           placeholder="例: 20cm x 15cm"
           value="{{ data.get('print_size_back_custom') or '' }}">
    <label>プリントカラー(後):</label>
    <input type="text" name="print_color_back"
           placeholder="全てのカラーをご記入ください。計xx色"
           value="{{ data.get('print_color_back') or '' }}">

    <label>フォントNo.(後):</label>
    <input type="text" name="font_no_back"
           placeholder="例: X-XX"
           value="{{ data.get('font_no_back') or '' }}">

    <label>プリントサンプル(後):</label>
    <input type="text" name="design_sample_back"
           placeholder="例: D-XXX"
           value="{{ data.get('design_sample_back') or '' }}">

    <label>プリント位置データ(後):</label>
    <input type="file" name="position_data_back">

    <!-- (B) 背面⑩～⑭の選択結果を格納 -->
    <input type="text" name="back_positions_selected" id="back_positions_selected"
           placeholder="背面で選んだ番号(10~14)" readonly
           value="{{ data.get('back_positions_selected') or '' }}">

    <!-- ▼▼ Tシャツ背面: ⑩～⑭ ▼▼ -->
    <div class="tshirt-container">
      <svg viewBox="0 0 300 300">
        <path class="tshirt-shape" d="
          M 70,20
          L 230,20
          Q 240,30 230,40
          L 230,70
          L 280,70
          L 280,110
          L 230,110
          L 230,250
          L 70,250
          L 70,110
          L 20,110
          L 20,70
          L 70,70
          L 70,40
          Q 60,30 70,20
          Z
        "></path>

        <circle cx="150" cy="60" r="10"
                class="click-area"
                data-num="10"></circle>
        <text x="150" y="60" class="area-label">10</text>

        <circle cx="150" cy="120" r="10"
                class="click-area"
                data-num="11"></circle>
        <text x="150" y="120" class="area-label">11</text>

        <circle cx="100" cy="210" r="10"
                class="click-area"
                data-num="12"></circle>
        <text x="100" y="210" class="area-label">12</text>

        <circle cx="150" cy="210" r="10"
                class="click-area"
                data-num="13"></circle>
        <text x="150" y="210" class="area-label">13</text>

        <circle cx="200" cy="210" r="10"
                class="click-area"
                data-num="14"></circle>
        <text x="200" y="210" class="area-label">14</text>
      </svg>
    </div>


    <!-- ======================
         ▼▼ その他プリント ▼▼
         ====================== -->
    <h3>プリント位置: その他</h3>
    <div class="radio-group">
      <label>
        <input type="radio" name="print_size_other"
               value="おまかせ (最大:横28cm x 縦35cm以内)"
               {% if data.get('print_size_other') == 'おまかせ (最大:横28cm x 縦35cm以内)' %}checked{% endif %}>
        おまかせ (最大:横28cm x 縦35cm以内)
      </label>
      <label>
        <input type="radio" name="print_size_other" value="custom"
               {% if data.get('print_size_other') == 'custom' %}checked{% endif %}>
        ヨコcm x タテcmくらい(入力する):
      </label>
    </div>
    <input type="text" name="print_size_other_custom"
           placeholder="例: 20cm x 15cm"
           value="{{ data.get('print_size_other_custom') or '' }}">
    <label>プリントカラー(その他):</label>
    <input type="text" name="print_color_other"
           placeholder="全てのカラーをご記入ください。計xx色"
           value="{{ data.get('print_color_other') or '' }}">

    <label>フォントNo.(その他):</label>
    <input type="text" name="font_no_other"
           placeholder="例: X-XX"
           value="{{ data.get('font_no_other') or '' }}">

    <label>プリントサンプル(その他):</label>
    <input type="text" name="design_sample_other"
           placeholder="例: D-XXX"
           value="{{ data.get('design_sample_other') or '' }}">

    <label>プリント位置データ(その他):</label>
    <input type="file" name="position_data_other">


    <!-- ★★★ 背ネーム・背番号プリント（複数選択チェックボックス） ★★★ -->
    <h3>背ネーム・背番号プリント</h3>
    <p>※複数選択可能</p>
    <div class="checkbox-group">
      <label>
        <input type="checkbox" name="back_name_number_print[]" value="ネーム&背番号セット"
          {% if 'ネーム&背番号セット' in (data.get('back_name_number_print_options') or '') %}checked{% endif %}>
          ネーム&背番号セット
      </label>
      <label>
        <input type="checkbox" name="back_name_number_print[]" value="ネーム(大)"
          {% if 'ネーム(大)' in (data.get('back_name_number_print_options') or '') %}checked{% endif %}>
          ネーム(大)
      </label>
      <label>
        <input type="checkbox" name="back_name_number_print[]" value="ネーム(小)"
          {% if 'ネーム(小)' in (data.get('back_name_number_print_options') or '') %}checked{% endif %}>
          ネーム(小)
      </label>
      <label>
        <input type="checkbox" name="back_name_number_print[]" value="番号(大)"
          {% if '番号(大)' in (data.get('back_name_number_print_options') or '') %}checked{% endif %}>
          番号(大)
      </label>
      <label>
        <input type="checkbox" name="back_name_number_print[]" value="番号(小)"
          {% if '番号(小)' in (data.get('back_name_number_print_options') or '') %}checked{% endif %}>
          番号(小)
      </label>
    </div>

    <h3>追加のデザインイメージデータ</h3>
    <p class="instruction">プリント位置(前, 左胸, 右胸, 背中, 左袖, 右袖)を選択し、アップロードできます。</p>
    <label>プリント位置:</label>
    <select name="additional_design_position">
      <option value="">選択してください</option>
      <option value="前" {% if data.get('additional_design_position') == '前' %}selected{% endif %}>前</option>
      <option value="左胸" {% if data.get('additional_design_position') == '左胸' %}selected{% endif %}>左胸</option>
      <option value="右胸" {% if data.get('additional_design_position') == '右胸' %}selected{% endif %}>右胸</option>
      <option value="背中" {% if data.get('additional_design_position') == '背中' %}selected{% endif %}>背中</option>
      <option value="左袖" {% if data.get('additional_design_position') == '左袖' %}selected{% endif %}>左袖</option>
      <option value="右袖" {% if data.get('additional_design_position') == '右袖' %}selected{% endif %}>右袖</option>
    </select>
    <label>デザインイメージデータ:</label>
    <input type="file" name="additional_design_image">

    <button type="submit">送信</button>

    <!-- ▼▼ JS: 前面(1～9)・背面(10～14) クリック選択 ▼▼ -->
    <script>
      // 前面
      const frontSvgContainer = document.querySelectorAll('.tshirt-container')[0];
      const frontAreas = frontSvgContainer.querySelectorAll('.click-area');
      const frontPositionsInput = document.getElementById('front_positions_selected');

      frontAreas.forEach(area => {
        area.addEventListener('click', () => {
          // いったん全部の selected を外す
          frontAreas.forEach(a => a.classList.remove('selected'));
          // クリックしたものだけ selected
          area.classList.add('selected');
          // data-num を input に格納
          const num = area.getAttribute('data-num');
          frontPositionsInput.value = num;
        });
      });

      // 背面
      const backSvgContainer = document.querySelectorAll('.tshirt-container')[1];
      const backAreas = backSvgContainer.querySelectorAll('.click-area');
      const backPositionsInput = document.getElementById('back_positions_selected');

      backAreas.forEach(area => {
        area.addEventListener('click', () => {
          backAreas.forEach(a => a.classList.remove('selected'));
          area.classList.add('selected');
          const num = area.getAttribute('data-num');
          backPositionsInput.value = num;
        });
      });
    </script>
  </form>
</body>
</html>
"""

@app.route("/paper_order_form", methods=["GET"])
def paper_order_form():
    user_id = request.args.get("user_id", "")
    guessed_data = {}
    if user_id in user_states and "paper_form_data" in user_states[user_id]:
        guessed_data = user_states[user_id]["paper_form_data"]
    return render_template_string(PAPER_FORM_HTML, user_id=user_id, data=guessed_data)

###################################
# ▼▼ 紙の注文用フォーム送信
###################################
@app.route("/paper_order_form_submit", methods=["POST"])
def paper_order_form_submit():
    form = request.form
    files = request.files
    user_id = form.get("user_id", "")

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

    size_ss = none_if_empty_int(form.get("size_ss"))
    size_s = none_if_empty_int(form.get("size_s"))
    size_m = none_if_empty_int(form.get("size_m"))
    size_l = none_if_empty_int(form.get("size_l"))
    size_ll = none_if_empty_int(form.get("size_ll"))
    size_lll = none_if_empty_int(form.get("size_lll"))

    print_size_front = none_if_empty_str(form.get("print_size_front"))
    print_size_front_custom = none_if_empty_str(form.get("print_size_front_custom"))
    print_color_front = none_if_empty_str(form.get("print_color_front"))
    font_no_front = none_if_empty_str(form.get("font_no_front"))
    design_sample_front = none_if_empty_str(form.get("design_sample_front"))

    print_size_back = none_if_empty_str(form.get("print_size_back"))
    print_size_back_custom = none_if_empty_str(form.get("print_size_back_custom"))
    print_color_back = none_if_empty_str(form.get("print_color_back"))
    font_no_back = none_if_empty_str(form.get("font_no_back"))
    design_sample_back = none_if_empty_str(form.get("design_sample_back"))

    print_size_other = none_if_empty_str(form.get("print_size_other"))
    print_size_other_custom = none_if_empty_str(form.get("print_size_other_custom"))
    print_color_other = none_if_empty_str(form.get("print_color_other"))
    font_no_other = none_if_empty_str(form.get("font_no_other"))
    design_sample_other = none_if_empty_str(form.get("design_sample_other"))

    # 位置データ(前/後/その他)
    pos_data_front = files.get("position_data_front")
    pos_data_back = files.get("position_data_back")
    pos_data_other = files.get("position_data_other")

    front_url = upload_file_to_s3(pos_data_front, S3_BUCKET_NAME, prefix="uploads/")
    back_url = upload_file_to_s3(pos_data_back, S3_BUCKET_NAME, prefix="uploads/")
    other_url = upload_file_to_s3(pos_data_other, S3_BUCKET_NAME, prefix="uploads/")

    # 追加のデザインイメージ
    additional_design_position = none_if_empty_str(form.get("additional_design_position"))
    additional_design_image = files.get("additional_design_image")
    additional_design_image_url = upload_file_to_s3(additional_design_image, S3_BUCKET_NAME, prefix="uploads/")

    # ▼▼ 背ネーム・背番号プリント (複数選択) ▼▼
    selected_back_name_number_print = form.getlist("back_name_number_print[]")
    back_name_number_print_options = ",".join(selected_back_name_number_print) if selected_back_name_number_print else None

    # ★★★ ここで「本注文見積番号」を生成 ★★★
    import time
    order_quote_number = f"O{int(time.time())}"

    # DBに保存 (orders)
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

                print_size_front,
                print_size_front_custom,
                print_color_front,
                font_no_front,
                design_sample_front,
                position_data_front_url,

                print_size_back,
                print_size_back_custom,
                print_color_back,
                font_no_back,
                design_sample_back,
                position_data_back_url,

                print_size_other,
                print_size_other_custom,
                print_color_other,
                font_no_other,
                design_sample_other,
                position_data_other_url,

                additional_design_position,
                additional_design_image_url,

                back_name_number_print_options,

                -- ★ 新たに本注文見積番号を追加 ★
                order_quote_number,

                created_at
            ) VALUES (
                %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s,
                %s,

                %s, %s, %s, %s, %s, %s,

                %s, %s, %s, %s, %s, %s,

                %s, %s, %s, %s, %s, %s,

                %s,
                %s,

                %s,

                %s,

                NOW()
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

                print_size_front,
                print_size_front_custom,
                print_color_front,
                font_no_front,
                design_sample_front,
                front_url,

                print_size_back,
                print_size_back_custom,
                print_color_back,
                font_no_back,
                design_sample_back,
                back_url,

                print_size_other,
                print_size_other_custom,
                print_color_other,
                font_no_other,
                design_sample_other,
                other_url,

                additional_design_position,
                additional_design_image_url,

                back_name_number_print_options,

                order_quote_number,  # 本注文見積番号

            )
            cur.execute(sql, params)
            new_id = cur.fetchone()[0]
        conn.commit()
        logger.info(f"Inserted paper_order id={new_id}, order_quote_number={order_quote_number}")

    # 見積→注文へのコンバージョンを示すため、estimatesテーブル側の order_placed = true に更新
    mark_estimate_as_ordered(user_id)

    # ★★★ 注文価格計算 → LINE通知 ★★★
    (discount_type, total_price, unit_price_calc) = calculate_order_price(
        product_name,
        size_ss or 0,
        size_s or 0,
        size_m or 0,
        size_l or 0,
        size_ll or 0,
        size_lll or 0,
        application_date or "",
        use_date or "",
        print_color_front or "",
        print_color_back or "",
        print_color_other or "",
        design_sample_back or "",
        design_sample_other or "",
        back_name_number_print_options or ""
    )

    used_positions = []
    if print_color_front:
        used_positions.append("前")
    if print_color_back:
        used_positions.append("後")
    if print_color_other:
        used_positions.append("その他")

    bn_options = back_name_number_print_options or "なし"

    push_text = (
        f"ご注文ありがとうございます。(注文用紙)\n"
        f"学校名: {school_name}\n"
        f"商品名: {product_name}\n"
        f"商品カラー: {product_color}\n"
        f"プリント位置: {', '.join(used_positions) if used_positions else 'なし'}\n"
        f"背ネーム&背番号プリント: {bn_options}\n"
        f"割引種別: {discount_type}\n"
        f"合計金額: ¥{total_price:,}\n"
        f"1枚あたり: ¥{unit_price_calc:,}\n"
        f"本注文見積番号: {order_quote_number}\n"  # ★追加
        "担当者より後ほどご連絡いたします。"
    )
    try:
        line_bot_api.push_message(to=user_id, messages=TextSendMessage(text=push_text))
    except Exception as e:
        logger.error(f"Push message failed: {e}")

    return "紙の注文フォーム送信完了。LINEに通知を送りました。"

# ▼▼ 簡易見積→注文へのコンバージョンがあった場合に estimates.order_placed を true にする関数 ▼▼
def mark_estimate_as_ordered(user_id):
    """
    同一user_idで未注文のestimateがあれば order_placed=true に更新するサンプル。
    実際は見積番号単位で管理するなど状況次第。
    """
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            sql = """
            UPDATE estimates
               SET order_placed = true
             WHERE user_id = %s
               AND order_placed = false
            """
            cur.execute(sql, (user_id,))
        conn.commit()

###################################
# ▼▼ 24時間ごとにリマインドを送るデモ
###################################
@app.route("/send_reminders", methods=["GET"])
def send_reminders():
    """
    作成から30秒以上経過した (order_placed=false, reminder_count<2) の見積をリマインドする。
    デモのため30秒にしています。
    """
    logger.info("[DEBUG] /send_reminders endpoint called.")

    UTC9 = datetime.timezone(datetime.timedelta(hours=9))
    threshold = datetime.datetime.now(UTC9) - datetime.timedelta(seconds=30)
    logger.info(f"[DEBUG] threshold (30秒前) = {threshold}")

    with get_db_connection() as conn:
        with conn.cursor() as cur:
            sql = """
            SELECT id, user_id, quote_number, total_price, created_at
              FROM estimates
             WHERE order_placed = false
               AND reminder_count < 2
            """
            cur.execute(sql)
            rows = cur.fetchall()

            logger.info(f"[DEBUG] fetched {len(rows)} rows from estimates for reminder check.")

            for (est_id, user_id, quote_number, total_price, created_at) in rows:
                if created_at.tzinfo is None:
                    created_at = created_at.replace(tzinfo=UTC9)

                logger.info(
                    f"[DEBUG] Checking estimate_id={est_id}, created_at={created_at}, quote_number={quote_number}"
                )

                if created_at < threshold:
                    logger.info(
                        f"[DEBUG] estimate_id={est_id} is older than 30 seconds; sending reminder."
                    )

                    reminder_text = (
                        f"【リマインド】\n"
                        f"簡易見積（見積番号: {quote_number}）\n"
                        f"合計金額: ¥{total_price:,}\n"
                        "作成から30秒以上経過しました。ご注文はお済みでしょうか？"
                    )

                    try:
                        line_bot_api.push_message(
                            to=user_id,
                            messages=TextSendMessage(text=reminder_text)
                        )
                        line_bot_api.push_message(
                            to=user_id,
                            messages=[create_mode_selection_flex()]
                        )

                        with conn.cursor() as cur2:
                            cur2.execute(
                                "UPDATE estimates SET reminder_count = reminder_count + 1 WHERE id = %s",
                                (est_id,)
                            )
                        conn.commit()

                        logger.info(f"[DEBUG] Sent reminder and updated reminder_count for estimate_id={est_id}")
                    except Exception as e:
                        logger.error(f"Push reminder failed for user_id={user_id}, estimate_id={est_id}: {e}")
                else:
                    logger.info(
                        f"[DEBUG] estimate_id={est_id} is NOT older than 30 seconds; skipping."
                    )

    return "リマインド送信完了"

###################################
# Flask起動 (既存)
###################################
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=True)
