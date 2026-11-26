"""
B-track 트렌드 인텔리전스 설정
패션/뷰티/라이프스타일/사회 유행 레이더
"""

import os
import re

# ===== 초기 Seed 채널 =====
# source_ref: 유튜브 채널 ID 또는 핸들(@로 시작)
# category_hint: 카테고리 힌트 (쉼표 구분)
# priority: 1~10 (높을수록 먼저 처리)
SEED_CHANNELS = [
    {
        "source_name": "패피tv",
        "source_ref": "@패피tv",
        "category_hint": "fashion,lifestyle,buying_guide",
        "priority": 10,
    },
    {
        "source_name": "오오티디",
        "source_ref": "@OOTD",
        "category_hint": "fashion,men,women",
        "priority": 8,
    },
    {
        "source_name": "룩북코리아",
        "source_ref": "@lookbook_korea",
        "category_hint": "fashion,lookbook",
        "priority": 8,
    },
    {
        "source_name": "향수덕후",
        "source_ref": "@fragrance_mania",
        "category_hint": "fragrance,lifestyle",
        "priority": 7,
    },
]

# ===== 수집 설정 =====
COLLECTION_MONTHS = 12          # 최근 N개월 영상 수집
BROAD_SCAN_BATCH_SIZE = 40      # Gemini 한 번에 처리할 영상 수 (호출 횟수↓로 처리량↑)
MAX_VIDEOS_PER_RUN = 2000       # 1회 실행당 최대 처리 영상 수 (브로드 스캔, Gemini 크레딧)
MIN_VIEW_COUNT = 1000           # 이 이하 조회수는 수집 제외
COLLECT_REFRESH_HOURS = 20      # 이 시간 내 이미 수집한 채널은 이번 회차 skip (오래된 것부터 처리)
B_TRACK_REFRESH_DAYS = 90
SHORTS_MAX_SECONDS = 180        # 이 이하 길이는 숏폼으로 분류 (초, 유튜브 공식 Shorts 상한 3분). baseline 분리 기준

# ===== YouTube API 유닛 예산 (절대 제약: A-track 침범 금지) =====
# 전체 한도 10,000/일 = A-track 격리분 1,000 + 비상버퍼 1,000 + B-track 8,000
# A-track 실측 일일 소비 ~50~150유닛 → 1,000은 6배 이상 안전마진
B_TRACK_DAILY_QUOTA_BUDGET = 8000   # B-track 일일 총 유닛 예산
B_TRACK_QUOTA_HARD_STOP = True       # 예산 소진 시 즉시 중단·익일 이월 (A-track 몫 침범 금지)
# search 1회 = 100유닛. 8,000 한도 내에서 읽기(단계2) 예비분만 남기고 search 최대 가동.
B_TRACK_READ_RESERVE = 2000          # 단계2 사실조회(읽기, 1유닛)용 예비 (=2,000회 읽기)
# → search 가용 = (8000 - 사용량 - READ_RESERVE) 범위에서 동적으로 최대 가동
# 유닛 비용표 (YouTube Data API v3)
QUOTA_COST = {
    "search": 100,
    "channels": 1,
    "playlistItems": 1,
    "videos": 1,
    "commentThreads": 1,
}

# ===== Gemini 설정 =====
BROAD_SCAN_MODEL = os.getenv("BTRACK_SCAN_MODEL", "gemini-3.5-flash-lite")
TREND_PACKET_MODEL = os.getenv("BTRACK_PACKET_MODEL", "gemini-3.5-flash-lite")


def _safe_error(error):
    """Mask credentials before delegating to the shared error formatter."""
    import config

    message = re.sub(r"""([?&]key=)[^&\s"'<>]+""", r"\1[redacted]", str(error))
    for name in ("YOUTUBE_API_KEY", "GEMINI_API_KEY", "DEEPSEEK_API_KEY", "SUPABASE_KEY"):
        secret = getattr(config, name, None)
        if secret:
            message = message.replace(secret, "[redacted]")
    try:
        from gemini_client import safe_error
    except ImportError:
        return message[:500]
    return safe_error(message)[:500]


def _read_pages(build_query, page_size=1000, *, max_rows=None):
    """Read an explicitly ordered PostgREST query with inclusive ranges."""
    rows = []
    while max_rows is None or len(rows) < max_rows:
        offset = len(rows)
        size = page_size if max_rows is None else min(page_size, max_rows - offset)
        # PostgREST appends range parameters to the builder, so each page needs a fresh one.
        page = build_query().range(offset, offset + size - 1).execute().data or []
        rows.extend(page)
        if len(page) < size:
            break
    return rows


# ===== view_over_baseline 기준 =====
# 채널 평균 대비 이 배수 이상이어야 deep dive 후보
DEEP_DIVE_THRESHOLD = 3.0
LIGHT_SCAN_THRESHOLD = 1.5

# ===== 트렌드 신호 기준 =====
RISING_MIN_CHANNELS = 3         # 최소 N개 채널에서 등장해야 rising
MAINSTREAM_MIN_CHANNELS = 10    # N개 이상이면 mainstream
VELOCITY_WINDOW_DAYS = 7        # 속도 계산 기간

# ===== 발굴(discovery) 설정 =====
# 단계1 search 키워드. search 1회=100유닛, 예산 가드 내에서 순서대로 소진.
# 카테고리별 검색어 — 채널이 아니라 "지금 이 주제로 콘텐츠 만드는 채널"을 찾는 용도.
DISCOVERY_KEYWORDS = [
    # fashion
    "남자 패션 코디", "여자 데일리룩", "패션 하울", "옷 추천", "코디 추천",
    "패션 트렌드", "명품 하울", "무신사 추천",
    # beauty
    "메이크업 튜토리얼", "스킨케어 루틴", "화장품 추천", "뷰티 하울", "겟레디윗미",
    "다이소 화장품",
    # fragrance
    "향수 추천", "남자 향수", "여자 향수", "공간 방향제",
    # active
    "러닝화 추천", "운동복 추천", "러닝 브이로그",
    # accessory
    "선글라스 추천", "가방 추천",
    # brand / shopping
    "신상 리뷰", "브랜드 팝업", "쿠팡 하울",
    # social / trend
    "요즘 유행", "올영 추천",
]
DISCOVERY_SEARCH_MAX_RESULTS = 50   # search 1회당 결과 수 (최대 50)
DISCOVERY_REGION = "KR"
DISCOVERY_RELEVANCE_LANG = "ko"
DISCOVERY_GEMINI_BATCH = 15         # Gemini 분류 1회당 채널 수
DISCOVERY_RECENT_TITLES = 10        # 후보별 최근 영상 제목 수집 개수
DISCOVERY_MIN_SUBSCRIBERS = 3000    # 이 미만 구독자 채널은 후보 제외
