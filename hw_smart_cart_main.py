import time
import smbus2 as smbus
import os
import firebase_admin
from firebase_admin import credentials, firestore
import RPi.GPIO as GPIO
from mfrc522 import SimpleMFRC522
import threading
import subprocess
import urllib.request
import urllib.parse
import json
from pyfingerprint.pyfingerprint import PyFingerprint

# 네이버 쇼핑 API 키는 환경변수로 관리 (코드에 직접 노출하지 않음)
# 실행 전 아래 환경변수를 설정
#   export NAVER_CLIENT_ID="발급받은_client_id"
#   export NAVER_CLIENT_SECRET="발급받은_client_secret"
client_id = os.environ.get("NAVER_CLIENT_ID")
client_secret = os.environ.get("NAVER_CLIENT_SECRET")

# Firebase 서비스 계정 키 경로도 환경변수로 관리
cred = credentials.Certificate(os.environ.get("FIREBASE_CRED_PATH", "./serviceAccountKey.json"))
firebase_admin.initialize_app(cred)
db = firestore.client()

i2c_address = 0x27
lcd_columns = 16
lcd_rows = 2
BUS = smbus.SMBus(1)
BLEN = 1

productinfo_DB = [
["미래생활 잘풀리는집 더도톰한3겹25*6 1416GX1EA", 8809180741459, 4800, 0],
["휴대용 클리오센스",8801441006253 , 3500, 0],
["2800마운틴핑크솔트",8801046311486 , 2200, 0],
["컴배트 좀벌레싹 서랍장용 아로마향 24입",8809401608226 , 10500, 0],
["다우니 실내건조플로럴", 4987176116895, 8900, 0],
["아이깨끗해 거품형 250ml",8806325600961 , 5500, 0],
["스너글 포근한 섬유탈취제 허거블 코튼 470ml",8801619093719 , 6500, 0],
["홈키퍼 수성에어졸 500ml",8809004770566 , 4900, 0],
["오리온 초코파이 420G",8801117534912 , 5200, 0],
["크라운하임 (초코)",8801111115223 , 4800, 0],
["참이슬 후레쉬",8801048951017 , 1800, 1],
["크러쉬 맥주",8801030949107,2400, 1]
]

def clear_scanned_products():
    docs = db.collection("scanned_products").stream()
    for doc in docs:
        db.collection("scanned_products").document(doc.id).delete()

def clear_total_price():
    db.collection("total_price").document("price").set({"total_price": 0})

def write_word(addr, data):
    global BLEN
    temp = data
    if BLEN == 1:
        temp |= 0x08
    else:
        temp &= 0xF7
    BUS.write_byte(addr, temp)

def send_command(comm):
    buf = comm & 0xF0
    buf |= 0x04
    write_word(i2c_address, buf)
    time.sleep(0.002)
    buf &= 0xFB
    write_word(i2c_address, buf)
    buf = (comm & 0x0F) << 4
    buf |= 0x04
    write_word(i2c_address, buf)
    time.sleep(0.002)
    buf &= 0xFB
    write_word(i2c_address, buf)

def send_data(data):
    buf = data & 0xF0
    buf |= 0x05
    write_word(i2c_address, buf)
    time.sleep(0.002)
    buf &= 0xFB
    write_word(i2c_address, buf)
    buf = (data & 0x0F) << 4
    buf |= 0x05
    write_word(i2c_address, buf)
    time.sleep(0.002)
    buf &= 0xFB
    write_word(i2c_address, buf)

def init_lcd():
    try:
        send_command(0x33)
        time.sleep(0.005)
        send_command(0x32)
        time.sleep(0.005)
        send_command(0x28)
        time.sleep(0.005)
        send_command(0x0C)
        time.sleep(0.005)
        send_command(0x01)
        time.sleep(0.005)
    except Exception as e:
        print("LCD initialization failed:", e)

def display_price_and_total_on_lcd(product_price, total_price):
    send_command(0x01)
    time.sleep(0.002)
    price_message = f"Price: {product_price} KRW"
    for char in price_message:
        send_data(ord(char))
    send_command(0xC0)
    total_message = f"Total: {total_price} KRW"
    for char in total_message:
        send_data(ord(char))

def calculate_age_group(year):
    current_year = time.localtime().tm_year
    age = current_year - int(year)
    age_group = (age // 10) * 10
    return f"{age_group}"

def get_user_age_group():
    users_ref = db.collection("live_users").limit(1).stream()
    for doc in users_ref:
        user_data = doc.to_dict()
        if user_data and "year" in user_data:
            return calculate_age_group(user_data["year"])
    return "전체"

def search_naver_api_and_store(query, age_group):
    if not query.strip():
        print("검색어가 비어 있습니다. API 호출을 생략합니다.")
        return

    search_query = f"{age_group} {query}"
    encText = urllib.parse.quote(search_query)
    url = f"https://openapi.naver.com/v1/search/shop.json?query={encText}&display=5"

    request = urllib.request.Request(url)
    request.add_header("X-Naver-Client-Id", client_id)
    request.add_header("X-Naver-Client-Secret", client_secret)

    try:
        response = urllib.request.urlopen(request)
        rescode = response.getcode()

        if rescode == 200:
            response_body = response.read()
            data = json.loads(response_body.decode('utf-8'))

            if data['items']:
                print(f"\n--- 네이버 쇼핑 추천 상품 ({search_query}) ---")

                recommended_items = []

                for item in data['items']:
                    title = item['title'].replace('<b>', '').replace('</b>', '')
                    price = item['lprice']
                    print(f"상품명: {title}")
                    print(f"가격: {price}원")
                    print('-' * 30)
                    recommended_items.append({"title": title, "price": price})

                db.collection("recommend_products").document("products").set({
                    "recommended": recommended_items
                })
                print("\n추천 상품을 Firestore에 저장했습니다.\n")

            else:
                print("검색 결과가 없습니다.")

        else:
            print("API 요청 실패. Error Code:", rescode)

    except Exception as e:
        print("API 요청 중 오류가 발생했습니다:", str(e))

def clear_recommand_products():
    docs = db.collection("recommend_products").stream()
    for doc in docs:
        db.collection("recommend_products").document(doc.id).delete()
    print("Cleared all documents from 'recommend_products' collection.")


def is_minor():
    users_ref = db.collection("live_users").limit(1).stream()
    for doc in users_ref:
        user_data = doc.to_dict()
        if user_data and "year" in user_data:
            current_year = time.localtime().tm_year
            age = current_year - int(user_data["year"])
            return age < 18
    return False

def find_product_info(product_number, total_price):
    age_group = get_user_age_group()
    for product in productinfo_DB:
        if product[1] == product_number:
            if product[3] == 1 and is_minor():
                print("This product is age-restricted. Playing age restriction warning.")
                display_price_and_total_on_lcd("18+                ", total_price)
                os.system("cvlc --play-and-exit ./assets/age.mp3")
                return None, total_price

            total_price += product[2]
            display_price_and_total_on_lcd(product[2], total_price)

            db.collection("scanned_products").add({
                "product_name": product[0],
                "product_number": product[1],
                "price": product[2],
                "age_restriction": 'Yes' if product[3] else 'No'
            })

            db.collection("total_price").document("price").set({
                "total_price": total_price
            })

            print(f"API 검색어: {age_group} {product[0]}")
            search_naver_api_and_store(product[0], age_group)

            return [product[0], product[1], product[2], 'Yes' if product[3] else 'No'], total_price
    return None, total_price

reader = SimpleMFRC522()

# 결제 완료 확인용 RFID 태그 ID 
PAYMENT_TAG_ID = 0

def monitor_rfid_tag():
    while True:
        try:
            tag_id, _ = reader.read()
            if tag_id == PAYMENT_TAG_ID:
                done_ref = db.collection("Done").document("PayDone").get()
                if done_ref.exists:
                    status = done_ref.to_dict().get("status", False)
                    if status == 1:
                        subprocess.Popen(["cvlc", "--play-and-exit", "./assets/ok.mp3"],
                                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        send_command(0x01)
                    elif status == 0:
                        subprocess.Popen(["cvlc", "--play-and-exit", "./assets/not_ok.mp3"],
                                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as e:
            print("Error reading RFID tag:", e)
        time.sleep(5)

rfid_thread = threading.Thread(target=monitor_rfid_tag, daemon=True)
rfid_thread.start()

clear_scanned_products()
clear_total_price()
clear_recommand_products()
init_lcd()
total_price = 0

db.collection("ID_check").document("status").set({"status": False})

# 사용자 등록 정보 
ID_info_DB = [
    ["user1", 2007, 1],
    ["user2", 1988, 2],
    ["user3", 1980, 3],
    ["user4", 1972, 4],
    ["user5", 2002, 5]
]

try:
    f = PyFingerprint('/dev/serial0', 57600, 0xFFFFFFFF, 0x00000000)
    if not f.verifyPassword():
        raise ValueError("Password is incorrect!")
    print("Found fingerprint sensor!")
except Exception as e:
    print("Error initializing sensor:", str(e))
    exit(1)

GPIO.setmode(GPIO.BOARD)
LED = 11
GPIO.setup(LED, GPIO.OUT, initial=GPIO.LOW)

stop_checking = threading.Event()

def get_fingerprint_id():
    try:
        print("Waiting for a valid finger...")
        while f.readImage() == False:
            pass
        print("Image taken")

        if f.convertImage(0x01) != True:
            print("Failed to convert image")
            return None

        result = f.searchTemplate()
        position = result[0]
        accuracy_score = result[1]

        if position == -1:
            print("Did not find a match")
            return None
        else:
            print(f"Found a match! ID: {position}, Confidence: {accuracy_score}")
            return position

    except Exception as e:
        print("Error:", str(e))
        return None

def check_user_in_firestore():
    global stop_checking
    users_ref = db.collection("live_users").stream()
    for doc in users_ref:
        data = doc.to_dict()
        name = data.get("name")
        year = data.get("year")

        matching_id = None
        for info in ID_info_DB:
            if info[0] == name and info[1] == int(year):
                matching_id = info[2]
                break

        if matching_id is not None:
            while not stop_checking.is_set():
                fingerprint_id = get_fingerprint_id()
                if fingerprint_id == matching_id:
                    print(f"User {name} with ID {matching_id} is authenticated.")
                    db.collection("ID_check").document("status").set({"status": True})
                    stop_checking.set()
                    return True
                else:
                    print(f"User {name} found in DB, but fingerprint ID does not match. Retrying...")
                    db.collection("ID_check").document("status").set({"status": False})
            return False

    print("No matching user found in Firestore.")
    db.collection("ID_check").document("status").set({"status": False})
    return False

def blink_led_until_status_true():
    while not stop_checking.is_set():
        status_ref = db.collection("ID_check").document("status").get()
        if status_ref.exists and status_ref.to_dict().get("status", False) == True:
            GPIO.output(LED, GPIO.LOW)
            break
        GPIO.output(LED, GPIO.HIGH)
        time.sleep(0.5)
        GPIO.output(LED, GPIO.LOW)
        time.sleep(0.5)

def main_loop():
    while not stop_checking.is_set():
        check_user_in_firestore()
        time.sleep(1)

try:
    led_thread = threading.Thread(target=blink_led_until_status_true)
    led_thread.start()

    main_loop()

except KeyboardInterrupt:
    stop_checking.set()
finally:
    GPIO.cleanup()

while True:
    try:
        product_number = int(input("Enter the product number (or enter 0 to exit): "))
        if product_number == 0:
            print("Exiting the program.")
            send_command(0x01)
            break
        result, total_price = find_product_info(product_number, total_price)
        if result:
            print(f"Product Name: {result[0]}")
            print(f"Product Number: {result[1]}")
            print(f"Price: {result[2]} KRW")
            print(f"Age Restriction: {result[3]}")
    except ValueError:
        print("Invalid input. Please enter a valid product number.")
