"""
B-track 엔티티 판단 레이어.

역할:
  1. trend_entities + entity_mentions + trend_videos를 엔티티 단위로 집계
  2. 언급량, 채널 확산, 평균 대비 성과, 트렌드 단계, 추천 액션 계산
  3. trend_entity_scores / trend_answer_cards에 저장

핵심 점수는 API 호출 없이 순수 Python 룰 기반으로 계산한다.
"""
from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timedelta, timezone
from statistics import median
from typing import Any

from supabase import create_client

import config


WINDOW_7D = 7
WINDOW_30D = 30
WINDOW_90D = 90


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
    if mention_count_30d >= 3 and distinct_channels_30d >= 3:
        return "rising", "여러 채널에서 반복 감지되어 상승 신호로 판단"
    if mention_count_30d >= 8 and distinct_channels_30d >= 5:
        return "mainstream", "이미 여러 채널에서 넓게 다뤄지는 대중화 단계"
    return "seed", "아직 제한된 채널에서만 감지되는 초기 신호"


def _recommended_action(stage: str, avg_baseline: float, confidence: str) -> str:
    if stage == "breakout" and confidence in ("medium", "high"):
        return "바로 제작"
    if stage == "rising" and avg_baseline >= 1.3:
        return "기획 후보"
    if stage == "fatigue":
        return "보류"
    return "관찰"


def _format_distribution(mentions_30d: list[dict]) -> dict[str, int]:
    formats = []
    for mention in mentions_30d:
        fmt = mention.get("content_format") or mention.get("format") or "unknown"
        formats.append(str(fmt))
    return dict(Counter(formats).most_common())


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
        "engagement_score": 0,
        "velocity_7d": _round(velocity_7d),
        "velocity_30d": _round(velocity_30d),
        "acceleration_score": _round(acceleration),
        "trend_stage": stage,
        "stage_reason": stage_reason,
        "peak_risk": _round(1 - hit_ratio if stage in ("mainstream", "fatigue") else 0),
        "saturation_score": _round(min(distinct_channels_30d / 10, 1.0)),
        "freshness_score": _round(1.0 if stage in ("seed", "rising", "breakout") else 0.4),
        "best_format": best_format,
        "format_distribution": fmt_dist,
        "best_examples": examples,
        "commerce_signal": 0,
        "brand_heat_score": _round(min((avg_baseline / 3) + (distinct_channels_30d / 10), 1.0)),
        "item_heat_score": 0,
        "shopping_intent_score": 0,
        "ppl_potential": 0,
        "channel_fit_score": 0,
        "timing_score": _round(min((velocity_7d * 2) + hit_ratio, 1.0)),
        "production_difficulty": 0,
        "originality_room": 0,
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

    rows = []
    for i in range(0, len(entity_ids), 100):
        batch = entity_ids[i:i + 100]
        resp = db.table("entity_mentions").select("*").in_("entity_id", batch).execute()
        rows.extend(resp.data or [])

    video_ids = sorted({r.get("video_id") for r in rows if r.get("video_id")})
    videos = {}
    for i in range(0, len(video_ids), 100):
        batch = video_ids[i:i + 100]
        resp = db.table("trend_videos").select(
            "video_id,title,channel_title,view_count,view_over_baseline,video_type"
        ).in_("video_id", batch).execute()
        for video in resp.data or []:
            videos[video["video_id"]] = video

    for row in rows:
        merged = dict(row)
        video = videos.get(row.get("video_id"), {})
        merged["title"] = video.get("title", merged.get("title", ""))
        merged["channel_title"] = video.get("channel_title", merged.get("channel_title", ""))
        merged["view_over_baseline"] = video.get("view_over_baseline", merged.get("view_over_baseline", 0))
        merged["content_format"] = merged.get("content_format") or video.get("video_type") or "unknown"
        merged["view_count"] = video.get("view_count", merged.get("view_count_at_scan", 0))
        eid = row.get("entity_id")
        if eid in mentions_by_entity:
            mentions_by_entity[eid].append(merged)
    return mentions_by_entity


def run_score(max_entities: int = 500) -> int:
    """B-track 엔티티 점수/답변카드 생성 후 Supabase에 저장."""
    db = _db()
    entities = db.table("trend_entities").select(
        "id,name,entity_type,category"
    ).order("last_seen_at", desc=True).limit(max_entities).execute().data or []

    if not entities:
        print("  ℹ️ 점수화할 trend_entities 없음")
        return 0

    entity_ids = [e["id"] for e in entities]
    mentions_by_entity = _load_mentions_for_entities(db, entity_ids)
    scores = []
    cards = []

    for entity in entities:
        mentions = mentions_by_entity.get(entity["id"], [])
        if not mentions:
            continue
        score, card = score_entity(entity, mentions)
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
