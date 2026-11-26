"""
B-track 엔티티 판단 레이어.

역할:
  1. trend_entities + entity_mentions + trend_videos를 엔티티 단위로 집계
  2. 언급량, 채널 확산, 평균 대비 성과, 트렌드 단계, 추천 액션 계산
  3. trend_entity_scores / trend_answer_cards에 저장

핵심 점수는 API 호출 없이 순수 Python 룰 기반으로 계산한다.
"""
from __future__ import annotations

import re
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from statistics import median
from typing import Any

from supabase import create_client

import config
from b_track_config import _read_pages


WINDOW_7D = 7
WINDOW_30D = 30
WINDOW_90D = 90
KST = timezone(timedelta(hours=9))

FORMAT_KEYWORDS = {
    "lookbook": ("룩북", "ootd", "착장", "데일리룩", "출근룩", "데이트룩"),
    "styling": ("코디", "스타일링", "매치", "입는 법", "레이어드"),
    "haul": ("하울", "언박싱", "쇼핑", "지름", "내돈내산", "택배"),
    "buying_guide": (
        "추천", "가이드", "고르는 법", "비교", "베스트", "top", "올영", "무신사",
        "다이소", "쿠팡", "가성비", "꿀템", "찐템", "사기 전",
    ),
    "review": ("리뷰", "후기", "착용", "솔직", "장단점"),
    "routine": ("루틴", "grwm", "겟레디", "스킨케어", "메이크업"),
}
FORMAT_DIFFICULTY = {
    "shorts_tip": 0.25, "buying_guide": 0.40, "review": 0.45, "routine": 0.50,
    "styling": 0.60, "haul": 0.65, "lookbook": 0.75, "vlog": 0.55,
}
COMMERCE_KEYWORDS = (
    "구매", "추천", "하울", "언박싱", "쇼핑", "가성비", "가격", "할인", "세일", "올영",
    "무신사", "쿠팡", "다이소", "링크", "품절", "재구매", "내돈내산", "찐템", "꿀템", "신상",
)
SHOPPING_INTENT_KEYWORDS = (
    "사기 전", "비교", "고르는 법", "추천템", "가성비", "사이즈", "착샷", "후기", "리뷰",
    "내돈내산", "재구매", "추천", "꿀템",
)
LUXURY_KEYWORDS = ("럭셔리", "명품", "샤넬", "에르메스", "디올", "luxury", "chanel", "hermes", "dior")
COMMERCE_TYPE_BONUS = {"item": 0.25, "product": 0.25, "brand": 0.20, "style": 0.10, "trend": 0.10}

ENTITY_SYNONYM_MAP = {
    alias: names[0]
    for names in (
        ("나이키", "nike"),
        ("아디다스", "adidas"),
        ("뉴발란스", "newbalance"),
        ("아식스", "asics"),
        ("살로몬", "salomon"),
        ("유니클로", "uniqlo"),
        ("자라", "zara"),
        ("코스", "cos"),
        ("무신사", "musinsa"),
        ("올리브영", "올영", "oliveyoung"),
        ("다이소", "daiso"),
        ("쿠팡", "coupang"),
        ("샤넬", "chanel"),
        ("에르메스", "hermes"),
        ("디올", "dior"),
        ("프라다", "prada"),
        ("미우미우", "miumiu"),
        ("룰루레몬", "lululemon"),
        ("파타고니아", "patagonia"),
        ("아크테릭스", "arcteryx"),
        ("스투시", "stussy"),
        ("폴로", "랄프로렌", "polo", "ralphlauren"),
        ("노스페이스", "northface"),
        ("컨버스", "converse"),
        ("반스", "vans"),
        ("자크뮈스", "jacquemus"),
        ("발렌시아가", "balenciaga"),
        ("셀린느", "celine"),
        ("보테가베네타", "bottegaveneta"),
        ("에이치앤엠", "hm"),
    )
    for alias in names
}


def canonical_entity_key(name: str) -> str:
    normalized = re.sub(r"[\s\-_.&']+", "", str(name or "").strip().lower())
    return ENTITY_SYNONYM_MAP.get(normalized, normalized)


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _days_ago(value: Any, now: datetime) -> int | None:
    dt = _parse_dt(value)
    if not dt:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return max(0, int((now - dt).total_seconds() // 86400))


def _round(value: float, digits: int = 3) -> float:
    return round(float(value or 0), digits)


def _clamp01(value: float) -> float:
    return max(0.0, min(value, 1.0))


def _confidence(mention_count_30d: int, distinct_channels_30d: int) -> str:
    if mention_count_30d >= 4 and distinct_channels_30d >= 3:
        return "high"
    if mention_count_30d >= 2 and distinct_channels_30d >= 2:
        return "medium"
    return "low"


def _trend_stage(
    mention_count_7d: int,
    mention_count_30d: int,
    distinct_channels_30d: int,
    avg_baseline: float,
    hit_ratio: float,
) -> tuple[str, str]:
    if mention_count_30d >= 12 and distinct_channels_30d >= 8 and avg_baseline < 1.2:
        return "fatigue", "언급 채널은 많지만 평균 대비 성과가 약해 피로 신호로 판단"
    if mention_count_7d >= 2 and distinct_channels_30d >= 3 and avg_baseline >= 1.8 and hit_ratio >= 0.5:
        return "breakout", "최근 언급과 채널 확산, 평균 대비 성과가 함께 높음"
    if mention_count_30d >= 8 and distinct_channels_30d >= 5:
        return "mainstream", "이미 여러 채널에서 넓게 다뤄지는 대중화 단계"
    if mention_count_30d >= 3 and distinct_channels_30d >= 3:
        return "rising", "여러 채널에서 반복 감지되어 상승 신호로 판단"
    return "seed", "아직 제한된 채널에서만 감지되는 초기 신호"


def _recommended_action(stage: str, avg_baseline: float, confidence: str) -> str:
    if stage == "breakout" and confidence in ("medium", "high"):
        return "바로 제작"
    if stage == "rising" and avg_baseline >= 1.3:
        return "기획 후보"
    if stage == "fatigue":
        return "보류"
    return "관찰"


def _mention_text(mention: dict) -> str:
    return f"{mention.get('title') or ''} {mention.get('description_short') or ''}".lower()


def classify_format(mention: dict) -> str:
    for fmt in (mention.get("content_format"), mention.get("format")):
        if isinstance(fmt, str) and fmt in FORMAT_DIFFICULTY:
            return fmt

    text = _mention_text(mention)
    for fmt, keywords in FORMAT_KEYWORDS.items():
        if any(keyword in text for keyword in keywords):
            return fmt
    return "shorts_tip" if str(mention.get("video_type") or "").lower() == "shorts" else "vlog"


def _format_distribution(mentions_30d: list[dict]) -> dict[str, int]:
    return dict(Counter(classify_format(mention) for mention in mentions_30d).most_common())


def _fashion_scores(
    entity: dict,
    mentions_30d: list[dict],
    *,
    fmt_dist: dict[str, int],
    best_format: str,
    avg_baseline: float,
    hit_ratio: float,
    distinct_channels_30d: int,
    stage: str,
    freshness: float,
) -> dict[str, float]:
    engagement_rates = []
    comment_rates = []
    texts = [_mention_text(mention) for mention in mentions_30d]
    for mention in mentions_30d:
        views = int(mention.get("view_count_at_scan") or mention.get("view_count") or 0)
        likes = int(mention.get("like_count") or 0)
        comments = int(mention.get("comment_count") or 0)
        if views > 0 and (likes > 0 or comments > 0):
            engagement_rates.append((likes + comments * 3) / views)
        if views > 0 and comments > 0:
            comment_rates.append(comments / views)

    def keyword_share(keywords: tuple[str, ...]) -> float:
        if not texts:
            return 0.0
        return sum(any(keyword in text for keyword in keywords) for text in texts) / len(texts)

    entity_type = entity.get("entity_type")
    commerce_bonus = COMMERCE_TYPE_BONUS.get(entity_type, 0.05)
    commerce_signal = _clamp01(keyword_share(COMMERCE_KEYWORDS) * 0.75 + commerce_bonus)
    product_or_brand = entity_type in ("item", "product", "brand")
    channel_reach = min(distinct_channels_30d / 8.0, 1.0)
    heat_base = min(avg_baseline / 3.0, 1.0) * 0.45 + hit_ratio * 0.30 + channel_reach * 0.25
    ppl_stage_bonus = 0.10 if stage in ("rising", "breakout", "mainstream") else 0.0
    ppl_fatigue_penalty = 0.25 if stage == "fatigue" else 0.0

    category = str(entity.get("category") or "").lower()
    if any(keyword in category for keyword in ("fashion", "accessory", "brand", "shopping", "active", "style")):
        category_fit = 0.45
    elif any(keyword in category for keyword in ("beauty", "fragrance", "lifestyle")):
        category_fit = 0.38
    else:
        category_fit = 0.20
    if best_format in ("lookbook", "styling", "haul", "buying_guide", "review"):
        format_fit = 0.30
    elif best_format in ("routine", "shorts_tip"):
        format_fit = 0.22
    else:
        format_fit = 0.15

    entity_name = str(entity.get("name") or entity.get("entity_name") or "").lower()
    luxury = any(keyword in entity_name for keyword in LUXURY_KEYWORDS) or keyword_share(LUXURY_KEYWORDS) > 0.5
    saturation = min(distinct_channels_30d / 10, 1.0)
    median_comment_rate = median(comment_rates) if comment_rates else 0.0
    scores = {
        "engagement_score": median(engagement_rates) / 0.06 if engagement_rates else 0.0,
        "commerce_signal": commerce_signal,
        "item_heat_score": heat_base * (1.0 if product_or_brand else 0.85),
        "shopping_intent_score": (
            keyword_share(SHOPPING_INTENT_KEYWORDS) * 0.75 + min(median_comment_rate / 0.005, 1.0) * 0.25
        ),
        "ppl_potential": (
            (0.35 if product_or_brand else 0.15) + commerce_signal * 0.35 + channel_reach * 0.20
            + ppl_stage_bonus - ppl_fatigue_penalty
        ),
        "channel_fit_score": category_fit + format_fit + min(avg_baseline / 2.5, 1.0) * 0.25,
        "production_difficulty": FORMAT_DIFFICULTY.get(best_format, 0.55) + (0.15 if luxury else 0.0),
        "originality_room": (1.0 - saturation) * 0.50 + freshness * 0.30 + (0.20 if len(fmt_dist) <= 2 else 0.10),
    }
    return {key: _round(_clamp01(value)) if mentions_30d else 0.0 for key, value in scores.items()}


def _best_examples(mentions_30d: list[dict], limit: int = 3) -> list[dict]:
    ranked = sorted(
        mentions_30d,
        key=lambda m: (
            float(m.get("view_over_baseline") or 0),
            int(m.get("view_count_at_scan") or m.get("view_count") or 0),
        ),
        reverse=True,
    )
    examples = []
    for mention in ranked[:limit]:
        examples.append({
            "video_id": mention.get("video_id"),
            "title": mention.get("title", ""),
            "channel_title": mention.get("channel_title", ""),
            "video_url": mention.get("video_url") or (
                f"https://youtu.be/{mention.get('video_id')}" if mention.get("video_id") else ""
            ),
            "view_over_baseline": _round(mention.get("view_over_baseline") or 0, 2),
            "view_count": int(mention.get("view_count_at_scan") or mention.get("view_count") or 0),
        })
    return examples


def score_entity(
    entity: dict,
    mentions: list[dict],
    scored_at: date | None = None,
    now: datetime | None = None,
) -> tuple[dict, dict]:
    """단일 엔티티의 점수 row와 답변 card row를 생성한다."""
    now = now or datetime.now(timezone.utc)
    scored_at = scored_at or now.date()

    enriched = []
    for mention in mentions:
        days = _days_ago(mention.get("published_at") or mention.get("created_at"), now)
        if days is None:
            continue
        item = dict(mention)
        item["_days_ago"] = days
        enriched.append(item)

    mentions_7d = [m for m in enriched if m["_days_ago"] <= WINDOW_7D]
    mentions_30d = [m for m in enriched if m["_days_ago"] <= WINDOW_30D]
    mentions_90d = [m for m in enriched if m["_days_ago"] <= WINDOW_90D]

    channels_7d = {m.get("channel_id") for m in mentions_7d if m.get("channel_id")}
    channels_30d = {m.get("channel_id") for m in mentions_30d if m.get("channel_id")}

    baselines = [
        float(m.get("view_over_baseline") or 0)
        for m in mentions_30d
        if float(m.get("view_over_baseline") or 0) > 0
    ]
    avg_baseline = sum(baselines) / len(baselines) if baselines else 0
    median_baseline = median(baselines) if baselines else 0
    best_baseline = max(baselines) if baselines else 0
    overperform_count = sum(1 for b in baselines if b >= 1.2)
    underperform_count = sum(1 for b in baselines if b < 1.0)
    hit_ratio = overperform_count / len(baselines) if baselines else 0

    mention_count_7d = len(mentions_7d)
    mention_count_30d = len(mentions_30d)
    mention_count_90d = len(mentions_90d)
    distinct_channels_7d = len(channels_7d)
    distinct_channels_30d = len(channels_30d)

    channel_diversity = distinct_channels_30d / mention_count_30d if mention_count_30d else 0
    recurrence = min(mention_count_90d / 10, 1.0)
    velocity_7d = mention_count_7d / 7
    velocity_30d = mention_count_30d / 30
    acceleration = velocity_7d / velocity_30d if velocity_30d else 0

    stage, stage_reason = _trend_stage(
        mention_count_7d,
        mention_count_30d,
        distinct_channels_30d,
        avg_baseline,
        hit_ratio,
    )
    confidence = _confidence(mention_count_30d, distinct_channels_30d)
    action = _recommended_action(stage, avg_baseline, confidence)

    fmt_dist = _format_distribution(mentions_30d)
    best_format = next(iter(fmt_dist.keys()), "unknown")
    examples = _best_examples(mentions_30d)
    total_views_30d = sum(int(m.get("view_count_at_scan") or m.get("view_count") or 0) for m in mentions_30d)
    freshness = 1.0 if stage in ("seed", "rising", "breakout") else 0.4

    entity_name = entity.get("name") or entity.get("entity_name") or ""
    score = {
        "entity_id": entity.get("id") or entity.get("entity_id"),
        "entity_name": entity_name,
        "entity_type": entity.get("entity_type"),
        "category": entity.get("category"),
        "scored_at": scored_at.isoformat(),
        "mention_count_7d": mention_count_7d,
        "mention_count_30d": mention_count_30d,
        "mention_count_90d": mention_count_90d,
        "distinct_channels_7d": distinct_channels_7d,
        "distinct_channels_30d": distinct_channels_30d,
        "channel_diversity_score": _round(channel_diversity),
        "recurrence_score": _round(recurrence),
        "avg_view_over_baseline": _round(avg_baseline),
        "median_view_over_baseline": _round(median_baseline),
        "best_view_over_baseline": _round(best_baseline),
        "overperform_count": overperform_count,
        "underperform_count": underperform_count,
        "hit_ratio": _round(hit_ratio),
        "total_views_30d": total_views_30d,
        "velocity_7d": _round(velocity_7d),
        "velocity_30d": _round(velocity_30d),
        "acceleration_score": _round(acceleration),
        "trend_stage": stage,
        "stage_reason": stage_reason,
        "peak_risk": _round(1 - hit_ratio if stage in ("mainstream", "fatigue") else 0),
        "saturation_score": _round(min(distinct_channels_30d / 10, 1.0)),
        "freshness_score": _round(freshness),
        "best_format": best_format,
        "format_distribution": fmt_dist,
        "best_examples": examples,
        "brand_heat_score": _round(min((avg_baseline / 3) + (distinct_channels_30d / 10), 1.0)),
        "timing_score": _round(min((velocity_7d * 2) + hit_ratio, 1.0)),
        **_fashion_scores(
            entity, mentions_30d, fmt_dist=fmt_dist, best_format=best_format, avg_baseline=avg_baseline,
            hit_ratio=hit_ratio, distinct_channels_30d=distinct_channels_30d, stage=stage, freshness=freshness,
        ),
        "recommended_action": action,
        "suggested_angle": _suggest_angle(entity_name, best_format, stage),
        "confidence": confidence,
    }

    card = {
        "entity_id": score["entity_id"],
        "entity_name": entity_name,
        "card_date": scored_at.isoformat(),
        "question_type": "entity_reaction",
        "answer_summary": _answer_summary(entity_name, stage, action, avg_baseline, distinct_channels_30d),
        "evidence_bullets": _evidence_bullets(score),
        "caution_bullets": _caution_bullets(score),
        "recommended_content_ideas": _content_ideas(entity_name, best_format, stage),
        "best_examples": examples,
        "confidence": confidence,
    }
    return score, card


def _suggest_angle(entity_name: str, best_format: str, stage: str) -> str:
    if not entity_name:
        return ""
    if best_format in ("styling", "lookbook"):
        return f"{entity_name}를 실제 착장/코디 중심으로 풀기"
    if best_format in ("haul", "buying_guide"):
        return f"{entity_name} 구매 전 체크포인트와 추천 조합"
    if stage == "breakout":
        return f"왜 지금 {entity_name}이 다시 반응하는지 빠르게 해석"
    return f"{entity_name}의 반응 포인트를 관찰 후 기획화"


def _answer_summary(entity_name: str, stage: str, action: str, avg_baseline: float, channels: int) -> str:
    return (
        f"{entity_name}은 현재 {stage} 단계로 판단됩니다. "
        f"최근 30일 {channels}개 채널에서 감지됐고 평균 대비 성과는 {avg_baseline:.2f}배입니다. "
        f"추천 액션은 '{action}'입니다."
    )


def _evidence_bullets(score: dict) -> list[str]:
    return [
        f"최근 30일 {score['mention_count_30d']}회 언급, {score['distinct_channels_30d']}개 채널에서 감지",
        f"평균 view_over_baseline {score['avg_view_over_baseline']:.2f}배, 최고 {score['best_view_over_baseline']:.2f}배",
        f"트렌드 단계: {score['trend_stage']} ({score['stage_reason']})",
        f"주력 포맷 {score['best_format']} | 커머스 신호 {score['commerce_signal']:.2f} | 참여율 {score['engagement_score']:.2f}",
    ]


def _caution_bullets(score: dict) -> list[str]:
    cautions = []
    if score["confidence"] == "low":
        cautions.append("아직 표본이 적어 확정 판단보다 관찰 대상으로 보는 것이 안전함")
    if score["trend_stage"] == "fatigue":
        cautions.append("언급량 대비 성과가 약해 식상함 또는 피로 신호 가능성 있음")
    if score["distinct_channels_30d"] <= 1:
        cautions.append("한 채널 신호에 가까워 시장 전체 반응으로 확대 해석 주의")
    return cautions or ["현재 수집 데이터 기준 큰 주의 신호는 없음"]


def _content_ideas(entity_name: str, best_format: str, stage: str) -> list[str]:
    if not entity_name:
        return []
    if best_format in ("styling", "lookbook"):
        return [
            f"요즘 다시 보이는 {entity_name} 코디",
            f"{entity_name}로 만드는 데일리룩 조합",
            f"{entity_name} 스타일링 성공/실패 포인트",
        ]
    if best_format in ("haul", "buying_guide", "review"):
        return [
            f"{entity_name} 사기 전 체크할 것",
            f"{entity_name} 인기 이유와 추천 아이템",
            f"{entity_name} 실제 구매/착용 후기형 콘텐츠",
        ]
    if stage == "breakout":
        return [
            f"왜 지금 {entity_name}이 뜨는가",
            f"{entity_name} 트렌드 빠르게 따라잡기",
            f"{entity_name}를 우리 스타일로 소화하는 법",
        ]
    return [
        f"{entity_name} 반응 관찰 리포트",
        f"{entity_name} 관련 아이템 비교",
        f"{entity_name}를 다룰 만한 새 각도 찾기",
    ]


def _db():
    return create_client(config.SUPABASE_URL, config.SUPABASE_KEY)


def _load_mentions_for_entities(db, entity_ids: list[int]) -> dict[int, list[dict]]:
    mentions_by_entity = {eid: [] for eid in entity_ids}
    if not entity_ids:
        return mentions_by_entity

    cutoff = (datetime.now(timezone.utc) - timedelta(days=WINDOW_90D + 1)).isoformat()
    rows = []
    for i in range(0, len(entity_ids), 50):
        batch = entity_ids[i:i + 50]
        rows.extend(_read_pages(
            lambda: db.table("entity_mentions").select("*").in_("entity_id", batch)
            .or_(f"published_at.gte.{cutoff},and(published_at.is.null,created_at.gte.{cutoff})")
            .order("entity_id").order("video_id")
        ))

    video_ids = sorted({r.get("video_id") for r in rows if r.get("video_id")})
    videos = {}
    for i in range(0, len(video_ids), 100):
        batch = video_ids[i:i + 100]
        for video in _read_pages(
            lambda: db.table("trend_videos").select(
                "video_id,title,channel_title,view_count,like_count,comment_count,duration_seconds,"
                "description_short,view_over_baseline,video_type"
            ).in_("video_id", batch).order("video_id")
        ):
            videos[video["video_id"]] = video

    for row in rows:
        merged = dict(row)
        video = videos.get(row.get("video_id"), {})
        merged["title"] = video.get("title", merged.get("title", ""))
        merged["channel_title"] = video.get("channel_title", merged.get("channel_title", ""))
        merged["view_over_baseline"] = video.get("view_over_baseline", merged.get("view_over_baseline", 0))
        merged["video_type"] = video.get("video_type") or merged.get("video_type") or ""
        merged["view_count"] = video.get("view_count", merged.get("view_count_at_scan", 0))
        merged["like_count"] = video.get("like_count") or 0
        merged["comment_count"] = video.get("comment_count") or 0
        merged["duration_seconds"] = video.get("duration_seconds") or 0
        merged["description_short"] = video.get("description_short") or ""
        eid = row.get("entity_id")
        if eid in mentions_by_entity:
            mentions_by_entity[eid].append(merged)
    return mentions_by_entity


def _mention_rank(mention: dict) -> tuple[float, int]:
    return float(mention.get("confidence") or 0), int(mention.get("view_count_at_scan") or 0)


def _merge_mentions(group: list[dict], mentions_by_entity: dict[int, list[dict]]) -> list[dict]:
    if len(group) == 1:
        return mentions_by_entity.get(group[0]["id"], [])

    merged = []
    video_indices = {}
    for entity in group:
        for mention in mentions_by_entity.get(entity["id"], []):
            video_id = mention.get("video_id")
            if video_id and video_id in video_indices:
                idx = video_indices[video_id]
                if _mention_rank(mention) > _mention_rank(merged[idx]):
                    merged[idx] = mention
                continue
            if video_id:
                video_indices[video_id] = len(merged)
            merged.append(mention)
    return merged


def run_score(max_entities: int = 500) -> int:
    """B-track 엔티티 점수/답변카드 생성 후 Supabase에 저장."""
    db = _db()
    entities = _read_pages(
        lambda: db.table("trend_entities").select("id,name,entity_type,category")
        .order("last_seen_at", desc=True).order("id"),
        max_rows=max_entities,
    )

    if not entities:
        print("  ℹ️ 점수화할 trend_entities 없음")
        return 0

    entity_ids = [e["id"] for e in entities]
    mentions_by_entity = _load_mentions_for_entities(db, entity_ids)
    groups = {}
    for entity in entities:
        key = canonical_entity_key(entity.get("name", ""))
        groups.setdefault(key or (entity["id"],), []).append(entity)

    scores = []
    cards = []
    now = datetime.now(timezone.utc)
    scored_at = now.astimezone(KST).date()

    for group in groups.values():
        entity = min(group, key=lambda e: (
            -len(mentions_by_entity.get(e["id"], [])),
            not re.search(r"[가-힣ㄱ-ㅎㅏ-ㅣ]", e.get("name") or ""),
            e["id"],
        ))
        mentions = _merge_mentions(group, mentions_by_entity)
        if not mentions:
            continue
        score, card = score_entity(entity, mentions, scored_at=scored_at, now=now)
        scores.append(score)
        cards.append(card)

    if not scores:
        print("  ℹ️ entity_mentions가 있는 엔티티 없음")
        return 0

    for i in range(0, len(scores), 100):
        db.table("trend_entity_scores").upsert(
            scores[i:i + 100],
            on_conflict="entity_id,scored_at",
        ).execute()
    for i in range(0, len(cards), 100):
        db.table("trend_answer_cards").upsert(
            cards[i:i + 100],
            on_conflict="entity_id,card_date,question_type",
        ).execute()

    print(f"✅ B-track 엔티티 점수화 완료: {len(scores)}개")
    top = sorted(scores, key=lambda s: (s["recommended_action"] == "바로 제작", s["avg_view_over_baseline"]), reverse=True)[:10]
    for idx, score in enumerate(top, start=1):
        print(
            f"  {idx}. {score['entity_name']} | {score['trend_stage']} | "
            f"{score['avg_view_over_baseline']:.2f}배 | {score['recommended_action']}"
        )
    return len(scores)
