"""
한고은 채널 분석 시스템 - 설정 파일
"""
import os

# ===== API 키 (GitHub Secrets에서 자동 로드) =====
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
GOOGLE_SHEET_ID = os.getenv("GOOGLE_SHEET_ID", "1wyCxiyqeMoXxKZ9meLSYFh-hcVogS0Ltn_NblspyoZM")
GOOGLE_SERVICE_ACCOUNT_JSON = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")

# ===== 분석 대상 채널 =====
TARGET_CHANNELS = [
    "안녕하세요 최화정이에요",
    "이민정 MJ",
    "한가인",
    "강주은",
    "고소영",
    "한지혜",
    "이지혜",
    "장영란",
    "공부왕찐천재",  # 홍진경
    "서인영"
]

# ===== 자체 채널 (참고용) =====
OWN_CHANNEL = "고은언니 한고은"

# ===== 분석 설정 =====
INITIAL_MONTHS = 6  # 초기 분석할 과거 기간 (개월)
TOP_COMMENTS_COUNT = 200  # 상위 댓글 분석 수
TRENDING_VIDEOS_COUNT = 50  # 일일 트렌드 분석 영상 수

# ===== 딥시크 모델 자동 전환 =====
from datetime import datetime
DEEPSEEK_DISCOUNT_END = datetime(2026, 5, 31, 23, 59)

def get_deepseek_model(complex_task=False):
    """할인 기간엔 V4 Pro, 끝나면 V4 Flash로 자동 전환"""
    if complex_task and datetime.now() < DEEPSEEK_DISCOUNT_END:
        return "deepseek-v4-pro"
    return "deepseek-v4-flash"

# ===== 구글 시트 시트명 =====
SHEET_VIDEOS = "영상_마스터데이터"
SHEET_ANALYSIS = "AI_분석결과"
SHEET_CHANNEL_INSIGHTS = "채널별_성공공식"
SHEET_TRENDS = "일일_트렌드"
SHEET_WEEKLY = "주간_리포트"

# ===== 카테고리 =====
TRENDING_CATEGORIES = {
    "전체": "0",
    "엔터테인먼트": "24",
    "라이프&블로그": "22",
}
