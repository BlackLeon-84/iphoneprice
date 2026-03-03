
import streamlit as st
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials
import os
import sys
import time
import subprocess
from datetime import datetime, timezone, timedelta

# --- 설정 ---
st.set_page_config(page_title="픽스콘 단가표 모니터", layout="wide")

BASE_DIR = os.path.dirname(__file__)
SERVICE_ACCOUNT_PATH = os.path.join(BASE_DIR, "service_account.json")
SPREADSHEET_KEY = "1VfAiPUL--QsX7GatPESVzz80xG0BQ7Obj_mywUhJVcM"

# --- 함수 ---
@st.cache_resource
def get_gsheet_client():
    scopes = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
    
    # [Deployment] Streamlit Cloud Secrets 우선 확인
    if "gcp_service_account" in st.secrets:
        creds = Credentials.from_service_account_info(
            st.secrets["gcp_service_account"], 
            scopes=scopes
        )
    # [Local] 로컬 파일 확인
    elif os.path.exists(SERVICE_ACCOUNT_PATH):
        creds = Credentials.from_service_account_file(SERVICE_ACCOUNT_PATH, scopes=scopes)
    else:
        st.error("Google Cloud 자격 증명을 찾을 수 없습니다. (secrets/service_account.json)")
        return None
        
    return gspread.authorize(creds)

@st.cache_data(ttl=3600)  # 1시간 캐시 (버튼 클릭할 때마다 API 호출 방지)
def load_data():
    gc = get_gsheet_client()
    try:
        sh = gc.open_by_key(SPREADSHEET_KEY)
        ws = sh.sheet1
        data = ws.get_all_records()
        df = pd.DataFrame(data)
        return df
    except Exception as e:
        st.error(f"데이터 로드 실패: {e}")
        return pd.DataFrame()

def run_scraper_script():
    script_path = os.path.join(BASE_DIR, "scraper_main.py")
    
    # [Env] 환경변수 준비 (subprocess에 전달)
    env = os.environ.copy()
    
    # 1. 픽스콘 계정 정보 전달
    # 로컬: secrets.json (load_secrets가 처리하지만, 명시적으로 넘겨도 무방)
    # 클라우드: st.secrets에서 가져와서 환경변수에 주입
    if "monitor_login" in st.secrets:
        env["FIXCON_ID"] = st.secrets["monitor_login"]["username"]
        env["FIXCON_PW"] = st.secrets["monitor_login"]["password"]
    
    # 2. 구글 서비스 계정 전달
    if "gcp_service_account" in st.secrets:
        # dict -> json string 변환하여 전달
        import json
        env["GCP_SERVICE_ACCOUNT"] = json.dumps(dict(st.secrets["gcp_service_account"]))

    try:
        # 서브프로세스로 스크립트 실행 (sys.executable 사용 필수)
        # env 파라미터로 위에서 설정한 환경변수 전달
        result = subprocess.run(
            [sys.executable, script_path], 
            capture_output=True, 
            text=True, 
            encoding='utf-8',
            check=True,
            env=env,
            timeout=180 # [Fix] 3분 타임아웃 추가
        )
        return True, result.stdout
    except subprocess.TimeoutExpired:
        return False, "시간 초과: 데이터 수집이 3분 이상 걸려 중단되었습니다."
    except subprocess.CalledProcessError as e:
        return False, e.stdout + "\n" + e.stderr
    except Exception as e:
        return False, str(e)

# --- UI ---
st.title("📱 픽스콘 단가표 모니터")

# 1. 사이드바: 업데이트 버튼 & 앱 설치 가이드
with st.sidebar:
    st.header("데이터 관리")
    
    # [설치 가이드] 홈 화면 추가 기능
    with st.expander("📱 홈 화면에 앱 추가하기"):
        st.markdown("""
        **아이폰 (Safari)**
        1. 하단공유 버튼 <img src="https://symbols.getvecta.com/stencil_82/46_share-apple.8a6e00ea01.svg" width="15"> 클릭
        2. '홈 화면에 추가' 선택
        
        **갤럭시 (Chrome)**
        1. 상단 메뉴 (⋮) 클릭
        2. '홈 화면에 추가' 또는 '앱 설치' 선택
        """, unsafe_allow_html=True)
    
    st.divider()

    if st.button("🔄 가격 정보 업데이트 (크롤링)", use_container_width=True):
        st.toast("백그라운드에서 최신 단가표를 수집 중입니다. 화면 멈춤 없이 앱을 계속 이용하실 수 있습니다!", icon="⏳")
        import threading
        import subprocess
        import json
        
        def bg_scraper_manual(env_dict):
            try:
                script_path = os.path.join(BASE_DIR, "scraper_main.py")
                subprocess.run([sys.executable, script_path], capture_output=True, text=True, encoding='utf-8', check=True, env=env_dict, timeout=180)
                # 업데이트 성공 시 캐시 초기화
                load_data.clear()
            except Exception as e:
                print("Manual background update failed:", e)

        env = os.environ.copy()
        if "monitor_login" in st.secrets:
            env["FIXCON_ID"] = st.secrets["monitor_login"]["username"]
            env["FIXCON_PW"] = st.secrets["monitor_login"]["password"]
        if "gcp_service_account" in st.secrets:
            env["GCP_SERVICE_ACCOUNT"] = json.dumps(dict(st.secrets["gcp_service_account"]))
        
        t = threading.Thread(target=bg_scraper_manual, args=(env,))
        t.daemon = True
        t.start()
    
    # st.info("데이터는 'Fixcon_DB' 구글 시트에 저장됩니다.")
    # st.markdown(f"[구글 시트 바로가기](https://docs.google.com/spreadsheets/d/{SPREADSHEET_KEY})")

# 2. 데이터 로드 및 전처리
df = load_data()

# [Fix] 데이터 로드 후 즉시 날짜 변환 (전역 적용)
if not df.empty and "수집일시" in df.columns:
    # 1. 날짜 변환 (오류 발생 시 NaT 처리)
    df["수집일시"] = pd.to_datetime(df["수집일시"], errors='coerce')
    # 2. 날짜가 비어있는 행 제거 (유효한 데이터만 남김)
    df = df.dropna(subset=["수집일시"])

    try:
        last_update = df["수집일시"].max()
        kst = timezone(timedelta(hours=9))
        now_kst = datetime.now(kst)
        
        # [Fix] 오전 10시 기준 업데이트 (10시간 차감하여 하루의 기준을 오전 10시로 변경)
        business_date = (now_kst - timedelta(hours=10)).date()
        
        try:
            last_update_tz_naive = last_update.tz_localize(None)
        except:
            last_update_tz_naive = last_update
            
        last_update_business_date = (last_update_tz_naive - timedelta(hours=10)).date()
        
        # 마지막으로 수집된 시간이 "어제 오전 10시 ~ 오늘 오전 10시 이전"이고, 현재 "오늘 오전 10시"가 지났다면
        if last_update_business_date < business_date:
            # Session State를 이용해 무한 루프 방지 (한 번만 실행)
            if "auto_updated" not in st.session_state:
                st.session_state["auto_updated"] = True
                
                # [Fix] 백그라운드 업데이트 (UI 블로킹 방지)
                st.toast("오전 10시가 지나 백그라운드에서 최신 단가표를 수집 중입니다. (기존 데이터 조회는 계속 가능합니다)", icon="⏳")
                
                import threading
                import subprocess
                import json
                
                def bg_scraper(env_dict):
                    try:
                        script_path = os.path.join(BASE_DIR, "scraper_main.py")
                        subprocess.run([sys.executable, script_path], capture_output=True, text=True, encoding='utf-8', check=True, env=env_dict, timeout=180)
                        # 업데이트 성공 시 캐시 초기화 (다음 클릭이나 탭 이동시 새 데이터가 보이도록)
                        load_data.clear()
                    except Exception as e:
                        print("Background update failed:", e)

                env = os.environ.copy()
                if "monitor_login" in st.secrets:
                    env["FIXCON_ID"] = st.secrets["monitor_login"]["username"]
                    env["FIXCON_PW"] = st.secrets["monitor_login"]["password"]
                if "gcp_service_account" in st.secrets:
                    env["GCP_SERVICE_ACCOUNT"] = json.dumps(dict(st.secrets["gcp_service_account"]))
                
                t = threading.Thread(target=bg_scraper, args=(env,))
                t.daemon = True
                t.start()
                
    except Exception as e:
        pass # 날짜 파싱 오류 등 무시

if not df.empty:
    # 정렬 및 메타데이터 표시
    df = df.sort_values(by="수집일시", ascending=False)
    latest_date = df["수집일시"].dt.strftime("%Y-%m-%d %H:%M").iloc[0]
    st.caption(f"최종 업데이트: {latest_date} (KST)")

    # [Scope Change] iPhone 데이터 및 악세사리 표시
    if "카테고리" in df.columns:
        # iPhone 또는 Acc_로 시작하는 카테고리만 포함
        df = df[ (df["카테고리"] == "iPhone") | (df["카테고리"].str.startswith("Acc_")) ]

    # 탭 구성: 검색 / 변동 내역 / 전체 목록
    tab1, tab3, tab2 = st.tabs(["🔍 부품 검색", "📉 변동 내역", "📋 전체 목록"])
    
    # [Cache] 모델 매핑 및 시리즈 분류 로직 캐싱 (속도 개선)
    @st.cache_data(show_spinner=False)
    def get_processed_data(df):
        if df.empty:
            return df, {}, []
            
        # 순서 중요: 긴 이름부터 매칭해야 함 (예: 17 Pro Max -> 17 Pro보다 먼저)
        MODEL_MAPPING = [
            ("17Pro-Max", "iPhone 17 Pro Max"), ("17Pro", "iPhone 17 Pro"), ("17AIR", "iPhone 17 Air"), ("17", "iPhone 17"),
            ("16Pro-Max", "iPhone 16 Pro Max"), ("16Pro", "iPhone 16 Pro"), ("16+", "iPhone 16 Plus"), ("16E", "iPhone 16E"), ("16", "iPhone 16"),
            ("15Pro-Max", "iPhone 15 Pro Max"), ("15Pro", "iPhone 15 Pro"), ("15+", "iPhone 15 Plus"), ("15", "iPhone 15"),
            ("14Pro-Max", "iPhone 14 Pro Max"), ("14Pro", "iPhone 14 Pro"), ("14+", "iPhone 14 Plus"), ("14", "iPhone 14"),
            ("13Pro-Max", "iPhone 13 Pro Max"), ("13Pro", "iPhone 13 Pro"), ("13Mini", "iPhone 13 Mini"), ("13", "iPhone 13"),
            ("12Pro-Max", "iPhone 12 Pro Max"), ("12Pro", "iPhone 12 Pro"), ("12Mini", "iPhone 12 Mini"), ("12", "iPhone 12"),
            ("11Pro-Max", "iPhone 11 Pro Max"), ("11Pro", "iPhone 11 Pro"), ("11", "iPhone 11"),
            ("XSMax", "iPhone XS Max"), ("XS Max", "iPhone XS Max"), ("XS-Max", "iPhone XS Max"), ("XS", "iPhone XS"), ("XR", "iPhone XR"), ("X", "iPhone X"),
            ("SE", "iPhone SE"), ("8+", "iPhone 8 Plus"), ("8", "iPhone 8"),
            ("7+", "iPhone 7 Plus"), ("7", "iPhone 7"), ("6S+", "iPhone 6S Plus"), ("6S", "iPhone 6S"), ("6+", "iPhone 6 Plus"), ("6", "iPhone 6")
        ]

        def extract_model_precise(row):
            # 1. 악세사리 처리
            cat = row["카테고리"]
            if str(cat).startswith("Acc_"):
                return "악세사리"

            # 2. 아이폰 모델 파싱
            name = row["상품명"]
            for pattern, display_name in MODEL_MAPPING:
                if pattern.lower() in name.lower():
                    return display_name
            return "기타"

        # [Changed] apply시 axis=1 사용 (카테고리 정보 접근 위해)
        df["모델"] = df.apply(extract_model_precise, axis=1)
        
        # [Optimization] 부품명 파싱도 미리 수행 (캐싱)
        def extract_part(row):
            name = row["상품명"]
            cat = row["카테고리"]
            model_name = row["모델"]

            # [New] 악세사리 부품 상세 분류
            if str(cat).startswith("Acc_") or "악세" in str(cat) or model_name == "악세사리":
                # 1. 필름류 (필름, 카메라링, 카메라필름)
                # 1. 필름류 (필름, 카메라링, 카메라필름)
                if any(x in name for x in ["필름", "카메라링", "카메라 링", "강화유리", "카메라 렌즈 보호링"]): return "필름"
                # 2. 케이스류
                if "케이스" in name: return "케이스"
                # 3. 충전기류 (케이블, 어댑터 통합)
                if any(x in name for x in ["케이블", "어댑터", "어덥터", "충전기", "젠더"]): return "충전기"
                # 4. 기타
                return "기타"

            # [User Request] 제외 필터 (하우징, 일반형 등)
            if "하우징" in name: return None
            if "(베젤형)" in name: return None
            if "(일반형)" in name: return None
            if "(고급형)" in name: return None
            if "13Pro 골드" in name: return None

            # [User Request] iPhone 7+, 8+ 액정 예외 처리 ((정), (재), (카))
            if model_name in ["iPhone 7 Plus", "iPhone 8 Plus"]:
                if any(x in name for x in ["(정)", "(재)", "(카)"]):
                    return "액정"
            
            # 명시적 카테고리 (케이블은 기타로 통합되므로 제거)
            if "액정" in name: return "액정"
            if "배터리" in name: return "배터리"
            if "카메라" in name: return "카메라"
            if "유리" in name: return "후면유리"
            if "보드" in name: return "메인보드"
            
            return "기타"

        # [Changed] apply시 axis=1 사용
        df["부품"] = df.apply(extract_part, axis=1)
        # [Filter] None 제거
        df = df.dropna(subset=["부품"])

        # 시리즈 매핑
        unique_models = df["모델"].unique().tolist()
        series_map = {}
        for m in unique_models:
            grp = "기타"
            if m == "악세사리": grp = "악세사리"
            elif "17" in m: grp = "iPhone 17 Series"
            elif "16" in m: grp = "iPhone 16 Series"
            elif "15" in m: grp = "iPhone 15 Series"
            elif "14" in m: grp = "iPhone 14 Series"
            elif "13" in m: grp = "iPhone 13 Series"
            elif "12" in m: grp = "iPhone 12 Series"
            elif "11" in m: grp = "iPhone 11 Series"
            elif any(x in m for x in ["X", "XS", "XR"]): grp = "iPhone X/XS/XR Series"
            elif any(x in m for x in ["SE", "8", "7", "6"]): grp = "iPhone SE/8/7/6 Series"
            series_map[m] = grp
            
        return df, series_map

    with tab1:
        # [Mobile UI] 버튼식 네비게이션 (One-hand usage)
        st.subheader("🛠️ 빠른 부품 검색")
        
        if not df.empty:
            # [Optimization] 데이터 전처리 캐싱 사용
            df, series_map = get_processed_data(df)
            
            # 순서 보장을 위한 리스트 정의 (최신순)
            SERIES_ORDER = ["iPhone 17 Series", "iPhone 16 Series", "iPhone 15 Series", "iPhone 14 Series", "iPhone 13 Series", "iPhone 12 Series", "iPhone 11 Series", "iPhone X/XS/XR Series", "iPhone SE/8/7/6 Series", "악세사리"]
            
            # Session State 초기화
            if "selected_model" not in st.session_state:
                st.session_state.selected_model = None
            if "selected_part" not in st.session_state:
                st.session_state.selected_part = None

            # [UI State 1] 모델이 선택되지 않았을 때 -> 전체 리스트 표시
            if st.session_state.selected_model is None:
                st.info("📱 수리할 기종을 선택해주세요.")
                
                # [Compact Layout] 화면을 3분할하여 시리즈를 배치 (가로폭 줄임 + 세로 길이 단축)
                d_col1, d_col2, d_col3 = st.columns(3)
                
                # 시리즈 분배 (최신 -> 구형 순서대로 3열 배치)
                # Col 1: 17, 16, 15
                # Col 2: 14, 13, 12
                # Col 3: 11, X/XS/XR, SE/8/7/6
                
                dashboard_cols = [d_col1, d_col2, d_col3]
                
                for idx, series in enumerate(SERIES_ORDER):
                    # 배치할 컬럼 선택 (0, 1, 2 순환 혹은 지정)
                    # 수동 지정이 더 깔끔할 수 있음
                    target_col = None
                    if idx < 3: target_col = d_col1       # 17, 16, 15
                    elif idx < 6: target_col = d_col2     # 14, 13, 12
                    else: target_col = d_col3             # 11, X..., Old
                    
                    with target_col:
                        # 해당 시리즈에 속한 모델 찾기
                        current_models = [m for m, s in series_map.items() if s == series]
                        if not current_models: continue
                        
                        # [User Request] 모델 정렬 순서 정의 (기본 -> 에어/플러스/미니 -> 프로 -> 맥스)
                        def model_sort_key(m):
                            m_lower = m.lower()

                            # [Exceptions] X Series (X -> XS -> XS Max -> XR)
                            if "iphone x" in m_lower or "xs" in m_lower or "xr" in m_lower:
                                if "xr" in m_lower: return 14
                                if "xs max" in m_lower: return 13
                                if "xs" in m_lower: return 12
                                return 11 # X

                            # [Exceptions] Old Series (SE -> 6 -> 6+ -> 6S -> 6S+ -> 7 -> 7+ -> 8 -> 8+)
                            if "iphone se" in m_lower: return 20
                            if "iphone 6" in m_lower:
                                if "6s" in m_lower: return 24 if "plus" in m_lower else 23
                                return 22 if "plus" in m_lower else 21
                            if "iphone 7" in m_lower: return 26 if "plus" in m_lower else 25
                            if "iphone 8" in m_lower: return 28 if "plus" in m_lower else 27

                            # 0순위: 16E (가장 오른쪽)
                            if "16e" in m_lower: return 5
                            # 1순위: Pro Max (가장 뒤)
                            if "pro max" in m_lower: return 4
                            # 2순위: Pro
                            if "pro" in m_lower: return 3
                            # 3순위: Plus / Mini / Air
                            if any(x in m_lower for x in ["plus", "+", "mini", "air"]): return 2
                            # 4순위: 기본형 (가장 앞)
                            return 1

                        # 모델명 정렬 (점수 오름차순)
                        current_models = sorted(current_models, key=model_sort_key)

                        # [UI Update] 각 시리즈를 박스로 감싸서 경계선 추가 (가독성 향상)
                        with st.container(border=True):
                            st.markdown(f"#### {series}")
                            
                            # [UI Update] 버튼 그리드 대신 Pills 사용 (모바일 자동 줄바꿈 & 4개 배치 효과)
                            # 라벨 생성: "iPhone 16 Pro" -> "16Pro"
                            short_label_map = {}
                            short_options = []
                            for m in current_models:
                                # 공백 제거하여 "16Pro" 형식으로 만듦
                                s_label = m.replace("iPhone ", "").replace(" ", "")
                                short_label_map[s_label] = m
                                short_options.append(s_label)
                            
                            # 현재 선택된 모델이 이 시리즈에 포함되는지 확인
                            default_sel = None
                            if st.session_state.selected_model in current_models:
                                default_sel = st.session_state.selected_model.replace("iPhone ", "").replace(" ", "")

                            # 중요: Key에 selected_model을 포함시켜서, 다른 모델 선택 시 컴포넌트를 강제 리셋(재생성)함
                            # 이렇게 해야 다른 시리즈의 하이라이트가 꺼짐.
                            selection = st.pills(
                                "Models", 
                                short_options, 
                                selection_mode="single", 
                                default=default_sel,
                                label_visibility="collapsed",
                                key=f"pills_{series}_{st.session_state.selected_model}"
                            )
                            
                            # 선택 이벤트 처리
                            if selection and (st.session_state.selected_model != short_label_map[selection]):
                                new_model = short_label_map[selection]
                                st.session_state.selected_model = new_model
                                
                                # [Fix] 악세사리(Apple)는 '액정'이 없으므로 '구성품'을 기본값으로 설정
                                if new_model == "악세사리":
                                    st.session_state.selected_part = "필름"
                                else:
                                    st.session_state.selected_part = "액정"
                                    
                                st.rerun()
                    
            # [UI State 2] 모델이 선택되었을 때 -> 부품 선택 및 결과 화면
            else:
                selected_model = st.session_state.selected_model
                
                # 상단 헤더
                # 상단 헤더
                c_back, c_title = st.columns([1, 5])
                with c_back:
                    # [Style] 뒤로가기 버튼 파란색 커스텀 (Primary 버튼 타겟팅)
                    st.markdown("""
                    <style>
                    /* Primary 버튼 스타일 강제 오버라이딩 */
                    .stButton > button[kind="primary"] {
                        background-color: #004085 !important;
                        color: white !important;
                        border: 1px solid #004085 !important;
                        font-weight: bold !important;
                    }
                    .stButton > button[kind="primary"]:hover {
                        background-color: #002752 !important;
                        border-color: #002752 !important;
                        color: white !important;
                    }
                    .stButton > button[kind="primary"]:active {
                        background-color: #002752 !important;
                        color: white !important;
                    }
                    /* Focus/Active 상태에서도 유지 */
                    .stButton > button[kind="primary"]:focus:not(:active) {
                        border-color: #004085 !important;
                        color: white !important;
                    }
                    </style>
                    """, unsafe_allow_html=True)
                    
                    # [Style] type="primary" 사용하여 CSS 타겟팅 용이하게 변경
                    if st.button("⬅️", help="목록으로", type="primary", use_container_width=True):
                        st.session_state.selected_model = None
                        st.session_state.selected_part = None
                        st.rerun()
                with c_title:
                    st.markdown(f"### 📱 {selected_model}")

                # 선택된 모델로 변수 설정
                model_df = df[df["모델"] == selected_model]
                
                # 2. 부품명 파싱
                def extract_part(name):
                    # [New] 악세사리 모델일 경우 별도 분류
                    if selected_model == "악세사리":
                        if any(x in name for x in ["필름", "카메라링", "카메라 링", "강화유리", "카메라 렌즈 보호링"]): return "필름"
                        if "케이스" in name: return "케이스"
                        if any(x in name for x in ["케이블", "어댑터", "어덥터", "충전기", "젠더"]): return "충전기"
                        return "기타"

                    # [User Request] 제외 필터 (하우징, 일반형 등)
                    if "하우징" in name: return None
                    if "(베젤형)" in name: return None # [User Request] 베젤형 하우징 제외
                    if "(일반형)" in name: return None
                    if "(고급형)" in name: return None
                    if "13Pro 골드" in name: return None # 구체적인 예시 차단

                    # [User Request] iPhone 7+, 8+ 액정 예외 처리 ((정), (재), (카))
                    # selected_model 변수가 상위 스코프에 있음
                    if selected_model in ["iPhone 7 Plus", "iPhone 8 Plus"]:
                        if any(x in name for x in ["(정)", "(재)", "(카)"]):
                            return "액정"
                    
                    # 명시적 카테고리 (케이블은 기타로 통합되므로 제거)
                    if "액정" in name: return "액정"
                    if "배터리" in name: return "배터리"
                    if "카메라" in name: return "카메라"
                    if "유리" in name: return "후면유리"
                    if "보드" in name: return "메인보드"
                    
                    # 나머지는 모두 '기타'
                    return "기타"
                
                model_df["부품"] = model_df["상품명"].apply(extract_part)
                
                # [Filter] None(하우징 등) 제거
                model_df = model_df.dropna(subset=["부품"])
                
                # [Sort] 부품 우선순위 정렬
                def part_sort_key(p):
                    if "액정" in p: return 0
                    if "배터리" in p: return 1
                    if "카메라" in p: return 2
                    if "후면유리" in p: return 3
                    if "메인보드" in p: return 4
                    if "충전기" in p: return 5
                    if "케이스" in p: return 6
                    if "필름" in p: return 7
                    return 10 # 기타
                
                parts = sorted(model_df["부품"].unique().tolist(), key=part_sort_key)
                
                # [UI Check] 부품이 없을 경우 처리
                if not parts:
                    st.warning("해당 기종의 재고가 없습니다.")
                    st.stop()

                # [UI Update] 부품 선택: Pills (모바일 가로 배치 보장) + 아이콘 적용
                st.write("🔧 부품을 선택하세요")
                
                # 아이콘 매핑
                ICON_MAP = {
                    "액정": "📱",
                    "배터리": "🔋",
                    "카메라": "📷",
                    "후면유리": "🧊",
                    "메인보드": "💾",
                    "충전기": "🔌",
                    "케이스": "🛡️",
                    "필름": "✨",
                    "기타": "📦"
                }

                # 라벨에 아이콘 합치기 (예: "📱 액정")
                # Pills는 텍스트만 지원하지만, 모바일에서 유일하게 "가로 배치"를 보장하는 컴포넌트입니다.
                part_labels = []
                label_to_real = {}
                for p in parts:
                    icon = ICON_MAP.get(p, "📦")
                    label = f"{icon} {p}"
                    part_labels.append(label)
                    label_to_real[label] = p
                
                # 이전에 선택된 부품이 있으면 default값 설정
                default_sel = None
                if st.session_state.selected_part:
                    # 저장된 part이름("액정")에 해당하는 라벨("📱 액정") 찾기
                    for lbl, real in label_to_real.items():
                        if real == st.session_state.selected_part:
                            default_sel = lbl
                            break

                selected_pill = st.pills(
                    "Part List", 
                    part_labels, 
                    selection_mode="single", 
                    default=default_sel, 
                    label_visibility="collapsed",
                    key="part_pills"
                )
                
                if selected_pill:
                     st.session_state.selected_part = label_to_real[selected_pill]
                
                # 결과 표시 (부품이 선택되었을 때)
                if st.session_state.selected_part:
                    selected_part = st.session_state.selected_part
                    final_df = model_df[model_df["부품"] == selected_part].copy()
                    
                    # [Data Cleaning]
                    final_df = final_df[final_df["가격"] != "Unknown"]
                    final_df = final_df[final_df["가격"] != ""]
                    
                    def parse_price(p_str):
                        try:
                            return int(str(p_str).replace("원", "").replace(",", "").strip())
                        except:
                            return 0
                            
                    final_df["가격_숫자"] = final_df["가격"].apply(parse_price)
                    
                    # [Fix] 중복 제거 로직 개선 (최신 데이터 우선)
                    # 1. 수집일시 기준 내림차순 정렬 (최신 데이터가 위로)
                    final_df = final_df.sort_values(by="수집일시", ascending=False)
                    # 2. 상품명이 같으면 중복 제거 (가장 위의 최신 데이터만 남김, 가격 변동 무시)
                    final_df = final_df.drop_duplicates(subset=["상품명"])
                    # 3. 보기 좋게 가격순 정렬
                    final_df = final_df.sort_values(by="가격_숫자", ascending=False)
                    
                    if not final_df.empty:
                        # [UI Update] HTML/CSS 기반 반응형 그리드 적용
                        # Native Streamlit으로는 "PC 3열 / 모바일 2열" 자동 전환이 불가능하므로 HTML 주입 사용
                        
                        st.markdown("""
                        <style>
                        /* [Fix] Mobile Overflow & Layout Tuning */
                        .product-grid {
                            display: grid;
                            grid-template-columns: repeat(3, 1fr);
                            gap: 10px;
                            width: 100%; /* 부모 컨테이너 꽉 채우기 */
                            box-sizing: border-box; /* 패딩 포함 너비 계산 */
                        }
                        
                        /* 모바일 최적화 (600px 이하) */
                        @media (max-width: 600px) {
                            .product-grid {
                                grid-template-columns: repeat(2, 1fr);
                                gap: 8px; /* 간격 축소 */
                            }
                            /* Streamlit 기본 패딩 보정 (모바일에서 여백 줄임) */
                            .block-container {
                                padding-left: 1rem !important;
                                padding-right: 1rem !important;
                            }
                        }

                        .product-card {
                            border: 1px solid rgba(49, 51, 63, 0.2);
                            border-radius: 8px;
                            padding: 10px;
                            background-color: var(--secondary-background-color);
                            color: var(--text-color);
                            font-family: sans-serif;
                            display: flex;
                            flex-direction: column;
                            justify-content: space-between;
                            box-sizing: border-box;
                            min-width: 0; /* [Fix] Grid 아이템 오버플로우 방지 필수 */
                            overflow: hidden; /* [Fix] 내용이 넘치면 숨김 */
                        }
                        .card-title {
                            font-weight: bold;
                            font-size: 0.85rem; /* [Fix] 폰트 조금 더 축소 (더 많이 보여주기 위함) */
                            margin-bottom: 8px;
                            /* [Fix] 한 줄 말줄임 -> 두 줄까지 허용 */
                            white-space: normal; 
                            display: -webkit-box;
                            -webkit-line-clamp: 2; /* 최대 2줄까지 표시 */
                            -webkit-box-orient: vertical;
                            overflow: hidden; 
                            text-overflow: ellipsis;
                            line-height: 1.3; /* 줄 간격 조정 */
                            width: 100%;
                        }
                        .card-status-soldout { color: #ff4b4b; font-size: 0.75rem; }
                        .card-status-ok { color: #0083b8; font-size: 0.75rem; }
                        .card-price-detail { font-size: 0.75rem; color: #555; margin-top: 4px; }
                        .card-total-price { font-size: 1.0rem; font-weight: bold; color: #00b050; margin-top: 5px; }
                        </style>
                        """, unsafe_allow_html=True)

                        html_content = '<div class="product-grid">'
                        
                        for idx, row in enumerate(final_df.to_dict("records")):
                            # 상태 텍스트
                            status_html = ""
                            if "품절" in row["상태"]:
                                status_html = '<span class="card-status-soldout">품절</span>'
                            else:
                                status_html = '<span class="card-status-ok">구매가능</span>'
                            
                            # 가격 계산
                            price_num = row['가격_숫자']
                            price_block = ""
                            
                            if price_num > 0:
                                vat = int(price_num * 0.1)
                                total = price_num + vat
                                p_str = f"{price_num:,}"
                                v_str = f"{vat:,}"
                                t_str = f"{total:,}"
                                
                                price_block = f"""
                                <div style="font-size: 0.8rem; opacity: 0.8;">{p_str}원 + {v_str}원 (VAT)</div>
                                <div class="card-total-price">💳 {t_str}원</div>
                                """
                            else:
                                price_block = f"<div class='card-total-price'>{row['가격']}</div>"

                            # 카드 조립
                            # [Fix] Indentation removed to prevent Markdown code block rendering
                            html_content += f"""<div class="product-card">
<div class="card-title" title="{row['상품명']}">{row['상품명']}</div>
<div style="display:flex; justify-content:space-between; align-items:center;">
{status_html}
</div>
<div>{price_block}</div>
</div>"""
                        
                        html_content += '</div>'
                        st.markdown(html_content, unsafe_allow_html=True)
                    else:
                        st.warning("가격 정보가 없는 상품만 있거나 데이터가 없습니다.")
                else:
                    st.write("👈 위 버튼을 눌러 부품을 선택해주세요.")
        
        else:
            st.warning("데이터가 없습니다.")

    with tab2:
        st.dataframe(df, use_container_width=True)

    with tab3:
        st.subheader("일일 가격 변동 내역")
        st.caption("최근 두 번의 수집 데이터를 비교하여 가격이나 상태가 변한 상품을 보여줍니다.")
        
        # [Cache] 히스토리 계산 로직 캐싱 (탭 전환 시 렉 방지)
        @st.cache_data(show_spinner=False)
        def get_history_data(df):
            # 1. 날짜만 추출 (YYYY-MM-DD)
            df["date_only"] = df["수집일시"].dt.date
            unique_days = sorted(df["date_only"].unique(), reverse=True)
            
            if len(unique_days) < 2:
                return [d.strftime("%Y-%m-%d") for d in unique_days], []
            
            history_list = []
            
            # 2. 일별 비교 (오늘 vs 어제, 어제 vs 그제...)
            # 하루에 여러 번 수집했더라도, 그 날의 '가장 마지막(최신)' 데이터만 대표로 사용
            for i in range(len(unique_days) - 1):
                curr_day = unique_days[i]
                prev_day = unique_days[i+1]
                
                # 각 날짜의 가장 최신 타임스탬프 찾기
                curr_ts = df[df["date_only"] == curr_day]["수집일시"].max()
                prev_ts = df[df["date_only"] == prev_day]["수집일시"].max()
                
                # 해당 타임스탬프의 데이터만 추출
                curr_df = df[df["수집일시"] == curr_ts].set_index("상품명")
                prev_df = df[df["수집일시"] == prev_ts].set_index("상품명")
                
                day_changes = []
                for name, row in curr_df.iterrows():
                    if name in prev_df.index:
                        prev_row = prev_df.loc[name]
                        if isinstance(prev_row, pd.DataFrame): prev_row = prev_row.iloc[0]
                        
                        curr_price = row["가격"]
                        prev_price = prev_row["가격"]
                        
                        # 가격 비교
                        try:
                            cp = int(str(curr_price).replace(",", "").replace("원", ""))
                            pp = int(str(prev_price).replace(",", "").replace("원", ""))
                            diff = cp - pp
                            if diff != 0:
                                icon = "🔻" if diff < 0 else "🔺"
                                color = "blue" if diff < 0 else "red"
                                diff_str = f":{color}[{diff:,}원]"
                                day_changes.append(f"{icon} **{name}**: {prev_price} → {curr_price} ({diff_str})")
                        except:
                            if curr_price != prev_price:
                                day_changes.append(f"🔄 **{name}**: {prev_price} → {curr_price}")
                        
                        # 상태 비교 (품절 등)
                        if row["상태"] != prev_row["상태"]:
                             day_changes.append(f"📦 **{name}**: {prev_row['상태']} → {row['상태']}")
                
                if day_changes:
                    history_list.append({
                        "date": curr_day.strftime("%Y-%m-%d"),
                        "prev_date": prev_day.strftime("%Y-%m-%d"),
                        "changes": day_changes,
                        "expanded": (i == 0) # 첫 번째(최신)만 펼침
                    })
            
            return unique_days, history_list

        dates, history_list= get_history_data(df)
        
        if len(dates) < 2:
            st.info("비교할 과거 데이터가 부족합니다. (최소 2회 이상 수집 필요)")
            st.write(f"현재 수집된 날짜: {dates}")
        else:
            st.markdown(f"##### 📉 최근 가격 변동 히스토리")
            
            if history_list:
                for item in history_list:
                    with st.expander(f"{item['date']} (vs {item['prev_date']})", expanded=item['expanded']):
                        for ch in item['changes']:
                            st.write(ch)
            else:
                st.info("최근 수집 기간 동안 가격 변동이 발견되지 않았습니다.")


else:
    st.warning("데이터가 없거나 구글 시트를 불러올 수 없습니다. 우측 메뉴에서 '업데이트'를 실행해보세요.")
