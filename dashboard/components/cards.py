"""도메인 카드 — Track A 기획 후보 / Track B 엔티티."""

from __future__ import annotations

from components.ui import (
    badge_html,
    badges_html,
    esc,
    link_html,
    meters_html,
    video_url,
)
from lib.constants import (
    ACTION_META,
    B_ACTION_META,
    CATEGORY_LABELS,
    FORMAT_LABELS,
    LIFESPAN_META,
    STAGE_META,
    SUBSCORES,
)
from lib.transforms import (
    DASH,
    fmt_date,
    fmt_int,
    fmt_ratio,
    fmt_views,
    score_signal_badges,
    to_num,
    truncate,
)


def _get(row, key, default=None):
    try:
        value = row.get(key, default)
    except AttributeError:
        return default
    if value is None:
        return default
    if isinstance(value, float) and value != value:  # NaN
        return default
    return value


def idea_card_html(row, rank: int | None = None, *, compact: bool = False) -> str:
    """Track A 기획 후보 카드 (video_scores 1행)."""
    action = _get(row, "recommended_action", "")
    meta = ACTION_META.get(action, {"short": action or DASH, "tone": "neutral"})
    lifespan = LIFESPAN_META.get(_get(row, "content_lifespan_type"))
    top_badges = [(meta["short"], meta["tone"])]
    if lifespan:
        top_badges.append(lifespan)
    signals = score_signal_badges(row)
    url = video_url(_get(row, "video_id"), _get(row, "video_url"))
    priority = to_num(_get(row, "priority_score"))
    meters = [(label, _get(row, key), key == "risk_score") for key, label in SUBSCORES]
    reason = _get(row, "strategy_reason", "")
    rank_html = f'<span class="ds-rank">#{rank:02d}</span>' if rank else ""
    sub = " · ".join(x for x in (
        esc(_get(row, "channel_title", DASH)),
        f"조회 {fmt_views(_get(row, 'view_count'))}",
        fmt_date(_get(row, "published_at"), "%y.%m.%d"),
    ) if x)
    return (
        f'<div class="ds-card accent" style="--accent:var(--track-a)">'
        f'<div class="row"><div style="display:flex;gap:8px;align-items:center">{rank_html}'
        f'{badges_html(top_badges)}</div>'
        f'<div class="ds-big">{"—" if priority is None else int(round(priority))}<small>우선순위</small></div></div>'
        f'<div><div class="ds-ctitle">{link_html(url, _get(row, "title", "제목 없음"), 70)}</div>'
        f'<div class="ds-csub">{sub}</div></div>'
        + ("" if compact else meters_html(meters))
        + (badges_html(signals) if signals else "")
        + (f'<div class="ds-reason">{esc(truncate(reason, 160 if compact else 260))}</div>' if reason else "")
        + "</div>"
    )


def stage_badge(stage) -> str:
    label, tone = STAGE_META.get(stage, (stage or DASH, "neutral"))
    return badge_html(label, tone)


def entity_card_html(row, *, compact: bool = False) -> str:
    """Track B 엔티티 카드 (trend_entity_scores 1행)."""
    stage = _get(row, "trend_stage")
    label, tone = STAGE_META.get(stage, (stage or DASH, "neutral"))
    action = _get(row, "recommended_action", DASH)
    fmt = _get(row, "best_format")
    cat = _get(row, "category")
    badges = [(label, tone), (action, B_ACTION_META.get(action, "neutral"))]
    if fmt:
        badges.append((FORMAT_LABELS.get(fmt, fmt), "B"))
    vob = _get(row, "median_view_over_baseline")
    meters = [
        ("커머스", _get(row, "commerce_signal")),
        ("구매 의도", _get(row, "shopping_intent_score")),
        ("PPL", _get(row, "ppl_potential")),
        ("독창 여지", _get(row, "originality_room")),
    ]
    angle = _get(row, "suggested_angle", "")
    return (
        f'<div class="ds-card accent" style="--accent:var(--track-b)">'
        f'<div class="row">{badges_html(badges)}'
        f'<div class="ds-big">{fmt_ratio(vob, 2)}<small>중앙 배수</small></div></div>'
        f'<div><div class="ds-ctitle">{esc(truncate(_get(row, "entity_name", DASH), 40))}</div>'
        f'<div class="ds-csub">{esc(CATEGORY_LABELS.get(cat, cat or DASH))} · '
        f'30일 {fmt_int(_get(row, "distinct_channels_30d"))}채널 · '
        f'{fmt_int(_get(row, "mention_count_30d"))}회 언급</div></div>'
        + ("" if compact else meters_html(meters, max_value=1, fmt="float"))
        + (f'<div class="ds-reason">{esc(truncate(angle, 140))}</div>' if angle else "")
        + "</div>"
    )
