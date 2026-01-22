
import requests
from bs4 import BeautifulSoup
import json
import os
import sys

# 인코딩 설정
sys.stdout.reconfigure(encoding='utf-8')

# 설정 파일 로드
BASE_DIR = os.getcwd() # 현재 작업 디렉토리 기준
SECRETS_PATH = os.path.join(BASE_DIR, ".streamlit", "secrets.toml")

def get_credentials():
    # .streamlit/secrets.toml 파싱 (간이)
    try:
        with open(SECRETS_PATH, "r", encoding="utf-8") as f:
            content = f.read()
            import toml
            data = toml.loads(content)
            return data["monitor_login"]["username"], data["monitor_login"]["password"]
    except Exception as e:
        print(f"Error loading secrets: {e}")
        return None, None

def login_fixcon(session, user_id, user_pw):
    login_url = "https://fixcon.co.kr/member/login.html"
    print(f"[*] 로그인 시도: {user_id}")
    res = session.get(login_url)
    soup = BeautifulSoup(res.text, "html.parser")
    
    login_form = soup.find("form", {"id": "member_form_0"})
    if not login_form:
        # cafe24 기본 폼 찾기 시도
        input_el = soup.find("input", {"name": "member_id"})
        if input_el: login_form = input_el.find_parent("form")

    if not login_form:
        print("[-] 로그인 폼을 찾을 수 없습니다.")
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
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": login_url,
        "Origin": "https://fixcon.co.kr",
        "Content-Type": "application/x-www-form-urlencoded"
    }
    
    res = session.post(action_url, data=login_data, headers=headers)
    
    # 마이페이지로 확인
    res = session.get("https://fixcon.co.kr/myshop/index.html")
    if "modify.html" in res.text or "로그아웃" in res.text:
        print("[+] 로그인 성공")
        return True
    else:
        print("[-] 로그인 실패")
        return False

def main():
    user_id, user_pw = get_credentials()
    if not user_id: return
    
    session = requests.Session()
    if not login_fixcon(session, user_id, user_pw):
        return
        
    # 메인 페이지에서 카테고리 추출
    print("[*] 카테고리 찾는 중...")
    res = session.get("https://fixcon.co.kr")
    soup = BeautifulSoup(res.text, "html.parser")
    
    # 모든 링크 중 cate_no가 있는 것 찾기
    links = soup.find_all("a", href=True)
    categories = {}
    
    for a in links:
        href = a['href']
        if "cate_no=" in href:
            try:
                cate_id = href.split("cate_no=")[1].split("&")[0]
                name = a.text.strip()
                if name:
                    categories[cate_id] = name
                    print(f"I found category: [{cate_id}] {name}")
            except:
                pass

if __name__ == "__main__":
    main()
