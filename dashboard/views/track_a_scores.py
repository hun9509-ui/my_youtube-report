"""A1 · 기획 후보 큐 — video_scores(v2.0 기본, v1.2 비교) × 경쟁 16채널."""

import pandas as pd
import streamlit as st

from components.cards import idea_card_html
from components.charts import stacked_bar
from components.ui import (
    empty_state,
    kpi_strip,
    page_header,
    render,
    sources_footer,
)
from lib.constants import ACTION_META, ACTION_ORDER, COMPARE_SCORE_VERSIONS, DEFAULT_SCORE_VERSION, SUBSCORES
from lib.queries import get_score_versions, get_strategy_scores
from lib.supabase_client import get_client
from lib.transforms import fmt_date, fmt_int, parse_rule_trace, video_id_link

client = get_client()
available = get_score_versions(client)
options = [v for v in COMPARE_SCORE_VERSIONS if v in available] or [DEFAULT_SCORE_VERSION]
options += [v for v in available if v not in options]

page_header(
    "기획 후보 큐",
    "경쟁 셀럽 채널 영상을 한고은 채널 관점으로 채점한 결과입니다. 실행 등급별로 나누고, 근거(rule_trace)까지 펼쳐 봅니다.",
    track="A", code="A1",
)

# ── 컨트롤 ────────────────────────────────────────────────
c1, c2, c3 = st.columns([1.1, 2.6, 1.3])
with c1:
    version = st.segmented_control(
        "점수 버전", options, default=DEFAULT_SCORE_VERSION if DEFAULT_SCORE_VERSION in options else options[0],
        required=True, key="a1_version",
    ) or DEFAULT_SCORE_VERSION
scores = get_strategy_scores(client, version)
if scores.empty:
    empty_state(f"{version} 점수 데이터가 없습니다.", "strategy_scorer 파이프라인 실행 후 다시 확인하세요.")
    st.stop()

channels = sorted(scores["channel_title"].dropna().unique().tolist()) if "channel_title" in scores else []
with c2:
    picked = st.multiselect("경쟁 채널", channels, placeholder=f"전체 {len(channels)}개 채널", key="a1_channels")
with c3:
    query = st.text_input("제목 검색", placeholder="키워드", key="a1_q")

d1, d2, d3 = st.columns([1.1, 2.6, 1.3])
with d1:
    view_mode = st.segmented_control("보기", ["카드", "표"], default="카드", required=True, key="a1_mode")
with d2:
    sort_key = st.selectbox(
        "정렬", ["priority_score"] + [k for k, _ in SUBSCORES],
        format_func=lambda k: {"priority_score": "우선순위"}.get(k, dict(SUBSCORES).get(k, k)), key="a1_sort",
    )
with d3:
    limit = st.number_input("등급별 표시", min_value=6, max_value=120, value=18, step=6, key="a1_limit")

df = scores.copy()
if picked:
    df = df[df["channel_title"].isin(picked)]
if query:
    df = df[df["title"].fillna("").str.contains(query, case=False, regex=False)]
df = df.sort_values(sort_key, ascending=(sort_key == "risk_score"), na_position="last")

# ── 요약 ──────────────────────────────────────────────────
counts = df["recommended_action"].value_counts()
kpi_strip([
    {"label": "후보 영상", "value": fmt_int(len(df)), "sub": f"{version} · {len(picked) or len(channels)}개 채널"},
    *[{"label": a, "value": fmt_int(counts.get(a, 0)), "tone": ACTION_META[a]["tone"],
       "sub": f"{(counts.get(a, 0) / len(df) * 100):.0f}%" if len(df) else "—"} for a in ACTION_ORDER],
    {"label": "최근 채점", "value": fmt_date(df["scored_at"].max() if "scored_at" in df and len(df) else None)},
])

with st.expander("채널별 실행 등급 분포", icon=":material/stacked_bar_chart:"):
    dist = df.groupby(["channel_title", "recommended_action"]).size().reset_index(name="n") if len(df) else pd.DataFrame()
    stacked_bar(dist, x="n", y="channel_title", color="recommended_action",
                color_domain=list(ACTION_ORDER), color_range=["#3F8A5C", "#5868A8", "#B9861D", "#CFCABD"],
                x_title="후보 수")

# ── 등급 탭 ──────────────────────────────────────────────
tabs = st.tabs([f"{a} · {counts.get(a, 0)}" for a in ACTION_ORDER])
for tab, action in zip(tabs, ACTION_ORDER):
    with tab:
        group = df[df["recommended_action"] == action]
        if group.empty:
            empty_state(f"'{action}' 후보가 없습니다.")
            continue
        shown = group.head(int(limit))
        if view_mode == "표":
            table = shown.assign(link=shown.apply(lambda r: video_id_link(r.get("video_id"), r.get("video_url")), axis=1))
            cols = ["title", "channel_title", "priority_score"] + [k for k, _ in SUBSCORES] + \
                   ["content_lifespan_type", "strategy_reason", "link"]
            st.dataframe(
                table[[c for c in cols if c in table.columns]], hide_index=True, height=min(640, 38 + 35 * len(table)),
                column_config={
                    "title": st.column_config.TextColumn("제목", width="large"),
                    "channel_title": st.column_config.TextColumn("채널"),
                    "priority_score": st.column_config.ProgressColumn("우선순위", min_value=0, max_value=100, format="%d"),
                    **{k: st.column_config.ProgressColumn(lbl, min_value=0, max_value=100, format="%d") for k, lbl in SUBSCORES},
                    "content_lifespan_type": st.column_config.TextColumn("수명"),
                    "strategy_reason": st.column_config.TextColumn("판단 근거", width="large"),
                    "link": st.column_config.LinkColumn("영상", display_text="열기"),
                },
            )
        else:
            rows = list(shown.iterrows())
            for start in range(0, len(rows), 3):
                cols = st.columns(3)
                for col, (i, (_, row)) in zip(cols, enumerate(rows[start:start + 3], start=start + 1)):
                    with col:
                        render(idea_card_html(row, i))
                        trace = parse_rule_trace(row.get("rule_trace"))
                        if trace:
                            with st.popover("점수 근거 rule_trace", icon=":material/rule:", width="stretch",
                                            key=f"a1_tr_{version}_{action}_{row.get('video_id')}_{i}"):
                                for key, rules in trace:
                                    st.markdown(f"**{key}**")
                                    st.markdown("\n".join(f"- `{r}`" for r in rules))
        if len(group) > len(shown):
            st.caption(f"상위 {len(shown)}개 표시 · 전체 {len(group)}개 (표시 개수를 늘리거나 필터를 좁히세요)")

sources_footer([
    ("video_scores", f"score_version={version} 단일 필터 · priority/novelty/fit/execution/repeatability/risk · rule_trace"),
    ("videos", "title, channel_title, view_count — 자체 채널 제외"),
    ("thumbnail_analysis", "couple_present, face_count, main_emotion, food_present"),
    ("initial/daily_analysis", "deepseek_raw→emotion_temperature (envy·aspiration·fatigue·cringe), ad_boost_suspected"),
])
