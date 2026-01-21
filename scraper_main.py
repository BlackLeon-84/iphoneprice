
import requests
from bs4 import BeautifulSoup
import json
import os
import sys
import datetime
import gspread
from google.oauth2.service_account import Credentials
import time

# [설정] Windows 콘솔 한글 출력
sys.stdout.reconfigure(encoding='utf-8')

# [설정] 파일 경로
BASE_DIR = os.path.dirname(__file__)
SECRETS_PATH = os.path.join(BASE_DIR, "secrets.json")
SERVICE_ACCOUNT_PATH = os.path.join(BASE_DIR, "service_account.json")

# [설정] 구글 시트 키
SPREADSHEET_KEY = "1VfAiPUL--QsX7GatPESVzz80xG0BQ7Obj_mywUhJVcM"

# [설정] 타겟 카테고리
# 24: iPhone, 25: iPad, 26: Watch, 386: AirPods/Pencil, 27: Acc, 28: Tools
TARGET_CATEGORIES = {
    "iPhone": "24",
    # "iPad": "25",
    # "Watch": "26",
    # "AirPods_Pencil": "386",
}

def load_secrets():
    # 1. 파일이 있으면 파일 사용 (로컬)
    if os.path.exists(SECRETS_PATH):
        with open(SECRETS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
            
    # 2. 파일이 없으면 환경변수 확인 (클라우드)
    # Streamlit의 경우, subprocess로 넘겨준 환경변수를 사용
    username = os.environ.get("FIXCON_ID")
    password = os.environ.get("FIXCON_PW")
    
    if username and password:
         return {"FIXCON_ID": username, "FIXCON_PW": password}
         
    return None

def get_gsheet_client():
    scopes = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
    
    # 1. 환경변수 확인 (클라우드/서브프로세스)
    if os.environ.get("GCP_SERVICE_ACCOUNT"):
        info = json.loads(os.environ.get("GCP_SERVICE_ACCOUNT"))
        creds = Credentials.from_service_account_info(info, scopes=scopes)
        return gspread.authorize(creds)

    # 2. 파일 확인 (로컬)
    if os.path.exists(SERVICE_ACCOUNT_PATH):
        creds = Credentials.from_service_account_file(SERVICE_ACCOUNT_PATH, scopes=scopes)
        return gspread.authorize(creds)
        
    return None

def login_fixcon(session, user_id, user_pw):
    login_url = "https://fixcon.co.kr/member/login.html"
    print(f"[*] 로그인 페이지 접속...")
    res = session.get(login_url)
    res.encoding = res.apparent_encoding
    
    soup = BeautifulSoup(res.text, "html.parser")
    login_form = soup.find("form", {"id": "member_form_0"})
    if not login_form:
        input_el = soup.find("input", {"name": "member_id"})
        if input_el: login_form = input_el.find_parent("form")
            
    if not login_form:
        print("[-] 로그인 폼을 찾을 수 없음")
        return False

    action_url = login_form.get("action")
    if not action_url.startswith("http"):
        action_url = f"https://fixcon.co.kr{action_url}"
    
    login_data = {}
    for inp in login_form.find_all("input"):
        if inp.get("name"):
            login_data[inp.get("name")] = inp.get("value", "")
            
    login_data["member_id"] = user_id
    login_data["member_passwd"] = user_pw
    login_data["use_login_keeping"] = "F"
    
    headers = {
        "Referer": login_url,
        "Origin": "https://fixcon.co.kr",
        "Content-Type": "application/x-www-form-urlencoded",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    
    res = session.post(action_url, data=login_data, headers=headers, timeout=10)
    
    # [Fix] POST 후 바로 리다이렉트가 안 될 수 있으므로, 마이페이지 강제 접속
    mypage_url = "https://fixcon.co.kr/myshop/index.html"
    print(f"[*] 마이페이지 접속 시도: {mypage_url}")
    res = session.get(mypage_url, timeout=10)
    res.encoding = res.apparent_encoding
    
    # 성공 확인
    if "myshop/index.html" in res.url or ("로그인" not in res.text and "modify.html" in res.text):
        print("[+] 로그인 성공!")
        return True
    else:
        print(f"[-] 로그인 실패. URL: {res.url}")
        print(f"[-] 응답 텍스트(일부): {res.text[:500]}")
        return False

def clean_text(text):
    if not text: return ""
    return text.strip().replace("\n", "").replace("\r", "")

def scrape_category(session, cat_name, cat_id):
    products = []
    page = 1
    
    while True:
        url = f"https://fixcon.co.kr/product/list.html?cate_no={cat_id}&page={page}"
        print(f"[*] 수집 중: {cat_name} (ID: {cat_id}) - {page}페이지")
        
        res = session.get(url)
        res.encoding = res.apparent_encoding
        soup = BeautifulSoup(res.text, "html.parser")
        
        # Cafe24 상품 리스트 선택자
        items = soup.select("ul.prdList > li") or soup.select(".xans-product-listnormal > li")
        
        if not items:
            # xans-record- 클래스로 재시도
            items = soup.select("li.xans-record-")
            
        if not items:
            print(f"    - 더 이상 상품이 없습니다. (총 {len(products)}개 수집 완료)")
            break
            
        print(f"    - {len(items)}개 상품 발견 (현재 페이지)")
        
        for item in items:
            # 1. 이름
            name_el = item.select_one(".name a") or item.select_one(".pname")
            if name_el:
                name = name_el.text.replace("상품명 :", "").strip()
            else:
                continue # 이름 없으면 스킵

            # 2. 가격
            price = "Unknown"
            
            # [Fix] scraper_requests.py에서 검증된 텍스트 분석 로직만 사용
            # (.price 클래스 등은 비어있거나 부정확할 수 있음)
            desc_el = item.select_one(".description")
            if desc_el:
                lines = desc_el.get_text(separator="\n").split("\n")
                for line in lines:
                    val = line.strip()
                    # '원'으로 끝나고 숫자가 포함된 경우 (예: 42,000원)
                    if val.endswith("원") and any(c.isdigit() for c in val):
                        price = val
                        break
            
            # print(f"    - 상품: {name} / 가격: {price}") # 디버그 출력 (너무 많아서 주석)
            
            # 품절 여부 (아이콘 등 확인)
            status = "판매중"
            if item.select("img[alt='품절']"):
                status = "품절"
                
            # [New] 이미지 스크래핑
            img_url = ""
            img_el = item.select_one(".thumbnail img")
            if img_el:
                img_url = img_el.get("src")
                if img_url:
                    if img_url.startswith("//"):
                        img_url = f"https:{img_url}"
                    elif img_url.startswith("/"):
                        img_url = f"https://fixcon.co.kr{img_url}"

            products.append({
                "category": cat_name,
                "name": name,
                "price": price,
                "status": status,
                "url": f"https://fixcon.co.kr{name_el['href']}" if name_el else "",
                "img_url": img_url # [New] 이미지 URL 추가
            })
            
        page += 1
        time.sleep(0.5) # 페이지 간 딜레이
        
        # 안전장치: 최대 30페이지까지만
        if page > 30:
            print("[-] 최대 페이지 도달")
            break
            
    return products

def main():
    # 1. 설정 로드
    secrets = load_secrets()
    if not secrets.get("FIXCON_ID") or not secrets.get("FIXCON_PW"):
        print("[Fatal] secrets.json에 아이디/비번이 없습니다.")
        sys.exit(1)

    # 2. 세션 시작 및 로그인
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    })
    
    if not login_fixcon(session, secrets["FIXCON_ID"], secrets["FIXCON_PW"]):
        sys.exit(1)

    # 3. 데이터 수집
    all_data = []
    # [Fix] KST Timezone check
    kst = datetime.timezone(datetime.timedelta(hours=9))
    timestamp = datetime.datetime.now(kst).strftime("%Y-%m-%d %H:%M:%S")
    
    for cat_name, cat_id in TARGET_CATEGORIES.items():
        items = scrape_category(session, cat_name, cat_id)
        for item in items:
            item["timestamp"] = timestamp
            all_data.append(item)
        time.sleep(1) # 부하 방지
        
    print(f"[*] 총 {len(all_data)}개 데이터 수집 완료")

    # 4. 구글 시트 저장
    try:
        print("[*] 구글 시트에 저장 중...")
        gc = get_gsheet_client()
        sh = gc.open_by_key(SPREADSHEET_KEY)
        ws = sh.sheet1
        
        # [Fix] 헤더 강제 업데이트 (컬럼 추가 반영)
        header = ["수집일시", "카테고리", "상품명", "가격", "상태", "URL", "이미지"]
        ws.update([header], "A1:G1")
            
        # 데이터 변환 (Dict -> List)
        rows_to_add = []
        for d in all_data:
            rows_to_add.append([
                d["timestamp"],
                d["category"],
                d["name"],
                d["price"],
                d["status"],
                d["url"],
                d.get("img_url", "") # [New] 이미지 URL 저장
            ])
            
        if rows_to_add:
            ws.append_rows(rows_to_add)
            print(f"[+] {len(rows_to_add)}개 행 추가 완료!")
        else:
            print("[-] 추가할 데이터가 없습니다.")
            
    except Exception as e:
        print(f"[-] 구글 시트 저장 실패: {e}")

if __name__ == "__main__":
    main()
