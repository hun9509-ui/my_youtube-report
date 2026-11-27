"""대시보드 전역 상수.

루트 파이프라인(config.py)을 import 하지 않고 독립 실행되도록 값을 복제해 둔다.
tests/test_dashboard.py 가 config.py 와의 정합성을 검사한다.
"""

# ── Track A ───────────────────────────────────────────────
OWN_CHANNEL = "고은언니 한고은"
DEFAULT_SCORE_VERSION = "v2.0"          # config.SCORE_VERSION 과 동일해야 함
COMPARE_SCORE_VERSIONS = ("v2.0", "v1.2")
HIT_MULTIPLIER = 2.0                     # 채널 중앙값 대비 2.0배+ = 히트

# config.TARGET_CHANNELS 의 키 (설정상 채널명). 실제 videos.channel_title 은
# YouTube 표시명이라 일부 다를 수 있으므로 필터 옵션은 항상 데이터에서 만든다.
COMPETITOR_CHANNELS = (
    "안녕하세요 최화정이에요",
    "이민정 MJ",
    "자유부인 한가인",
    "깡주은",
    "고소영",
    "한지혜 Han Ji Hye",
    "밉지않은 관종언니",
    "A급 장영란",
    "공부왕찐천재 홍진경",
    "개과천선 서인영",
    "김나영의 nofilterTV",
    "순풍 선우용여",
    "채정안TV",
    "신애라이프",
    "Umaizing 엄정화TV",
    "황신혜의 cine style",
)

ACTION_ORDER = ("바로 기획화", "각색 후 기획", "아이디어 보관", "우선순위 낮음")
ACTION_META = {
    "바로 기획화": {"short": "바로 기획", "tone": "up"},
    "각색 후 기획": {"short": "각색 후", "tone": "info"},
    "아이디어 보관": {"short": "보관", "tone": "warn"},
    "우선순위 낮음": {"short": "낮음", "tone": "neutral"},
}

SUBSCORES = (
    ("novelty_score", "신선도"),
    ("hangoeun_fit_score", "한고은 적합"),
    ("execution_score", "실행 용이"),
    ("repeatability_score", "반복 가능"),
    ("risk_score", "리스크"),
)

LIFESPAN_META = {
    "EVERGREEN": ("에버그린", "up"),
    "MID_TERM": ("중기", "warn"),
    "FAST_TREND": ("단기 트렌드", "down"),
}

GROWTH_PATTERN_META = {
    "EVERGREEN": ("에버그린", "up"),
    "LONGTAIL": ("롱테일", "info"),
    "STEADY": ("꾸준", "neutral"),
    "FAST_DECAY": ("급감", "down"),
}

EMOTIONS = ("envy", "aspiration", "fatigue", "cringe")
EMOTION_LABELS = {
    "envy": "부러움",
    "aspiration": "동경",
    "fatigue": "피로",
    "cringe": "오글",
    "comfort": "위안",
    "healing": "힐링",
    "nostalgia": "향수",
    "trust": "신뢰",
    "intimacy": "친밀",
}

THUMBNAIL_SIGNALS = (
    ("couple", "커플 등장"),
    ("two_faces", "2인 얼굴"),
    ("surprise", "놀람 감정"),
    ("food", "음식 노출"),
)

# ── Track B ───────────────────────────────────────────────
STAGE_ORDER = ("breakout", "rising", "mainstream", "early", "seed", "fatigue")
STAGE_META = {
    "breakout": ("돌파", "down"),
    "rising": ("상승", "warn"),
    "mainstream": ("대중화", "info"),
    "early": ("초기", "neutral"),
    "seed": ("초기", "neutral"),
    "fatigue": ("피로", "muted"),
}

FORMAT_LABELS = {
    "lookbook": "룩북",
    "styling": "스타일링",
    "haul": "하울",
    "buying_guide": "구매 가이드",
    "review": "리뷰",
    "routine": "루틴",
    "shorts_tip": "쇼츠 팁",
    "vlog": "브이로그",
}

B_ACTION_ORDER = ("바로 제작", "기획 후보", "관찰", "보류")
B_ACTION_META = {
    "바로 제작": "up",
    "기획 후보": "info",
    "관찰": "neutral",
    "보류": "muted",
}

CATEGORY_LABELS = {
    "fashion": "패션",
    "accessory": "액세서리",
    "beauty": "뷰티",
    "fragrance": "향수",
    "lifestyle": "라이프",
    "active": "액티브",
    "home": "홈",
    "social": "소셜/밈",
}

B_METRICS = (
    ("engagement_score", "참여"),
    ("commerce_signal", "커머스"),
    ("item_heat_score", "아이템 열기"),
    ("shopping_intent_score", "구매 의도"),
    ("ppl_potential", "PPL"),
    ("channel_fit_score", "채널 적합"),
    ("originality_room", "독창 여지"),
    ("production_difficulty", "제작 난이도"),
)
