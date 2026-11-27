"""홈 · 전략 브리핑 — Track A 바로 기획화 TOP + 자체 채널 + Track B 시그널 + 수집 헬스."""

from datetime import datetime

import pandas as pd
import streamlit as st

from components.cards import entity_card_html, idea_card_html
from components.ui import (
    esc,
    empty_state,
    grid_html,
    kpi_strip,
    list_block_html,
    page_header,
    pill_html,
    render,
    section,
    sources_footer,
)
from lib.constants import DEFAULT_SCORE_VERSION, OWN_CHANNEL
from lib.queries import (
    get_b_track_entity_scores,
    get_collection_health,
    get_own_videos,
    get_strategy_scores,
    get_weekly_market_states,
)
from lib.supabase_client import get_client
from lib.transforms import (
    KST,
    fmt_date,
    fmt_int,
    fmt_views,
    freshness,
    normalize_list,
    to_num,
    top_b_signals,
)

client = get_client()
health = get_collection_health(client)
scores = get_strategy_scores(client, DEFAULT_SCORE_VERSION)
own = get_own_videos(client)
entities = get_b_track_entity_scores(client)
markets = get_weekly_market_states(client, limit=1)
market = markets[0] if markets else None

label, tone = freshness(health.get("video_scores"))
page_header(
    "오늘의 전략 브리핑",
    "셀럽 경쟁 채널(Track A)에서 검증된 기획과 패션·유행(Track B)에서 떠오르는 신호를 한 장에 모았습니다.",
    code=datetime.now(KST).strftime("%Y.%m.%d %a"),
    meta=[pill_html(f"점수 {DEFAULT_SCORE_VERSION} · {label}", tone),
          pill_html(f"B 스냅샷 {fmt_date(health.get('trend_entity_scores'))}",
                    freshness(health.get("trend_entity_scores"))[1])],
)

# ── 시장 한 줄 ─────────────────────────────────────────────
if market:
    opp = to_num(market.get("hangoeun_opportunity_score"))
    render(
        '<div class="ds-hero"><div>'
        f'<div class="ds-eyebrow"><span>{esc(fmt_date(market.get("report_date"), "%Y.%m.%d"))} 주간 시장</span>'
        f'<span class="chip" style="background:var(--info-bg);color:var(--info)">「{esc(market.get("dominant_emotion") or "—")}」</span></div>'
        f'<div class="q">{esc(market.get("market_summary") or "시장 요약 없음")}</div></div>'
        f'<div class="ds-gauge"><div class="n" style="color:var(--up)">{"—" if opp is None else int(opp)}</div>'
        f'<div class="l">한고은 기회지수 · 위안 욕구 {esc(fmt_int(market.get("comfort_content_score")))}</div></div></div>'
    )

# ── Track A · 바로 기획화 TOP ─────────────────────────────
immediate = scores[scores["recommended_action"] == "바로 기획화"] if not scores.empty else pd.DataFrame()
section("지금 바로 기획할 셀럽 레퍼런스", f"Track A · video_scores {DEFAULT_SCORE_VERSION} '바로 기획화' 상위",
        kicker="A", right=f"{len(immediate)}건 대기")
if immediate.empty:
    empty_state("'바로 기획화' 후보가 없습니다.", "A1 기획 후보 큐에서 '각색 후 기획' 후보를 확인하세요.")
else:
    render(grid_html([idea_card_html(r, i + 1, compact=True) for i, (_, r) in enumerate(immediate.head(3).iterrows())], 3))
    st.page_link("views/track_a_scores.py", label="A1 기획 후보 큐 전체 보기", icon=":material/arrow_forward:")

# ── 자체 채널 ─────────────────────────────────────────────
section(f"{OWN_CHANNEL} 현황", "own_channel_videos · 롱폼/쇼츠 분리", kicker="A")
if own.empty:
    empty_state("자체 채널 영상 데이터가 없습니다.")
else:
    own = own.copy()
    own["view_count"] = pd.to_numeric(own["view_count"], errors="coerce")
    long_ = own[own["video_type"] == "longform"]
    shorts = own[own["video_type"] == "shorts"]
    latest = own.iloc[0]
    kpi_strip([
        {"label": "전체 영상", "value": fmt_int(len(own)), "sub": f"추적 중 {fmt_int((own['tracking_active'] == True).sum())}"},  # noqa: E712
        {"label": "롱폼 중앙 조회", "value": fmt_views(long_["view_count"].median() if len(long_) else None),
         "sub": f"{len(long_)}편"},
        {"label": "쇼츠 중앙 조회", "value": fmt_views(shorts["view_count"].median() if len(shorts) else None),
         "sub": f"{len(shorts)}편"},
        {"label": "최근 업로드", "value": fmt_date(latest.get("published_at"), "%m.%d"),
         "sub": str(latest.get("title") or "")[:28]},
        {"label": "최근 업로드 조회", "value": fmt_views(latest.get("view_count")), "sub": latest.get("video_type") or ""},
    ])

# ── Track B 시그널 ────────────────────────────────────────
signals = top_b_signals(entities, n=6)
section("패션·유행 최신 시그널", "Track B · trend_entity_scores 최신 스냅샷의 breakout/rising", kicker="B",
        right=f"스냅샷 {fmt_date(health.get('trend_entity_scores'), '%Y.%m.%d')}")
if signals.empty:
    empty_state("breakout/rising 단계 엔티티가 없습니다.", "B1 트렌드 레이더에서 mainstream/초기 신호를 확인하세요.")
else:
    render(grid_html([entity_card_html(r, compact=True) for _, r in signals.iterrows()], 3))
    st.page_link("views/track_b_radar.py", label="B1 트렌드 레이더 열기", icon=":material/arrow_forward:")

# ── 시장 추천 / 피해야 할 포맷 ───────────────────────────
if market:
    section("이번 주 시장 신호", "weekly_market_state")
    c1, c2, c3 = st.columns(3)
    with c1:
        render(list_block_html("추천 기획 방향", normalize_list(market.get("hangoeun_recommended_topics")), "up", limit=4))
    with c2:
        render(list_block_html("포화된 포맷", normalize_list(market.get("oversaturated_formats")), "down", "✕", limit=4))
    with c3:
        render(list_block_html("시청자 피로 신호", normalize_list(market.get("viewer_fatigue_signals")), "warn", "!", limit=4))

# ── 데이터 헬스 ───────────────────────────────────────────
section("데이터 수집 상태", "테이블별 최신 적재 시각")
HEALTH_LABELS = {
    "videos": "경쟁 영상 수집", "video_snapshots": "일간 스냅샷", "video_scores": "전략 점수",
    "own_hourly": "자체 채널 시간별", "trend_classified": "급상승 분류", "weekly_market_state": "주간 시장",
    "trend_entity_scores": "B 엔티티 점수", "trend_clusters": "B 클러스터",
}
items = []
for key, name in HEALTH_LABELS.items():
    value = health.get(key)
    lab, tn = freshness(value)
    items.append({"label": name, "value": lab, "sub": fmt_date(value, "%m.%d %H:%M") if value and len(str(value)) > 10
                  else fmt_date(value, "%Y.%m.%d"), "tone": tn})
kpi_strip(items[:4])
render('<div style="height:8px"></div>')
kpi_strip(items[4:])

sources_footer([
    ("video_scores × videos", f"score_version={DEFAULT_SCORE_VERSION}, recommended_action='바로 기획화'"),
    ("own_channel_videos", "video_type, view_count, tracking_active"),
    ("trend_entity_scores", "최신 scored_at · trend_stage ∈ {breakout, rising}"),
    ("weekly_market_state", "최신 report_date"),
])
