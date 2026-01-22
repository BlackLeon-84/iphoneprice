
import requests
from bs4 import BeautifulSoup
import sys
import os
import re

# 현재 디렉토리를 경로에 추가하여 import 가능하게 함
sys.path.append(os.getcwd())

from scraper_main import login_fixcon

# 인코딩 설정
sys.stdout.reconfigure(encoding='utf-8')

def get_manual_secrets():
    toml_path = os.path.join(os.getcwd(), ".streamlit", "secrets.toml")
    if not os.path.exists(toml_path):
        return None, None
        
    try:
        with open(toml_path, "r", encoding="utf-8") as f:
            content = f.read()
            
        # Regex로 간단 파싱 (라이브러리 없이)
        user_match = re.search(r'username\s*=\s*"([^"]+)"', content)
        pass_match = re.search(r'password\s*=\s*"([^"]+)"', content)
        
        if user_match and pass_match:
            return user_match.group(1), pass_match.group(1)
    except:
        pass
        
    return None, None

def main():
    user_id, user_pw = get_manual_secrets()
    if not user_id:
        print("[-] Secrets toml parsing failed")
        return

    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    })

    if login_fixcon(session, user_id, user_pw):
        print("[+] Login Success. Fetching categories...")
        
        # 1. 메인 페이지
        res = session.get("https://fixcon.co.kr/")
        soup = BeautifulSoup(res.text, "html.parser")
        
        print("\n--- Main Categories ---")
        seen = set()
        for a in soup.find_all("a", href=True):
             if "cate_no=" in a['href']:
                try:
                    cate_no = a['href'].split("cate_no=")[1].split("&")[0]
                    name = a.text.strip()
                    if name and cate_no not in seen:
                        seen.add(cate_no)
                        print(f"[{cate_no}] {name}")
                except:
                    pass

        # 2. 악세사리 (27) 하위
        print("\n--- Sub Categories (Accesories: 27) ---")
        res = session.get("https://fixcon.co.kr/product/list.html?cate_no=27")
        soup = BeautifulSoup(res.text, "html.parser")
        
        for a in soup.find_all("a", href=True):
             if "cate_no=" in a['href']:
                try:
                    cate_no = a['href'].split("cate_no=")[1].split("&")[0]
                    name = a.text.strip()
                    # 27이 아닌 다른 카테고리만 출력 (하위 카테고리일 가능성)
                    if name and cate_no != "27": 
                        print(f"[{cate_no}] {name}")
                except:
                    pass

if __name__ == "__main__":
    main()
