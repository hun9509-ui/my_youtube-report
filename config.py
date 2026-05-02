"""
한고은 채널 분석 시스템 - 설정 파일
"""
import os
from datetime import datetime

# ===== API 키 =====
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
GOOGLE_SHEET_ID = os.getenv("GOOGLE_SHEET_ID", "1wyCxiyqeMoXxKZ9meLSYFh-hcVogS0Ltn_NblspyoZM")
GOOGLE_WEBHOOK_URL = os.getenv("GOOGLE_WEBHOOK_URL")

# ===== Supabase =====
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

# ===== 분석 대상 채널 =====
TARGET_CHANNELS = [
    "안녕하세요 최화정이에요",
    "이민정 MJ",
    "자유부인 한가인",
    "깡주은",
    "고소영",
    "한지혜 Han Ji Hye",
    "밉지않은 관종언니",
    "A급 장영란",
    "공부왕찐천재 홍진경",
    "개과천선 서인영"
]

# ===== 초기 수집 배치 분할 (가나다순) =====
INITIAL_BATCHES = {
    1: ["개과천선 서인영", "고소영"],
    2: ["깡주은", "공부왕찐천재 홍진경"],
    3: ["밉지않은 관종언니", "안녕하세요 최화정이에요"],
    4: ["이민정 MJ", "A급 장영란"],
    5: ["자유부인 한가인", "한지혜 Han Ji Hye"],
}

# ===== 자체 채널 =====
OWN_CHANNEL = "고은언니 한고은"

# ===== 분석 설정 =====
INITIAL_MONTHS = 6
TOP_COMMENTS_COUNT = 200
TRENDING_VIDEOS_COUNT = 50

# ===== DeepSeek 모델 =====
DEEPSEEK_DISCOUNT_END = datetime(2026, 5, 31, 23, 59)

def get_deepseek_model(complex_task=False):
    if complex_task and datetime.now() < DEEPSEEK_DISCOUNT_END:
        return "deepseek-v4-pro"
    return "deepseek-v4-flash"

# ===== 구글 시트 탭 이름 =====
SHEET_VIDEOS = "영상_마스터데이터"
SHEET_INITIAL = "초기_분석결과"          # 신규
SHEET_DAILY = "일일_분석결과"            # 신규
SHEET_CHANNEL_INSIGHTS = "채널별_성공공식"
SHEET_TRENDS = "일일_트렌드"
SHEET_WEEKLY = "주간_리포트"

# 기존 호환성 유지
SHEET_ANALYSIS = SHEET_DAILY  # 기존 코드 호환

# ===== 트렌드 카테고리 =====
TRENDING_CATEGORIES = {
    "전체": "0",
    "엔터테인먼트": "24",
    "라이프&블로그": "22",
}
