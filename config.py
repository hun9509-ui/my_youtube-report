"""
한고은 채널 분석 시스템 - 설정 파일
"""
import os
from datetime import timedelta, timezone

KST = timezone(timedelta(hours=9), name="KST")

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

# ===== Google Cloud (Vertex AI) =====
GOOGLE_CLOUD_PROJECT = os.getenv("GOOGLE_CLOUD_PROJECT", "totemic-courage-495009-h4")
GOOGLE_CLOUD_LOCATION = os.getenv("GOOGLE_CLOUD_LOCATION", "global")

# ===== 분석 대상 채널 =====
TARGET_CHANNELS = {
    "안녕하세요 최화정이에요": "UCA9kvKQdNxslWm5IKZgXS_A",
    "이민정 MJ": "UCFNUGAiaGRgzVCXrjVoe1Hw",
    "자유부인 한가인": "UCvnVUhn95YfQazC_bc2lU6w",
    "깡주은": "UCcjmCG5tTegWwqcSDSkDevQ",
    "고소영": "UCag6Qu6uHjQKPvBI5MW12AQ",
    "한지혜 Han Ji Hye": "UCfcEEsjr6_lUUHQ-1zID2cQ",
    "밉지않은 관종언니": "UCtkRVaUSpkuhqdKv39oBXKA",
    "A급 장영란": "UCfVWxOKzPwSZkEcxXmrH6yw",
    "공부왕찐천재 홍진경": "UCkxbPwdaV74Erdxt97Nt23w",
    "개과천선 서인영": "UCAMGWbOnDd9dpAK7BO0Wftw"
}

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
OWN_CHANNEL_ID = "UCPFW9tE-hAuyR4037csZAqg"
OWN_CHANNEL_TRACKING_WEEKS = 5  # 업로드 후 5주 월요일까지 추적

# ===== 분석 버전 =====
ANALYSIS_VERSION = "1.2"  # 검증된 응답 및 모델 버전 기록
SCORE_VERSION = "v1.2"    # 전략 스코어링 버전

# ===== 분석 설정 =====
INITIAL_MONTHS = 6
TOP_COMMENTS_COUNT = 200
TRENDING_VIDEOS_COUNT = 50
SHORTS_MAX_SECONDS = 180
DAILY_LOOKBACK_DAYS = 7

# ===== Gemini 모델 =====
# TEXT_ANALYSIS_ENGINE: "gemini" | "deepseek"
# "gemini"로 설정하면 deepseek_processor의 모든 분석이 Gemini로 라우팅됨
GEMINI_PROVIDER = os.getenv("GEMINI_PROVIDER", "ai_studio").strip().lower()
TEXT_ANALYSIS_ENGINE = os.getenv("TEXT_ANALYSIS_ENGINE", "deepseek").strip().lower()
GEMINI_THINKING_LEVEL = os.getenv("GEMINI_THINKING_LEVEL", "").strip().upper()


def get_gemini_model(mode='daily'):
    """용도별 모델을 개별 환경변수로 변경할 수 있다."""
    if mode in ('btrack', 'free'):
        return os.getenv("GEMINI_BTRACK_MODEL", "gemini-3.5-flash-lite")
    default = os.getenv("GEMINI_MODEL", "gemini-4-hydrogen")
    return os.getenv(f"GEMINI_{mode.upper()}_MODEL", default)


def get_gemini_fallback_models(mode='daily') -> list[str]:
    configured = os.getenv("GEMINI_FALLBACK_MODELS")
    if configured is not None:
        models = [model.strip() for model in configured.split(",")]
    elif mode in ('btrack', 'free'):
        models = ["gemini-2.5-flash-lite"]
    else:
        models = ["gemini-3.8-flash", "gemini-3.5-flash", "gemini-3.5-flash-lite", "gemini-2.5-flash-lite"]
    primary = get_gemini_model(mode)
    return [model for model in dict.fromkeys(models) if model and model != primary]

# ===== 대박 영상 기준 =====
HIT_VIDEO_MULTIPLIER = 2.0  # 채널 평균 조회수 × 2배 이상

# ===== DeepSeek 모델 =====
def get_deepseek_model(complex_task=False):
    default = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash")
    return os.getenv("DEEPSEEK_COMPLEX_MODEL", default) if complex_task else default

# ===== 구글 시트 탭 이름 =====
SHEET_VIDEOS = "영상_마스터데이터"
SHEET_INITIAL = "초기_분석결과"          # 신규
SHEET_DAILY = "일일_분석결과"            # 신규
SHEET_CHANNEL_INSIGHTS = "채널별_성공공식"
SHEET_TRENDS = "일일_트렌드"
SHEET_EVENT_KEYWORDS = "일일_사건키워드"
SHEET_WEEKLY = "주간_리포트"
SHEET_OWN_ANALYSIS = "자체채널_분석"
SHEET_OWN_TRACKING = "자체채널_추적"
SHEET_SCORES = "전략_스코어"

# 기존 호환성 유지
SHEET_ANALYSIS = SHEET_DAILY  # 기존 코드 호환

# ===== 트렌드 카테고리 =====
TRENDING_CATEGORIES = {
    "전체": "0",
    "엔터테인먼트": "24",
    "라이프&블로그": "22",
}
