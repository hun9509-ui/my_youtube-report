"""A4 · 주간 시장 리포트 & 스냅샷 — weekly_market_state · weekly_reports · video_snapshots · trend_classified."""

import pandas as pd
import streamlit as st

from components.charts import stacked_bar
from components.ui import (
    empty_state,
    esc,
    kpi_strip,
    list_block_html,
    page_header,
    render,
    section,
    sources_footer,
)
from lib.constants import GROWTH_PATTERN_META
from lib.queries import get_latest_snapshots, get_trend_classified, get_weekly_market_states, get_weekly_reports
from lib.supabase_client import get_client
from lib.transforms import exclude_own_channel, fmt_date, fmt_int, normalize_list, to_num, video_id_link

client = get_client()
markets = get_weekly_market_states(client, limit=12)
reports = get_weekly_reports(client, limit=12)

page_header(
    "주간 시장 리포트 · 스냅샷",
    "주간 시장 상태와 리포트, 경쟁 영상 성장 패턴, 트랙별 급상승 알림을 한 흐름으로 읽습니다.",
    track="A", code="A4",
)

# ── 시장 상태 ─────────────────────────────────────────────
section("주간 시장 상태", "weekly_market_state", kicker="01")
if not markets:
    empty_state("weekly_market_state 데이터가 없습니다.")
else:
    dates = [m.get("report_date") for m in markets]
    pick = st.selectbox("리포트 주차", dates, format_func=lambda d: fmt_date(d, "%Y.%m.%d 주"), key="a4_week")
    market = next(m for m in markets if m.get("report_date") == pick)
    opp, comfort = to_num(market.get("hangoeun_opportunity_score")), to_num(market.get("comfort_content_score"))
    render(
        '<div class="ds-hero"><div>'
        f'<div class="ds-eyebrow"><span class="chip" style="background:var(--info-bg);color:var(--info)">'
        f'지배 감정 「{esc(market.get("dominant_emotion") or "—")}」</span></div>'
        f'<div class="q">{esc(market.get("market_summary") or "시장 요약 없음")}</div></div>'
        f'<div style="display:flex;gap:26px"><div class="ds-gauge"><div class="n" style="color:var(--up)">'
        f'{"—" if opp is None else int(opp)}</div><div class="l">한고은 기회지수</div></div>'
        f'<div class="ds-gauge"><div class="n">{"—" if comfort is None else int(comfort)}</div>'
        f'<div class="l">위안 콘텐츠 욕구</div></div></div></div>'
    )
    if len(markets) > 1:
        trend = pd.DataFrame([{"주차": m.get("report_date"), "기회지수": to_num(m.get("hangoeun_opportunity_score")),
                               "위안 욕구": to_num(m.get("comfort_content_score"))} for m in markets])
        st.caption("최근 주차 추이 — " + " · ".join(
            f"{fmt_date(r['주차'])} 기회 {fmt_int(r['기회지수'])}" for _, r in trend.iloc[::-1].iterrows()))
    render('<div style="height:10px"></div>')
    c1, c2, c3 = st.columns(3)
    with c1:
        render(list_block_html("상승 토픽", normalize_list(market.get("rising_topics")), "up", "▲"))
    with c2:
        render(list_block_html("하락 토픽", normalize_list(market.get("declining_topics")), "down", "▼"))
    with c3:
        render(list_block_html("시청자 피로 신호", normalize_list(market.get("viewer_fatigue_signals")), "warn", "!"))
    render('<div style="height:10px"></div>')
    c1, c2, c3 = st.columns(3)
    with c1:
        render(list_block_html("떠오르는 포맷", normalize_list(market.get("emerging_formats")), "info", "+"))
    with c2:
        render(list_block_html("포화 포맷", normalize_list(market.get("oversaturated_formats")), "muted", "✕"))
    with c3:
        render(list_block_html("한고은 추천 토픽", normalize_list(market.get("hangoeun_recommended_topics")), "A"))
    gaps = normalize_list(market.get("competitive_gap"))
    if gaps:
        render('<div style="height:10px"></div>')
        render(list_block_html("경쟁 공백 (우리만 할 수 있는 것)", gaps, "B", "◆", limit=6))

# ── 주간 리포트 ───────────────────────────────────────────
section("주간 리포트", "weekly_reports", kicker="02")
if not reports:
    empty_state("weekly_reports 데이터가 없습니다.")
else:
    rpick = st.selectbox("리포트", [r.get("report_date") for r in reports],
                         format_func=lambda d: fmt_date(d, "%Y.%m.%d 리포트"), key="a4_report")
    rep = next(r for r in reports if r.get("report_date") == rpick)
    if rep.get("week_summary"):
        render(f'<div class="ds-hero" style="grid-template-columns:1fr"><div class="q">{esc(rep["week_summary"])}</div></div>')
        render('<div style="height:10px"></div>')
    c1, c2 = st.columns(2)
    with c1:
        render(list_block_html("한고은 실행 인사이트", normalize_list(rep.get("actionable_insights_for_hangoeun")), "A", limit=6))
    with c2:
        render(list_block_html("다음 영상 제안", normalize_list(rep.get("next_video_suggestions")), "up", limit=6))
    render('<div style="height:10px"></div>')
    c1, c2, c3 = st.columns(3)
    with c1:
        render(list_block_html("성과 상위 채널", normalize_list(rep.get("top_performing_channels")), "neutral", limit=4))
    with c2:
        render(list_block_html("포맷 트렌드", normalize_list(rep.get("format_trends")), "info", limit=4))
    with c3:
        render(list_block_html("PPL 관찰", normalize_list(rep.get("ppl_observations")), "warn", limit=4))

# ── 스냅샷 성장 패턴 ──────────────────────────────────────
section("경쟁 영상 성장 패턴", "video_snapshots 최근 14일 · 영상별 최신 growth_pattern", kicker="03")
snaps = exclude_own_channel(get_latest_snapshots(client, days=14))
if snaps.empty or "growth_pattern" not in snaps.columns:
    empty_state("video_snapshots 데이터가 없습니다.")
else:
    s = snaps[snaps["growth_pattern"].notna()].copy()
    if s.empty:
        empty_state("growth_pattern 이 기록된 스냅샷이 없습니다.")
    else:
        counts = s["growth_pattern"].value_counts()
        kpi_strip([{"label": GROWTH_PATTERN_META.get(k, (k, ""))[0], "value": fmt_int(v),
                    "tone": GROWTH_PATTERN_META.get(k, ("", "neutral"))[1], "sub": k} for k, v in counts.items()])
        render('<div style="height:10px"></div>')
        dist = s.groupby(["channel_title", "growth_pattern"]).size().reset_index(name="n")
        dist["패턴"] = dist["growth_pattern"].map(lambda k: GROWTH_PATTERN_META.get(k, (k, ""))[0])
        stacked_bar(dist, x="n", y="channel_title", color="패턴", normalize=True, x_title="채널 내 비중",
                    color_domain=["에버그린", "롱테일", "꾸준", "급감"],
                    color_range=["#3F8A5C", "#5868A8", "#A8A397", "#B8442F"])

# ── 급상승 알림 ───────────────────────────────────────────
section("트랙별 급상승 알림", "trend_classified 최근 7일 · A=셀럽형 · B=패션/유행형 · IRREGULAR=이상 급등", kicker="04")
tc = get_trend_classified(client, days=7)
if tc.empty:
    empty_state("최근 7일 급상승 분류가 없습니다.")
else:
    tc = tc.copy()
    tc["link"] = tc["video_id"].map(video_id_link)
    tc["analysis_date"] = pd.to_datetime(tc["analysis_date"], errors="coerce")
    tc["keywords"] = tc["matched_keywords"].map(lambda v: ", ".join(normalize_list(v))[:80]) \
        if "matched_keywords" in tc else ""
    tc["reasons"] = tc["irregular_reasons"].map(lambda v: " / ".join(normalize_list(v))[:120]) \
        if "irregular_reasons" in tc else ""
    tracks = [("A", "Track A · 셀럽"), ("B", "Track B · 패션/유행"), ("IRREGULAR", "이레귤러")]
    tabs = st.tabs([f"{name} · {int((tc['track'] == key).sum())}" for key, name in tracks])
    for tab, (key, _) in zip(tabs, tracks):
        with tab:
            sub = tc[tc["track"] == key].sort_values(["analysis_date", "view_count"], ascending=False)
            if sub.empty:
                empty_state("해당 트랙 알림 없음")
                continue
            cols = ["analysis_date", "title", "channel_title", "view_count",
                    "track_score_a" if key != "B" else "track_score_b",
                    "reasons" if key == "IRREGULAR" else "keywords", "link"]
            st.dataframe(
                sub[cols].drop_duplicates(subset=["title", "channel_title"]).head(60), hide_index=True, height=420,
                column_config={
                    "analysis_date": st.column_config.DateColumn("감지일", format="MM.DD"),
                    "title": st.column_config.TextColumn("제목", width="large"),
                    "channel_title": "채널",
                    "view_count": st.column_config.NumberColumn("조회", format="compact"),
                    "track_score_a": st.column_config.NumberColumn("A 점수", format="%.0f"),
                    "track_score_b": st.column_config.NumberColumn("B 점수", format="%.0f"),
                    "keywords": "매칭 키워드", "reasons": "이레귤러 사유",
                    "link": st.column_config.LinkColumn("영상", display_text="열기"),
                },
            )

sources_footer([
    ("weekly_market_state", "dominant_emotion, rising/declining_topics, emerging/oversaturated_formats, hangoeun_opportunity_score"),
    ("weekly_reports", "week_summary, actionable_insights_for_hangoeun, next_video_suggestions"),
    ("video_snapshots", "최근 14일 영상별 최신 growth_pattern — 자체 채널 제외"),
    ("trend_classified", "최근 7일 track A/B/IRREGULAR"),
])
