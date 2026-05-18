"""
트렌드 영상 분류 모듈

기능:
  1. 키워드 룰 기반 트랙 분류 (트랙 A: 30~60대 여성 / 트랙 B: 10~30대)
  2. 게임/정치/시사 자동 제외
  3. 이레귤러 감지 (24시간 50만 OR 시간당 5만)
  4. 사건/현상 키워드 자동 추출 (빈도 기반)

AI 점수 사용 안 함 - 100% 객관 룰 기반.
"""
from datetime import datetime, timezone
from collections import Counter
import re


# ────────────────────────────────────────────
# 제외 키워드 (두 트랙 모두 적용)
# ────────────────────────────────────────────
EXCLUDE_KEYWORDS_COMMON = [
    # 게임
    "게임", "플레이", "공략", "스트리머", "롤", "LOL",
    "배그", "마인크래프트", "발로란트", "오버워치", "메이플",
    "스팀", "콘솔", "PS5", "닌텐도",
    # 정치
    "대선", "총선", "정당", "민주당", "국민의힘", "정치",
    "국회", "의원", "장관", "대통령",
    # 시사
    "시사", "뉴스속보", "특종", "긴급속보",
    # 애니/만화/서브컬처
    "애니", "애니메이션", "anime", "만화", "웹툰", "피규어",
    "코스프레", "짱구", "도라에몽", "원피스", "나루토",
    "드래곤볼", "포켓몬", "귀멸", "진격", "주술회전",
    "블리치", "원신", "이세계", "마왕", "용사", "성우",
    "오타쿠", "덕후", "성지순례",
]

# 트랙 A (30~60대 여성) 추가 제외
EXCLUDE_KEYWORDS_TRACK_A = [
    # 전문 메이크업/뷰티
    "메이크업", "화장법", "립", "아이섀도우", "컨실러",
    "쉐도우", "마스카라", "베이스", "파운데이션", "파데",
    # 10대 콘텐츠
    "고딩", "중딩", "교복", "급식", "수능",
]


# ────────────────────────────────────────────
# 트랙 시그널 키워드 (PD님이 추후 운영하며 튜닝)
# ────────────────────────────────────────────
TRACK_A_SIGNALS = {
    # 강한 신호 (점수 +3)
    "strong": [
        "주부", "살림", "쟁임템", "찐템", "60대", "갱년기",
        "친정", "며느리", "시어머니", "엄마", "아내", "주방",
        "정리수납", "살림꿀팁", "결혼생활", "부부",
    ],
    # 중간 신호 (점수 +1)
    "medium": [
        "일상", "브이로그", "정리", "요리", "자취",
        "레시피", "집밥", "수납", "청소", "데일리",
    ],
}

TRACK_B_SIGNALS = {
    "strong": [
        "챌린지", "MZ", "갓생", "ㄹㅇ", "역대급",
        "사태", "근황", "팩폭", "인싸",
    ],
    "medium": [
        "밈", "유행", "트렌드", "꿀잼", "레전드",
        "썰", "후기", "리뷰",
    ],
}


# ────────────────────────────────────────────
# 1. 제외 필터
# ────────────────────────────────────────────

def is_excluded(video: dict, track: str = "common") -> tuple[bool, str]:
    """
    영상이 제외 대상인지 판정.
    Returns: (제외여부, 사유)
    """
    text = f"{video.get('title', '')} {video.get('description', '')[:200]}"
    text_lower = text.lower()

    # 공통 제외
    for kw in EXCLUDE_KEYWORDS_COMMON:
        if kw.lower() in text_lower:
            return True, f"공통제외: {kw}"

    # 트랙 A 추가 제외
    if track == "A":
        for kw in EXCLUDE_KEYWORDS_TRACK_A:
            if kw.lower() in text_lower:
                return True, f"트랙A제외: {kw}"

    return False, ""


# ────────────────────────────────────────────
# 2. 트랙 점수 계산 (객관 룰)
# ────────────────────────────────────────────

def calculate_track_scores(video: dict) -> dict:
    """
    영상의 트랙 A/B 점수를 키워드 매칭으로 계산.
    AI 호출 없음. 100% 재현 가능.
    """
    text = f"{video.get('title', '')} {video.get('description', '')[:300]}"
    text_lower = text.lower()

    score_a = 0
    score_b = 0
    matched_a = []
    matched_b = []

    # 트랙 A 매칭
    for kw in TRACK_A_SIGNALS["strong"]:
        if kw.lower() in text_lower:
            score_a += 3
            matched_a.append(kw)
    for kw in TRACK_A_SIGNALS["medium"]:
        if kw.lower() in text_lower:
            score_a += 1
            matched_a.append(kw)

    # 트랙 B 매칭
    for kw in TRACK_B_SIGNALS["strong"]:
        if kw.lower() in text_lower:
            score_b += 3
            matched_b.append(kw)
    for kw in TRACK_B_SIGNALS["medium"]:
        if kw.lower() in text_lower:
            score_b += 1
            matched_b.append(kw)

    return {
        "score_a": score_a,
        "score_b": score_b,
        "matched_a": matched_a,
        "matched_b": matched_b,
    }


def assign_track(video: dict) -> str | None:
    """
    영상을 트랙에 배정.
    Returns: 'A' | 'B' | None (둘 다 0점이면 미분류)
    """
    # 공통 제외 먼저
    excluded, _ = is_excluded(video, "common")
    if excluded:
        return None

    scores = calculate_track_scores(video)

    # 둘 다 0점이면 미분류
    if scores["score_a"] == 0 and scores["score_b"] == 0:
        return None

    # 트랙 A 후보면 추가 제외 체크
    if scores["score_a"] >= scores["score_b"]:
        excluded_a, _ = is_excluded(video, "A")
        if excluded_a:
            return "B" if scores["score_b"] > 0 else None
        return "A"

    return "B"


# ────────────────────────────────────────────
# 3. 이레귤러 감지 (24시간 50만 OR 시간당 5만)
# ────────────────────────────────────────────

def detect_irregular(video: dict) -> dict:
    """
    이레귤러 영상 감지.
    Returns: {'is_irregular': bool, 'reasons': [str], 'metrics': {}}
    """
    published_at = video.get("published_at", "")
    view_count = video.get("view_count", 0)

    if not published_at:
        return {"is_irregular": False, "reasons": [], "metrics": {}}

    try:
        upload_time = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
    except ValueError:
        return {"is_irregular": False, "reasons": [], "metrics": {}}

    now = datetime.now(timezone.utc)
    hours_since_upload = max((now - upload_time).total_seconds() / 3600, 0.1)
    views_per_hour = view_count / hours_since_upload

    reasons = []

    # 조건 1: 24시간 내 50만 이상
    if hours_since_upload <= 24 and view_count >= 500_000:
        reasons.append(f"24시간 내 {view_count:,}회 (50만↑)")

    # 조건 2: 시간당 5만 이상
    if views_per_hour >= 50_000:
        reasons.append(f"시간당 {int(views_per_hour):,}회 (5만↑)")

    return {
        "is_irregular": len(reasons) > 0,
        "reasons": reasons,
        "metrics": {
            "hours_since_upload": round(hours_since_upload, 1),
            "view_count": view_count,
            "views_per_hour": int(views_per_hour),
        },
    }


# ────────────────────────────────────────────
# 4. 사건/현상 키워드 추출 (빈도 기반)
# ────────────────────────────────────────────

# 한국어 불용어 (너무 흔해서 키워드로 의미 없는 것)
STOPWORDS = {
    "그리고", "그런데", "하지만", "그래서", "그러나",
    "이것", "저것", "그것", "여기", "거기",
    "정말", "진짜", "너무", "아주", "매우",
    "오늘", "어제", "내일", "지금", "이제",
    "사람", "이번", "다른", "그냥", "혹시",
    "유튜브", "영상", "채널", "구독", "좋아요",
    "shorts", "shorts영상", "official",
}


def extract_event_keywords(videos: list[dict], top_n: int = 10) -> list[str]:
    """
    영상 제목에서 빈도 높은 명사를 사건/현상 키워드로 추출.
    예: "성수 포켓몬 사태" → ['성수', '포켓몬', '사태']
    형태소 분석기 없이 정규식 기반 (의존성 추가 없이 작동).
    """
    all_text = " ".join([v.get("title", "") for v in videos])

    # 한글 2글자 이상 + 영문 단어 추출
    korean_words = re.findall(r"[가-힣]{2,}", all_text)
    english_words = re.findall(r"[a-zA-Z]{3,}", all_text)
    words = korean_words + [w.lower() for w in english_words]

    # 불용어 제거
    words = [w for w in words if w not in STOPWORDS]

    # 빈도 계산
    counter = Counter(words)

    # 3회 이상 등장 + 빈도순 정렬
    significant = [w for w, c in counter.most_common(50) if c >= 3]

    return significant[:top_n]


# ────────────────────────────────────────────
# 5. 통합 분류 파이프라인
# ────────────────────────────────────────────

def classify_videos(videos: list[dict]) -> dict:
    """
    영상 리스트를 받아 트랙별로 분류 + 이레귤러 감지.
    Returns: {
        'track_a': [...],
        'track_b': [...],
        'irregular': [...],
        'unclassified': [...],
        'event_keywords': [...]
    }
    """
    track_a = []
    track_b = []
    irregular = []
    unclassified = []

    for v in videos:
        # 이레귤러 먼저 감지 (트랙과 별개)
        ir = detect_irregular(v)
        if ir["is_irregular"]:
            v_copy = dict(v)
            v_copy["irregular_reasons"] = ir["reasons"]
            v_copy["irregular_metrics"] = ir["metrics"]
            irregular.append(v_copy)

        # 트랙 분류
        track = assign_track(v)
        scores = calculate_track_scores(v)
        v_copy = dict(v)
        v_copy["track_score_a"] = scores["score_a"]
        v_copy["track_score_b"] = scores["score_b"]
        v_copy["matched_keywords"] = scores["matched_a"] + scores["matched_b"]

        if track == "A":
            track_a.append(v_copy)
        elif track == "B":
            track_b.append(v_copy)
        else:
            unclassified.append(v_copy)

    # 트랙별 조회수 순 정렬
    track_a.sort(key=lambda x: x.get("view_count", 0), reverse=True)
    track_b.sort(key=lambda x: x.get("view_count", 0), reverse=True)

    # 이레귤러는 시간당 조회수 순
    irregular.sort(
        key=lambda x: x.get("irregular_metrics", {}).get("views_per_hour", 0),
        reverse=True,
    )

    # 사건 키워드 추출 (전체 영상 기준)
    event_keywords = extract_event_keywords(videos)

    return {
        "track_a":        track_a[:25],
        "track_b":        track_b[:25],
        "irregular":      irregular,
        "unclassified":   unclassified,
        "event_keywords": event_keywords,
    }
