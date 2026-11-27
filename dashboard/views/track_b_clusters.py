"""B2 · 트렌드 클러스터 & 셀럽 미채택 기회 — trend_clusters · 교차 레이더 · trend_sources · trend_videos."""

from datetime import datetime, timedelta, timezone

import pandas as pd
import streamlit as st

from components.cards import entity_card_html
from components.charts import STAGE_COLORS, TRACK_B, bar_h, scatter_bubble
from components.ui import (
    callout,
    empty_state,
    grid_html,
    kpi_strip,
    page_header,
    pill_html,
    render,
    section,
    sources_footer,
)
from lib.constants import CATEGORY_LABELS, STAGE_META, STAGE_ORDER
from lib.queries import (
    get_b_track_clusters,
    get_b_track_entity_scores,
    get_recent_celeb_titles,
    get_top_trend_videos,
    get_trend_sources,
)
from lib.supabase_client import get_client
from lib.transforms import celeb_unadopted, fmt_date, fmt_int, fmt_rate, parse_ts, split_hints, stage_rank, video_id_link

client = get_client()
clusters = get_b_track_clusters(client)
latest = clusters["computed_at"].max() if not clusters.empty else None

page_header(
    "트렌드 클러스터 · 셀럽 미채택 기회",
    "패션·유행 채널에서 몰리는 주제 클러스터와, 아직 셀럽 채널(Track A)이 다루지 않은 '선점 가능' 엔티티를 교차로 찾습니다.",
    track="B", code="B2",
    meta=[pill_html(f"클러스터 {fmt_date(latest, '%m.%d %H:%M')}", "up" if latest else "muted")],
)

# ── 클러스터 ──────────────────────────────────────────────
section("최신 트렌드 클러스터", "trend_clusters 최신 집계 배치 · 최근 7일 언급/채널/속도", kicker="01")
if clusters.empty:
    empty_state("trend_clusters 데이터가 없습니다.")
else:
    counts = clusters["trend_stage"].value_counts()
    kpi_strip([
        {"label": "클러스터", "value": fmt_int(len(clusters))},
        *[{"label": STAGE_META.get(s, (s, ""))[0], "value": fmt_int(counts.get(s, 0)), "sub": s,
           "tone": STAGE_META.get(s, ("", "neutral"))[1]}
          for s in sorted(counts.index, key=stage_rank)[:4]],
        {"label": "7일 총 조회", "value": f"{clusters['total_views_7d'].sum() / 1e4:,.0f}만"
         if clusters["total_views_7d"].notna().any() else "—"},
    ])
    c1, c2 = st.columns([1.2, 1])
    plot = clusters.copy()
    plot["단계"] = plot["trend_stage"]
    plot["_label"] = plot["cluster_name"].isin(set(plot.nlargest(10, "mention_count_7d")["cluster_name"]))
    domain = [s for s in STAGE_ORDER if s in set(plot["단계"])]
    y_col = "convergence_score" if plot["convergence_score"].notna().any() else "velocity_score"
    with c1:
        scatter_bubble(
            plot, x="distinct_channels_7d", y="mention_count_7d", size="total_views_7d", color="단계",
            x_title="7일 언급 채널 수", y_title="7일 언급량", label="cluster_name", height=380,
            color_domain=domain, color_range=[STAGE_COLORS.get(s, "#A8A397") for s in domain],
            tooltip=["cluster_name", "category", "단계", "mention_count_7d", "distinct_channels_7d",
                     "total_views_7d", "velocity_score", y_col],
        )
    with c2:
        stage_pick = st.pills("단계", domain, selection_mode="multi", default=domain,
                              format_func=lambda s: STAGE_META.get(s, (s, ""))[0], key="b2_stage")
        view = clusters[clusters["trend_stage"].isin(stage_pick or domain)].copy()
        view["category"] = view["category"].map(lambda c: CATEGORY_LABELS.get(c, c))
        st.dataframe(
            view[["cluster_name", "category", "trend_stage", "mention_count_7d", "distinct_channels_7d",
                  "velocity_score", "convergence_score", "fatigue_score", "summary"]],
            hide_index=True, height=330,
            column_config={
                "cluster_name": st.column_config.TextColumn("클러스터", pinned=True),
                "category": "카테고리", "trend_stage": "단계",
                "mention_count_7d": st.column_config.NumberColumn("언급7d", format="%d"),
                "distinct_channels_7d": st.column_config.NumberColumn("채널7d", format="%d"),
                "velocity_score": st.column_config.ProgressColumn("속도", min_value=0, max_value=1, format="%.2f"),
                "convergence_score": st.column_config.ProgressColumn("수렴", min_value=0, max_value=1, format="%.2f"),
                "fatigue_score": st.column_config.ProgressColumn("피로", min_value=0, max_value=1, format="%.2f"),
                "summary": st.column_config.TextColumn("요약", width="large"),
            },
        )

# ── 셀럽 미채택 레이더 ────────────────────────────────────
section("셀럽 미채택 유행 레이더",
        "Track B 다수 채널이 최근 언급 중이지만 최근 Track A 셀럽 영상 제목에는 아직 없는 엔티티", kicker="02")
ents = get_b_track_entity_scores(client)
if ents.empty:
    empty_state("trend_entity_scores 최신 스냅샷이 없습니다.")
else:
    k1, k2, k3 = st.columns([1, 1, 1.6])
    with k1:
        min_ch = st.slider("최소 언급 채널(30일)", 2, 20, 3, key="b2_minch")
    with k2:
        days = st.select_slider("셀럽 제목 비교 기간", [30, 60, 90, 180], value=90, format_func=lambda d: f"최근 {d}일",
                                key="b2_days")
    with k3:
        stage_opts = [s for s in STAGE_ORDER if s in set(ents["trend_stage"].dropna())]
        stages = st.pills("단계", stage_opts, selection_mode="multi",
                          default=[s for s in stage_opts if s in ("breakout", "rising", "mainstream")] or stage_opts,
                          format_func=lambda s: STAGE_META.get(s, (s, ""))[0], key="b2_unadopted_stage")
    titles = get_recent_celeb_titles(client, days=days)
    gap = celeb_unadopted(ents, titles, min_channels=min_ch, stages=stages or stage_opts)
    candidates = ents[ents["trend_stage"].isin(stages or stage_opts) & (ents["distinct_channels_30d"].fillna(0) >= min_ch)]
    kpi_strip([
        {"label": "조건 충족 엔티티", "value": fmt_int(len(candidates))},
        {"label": "셀럽 미채택", "value": fmt_int(len(gap)), "tone": "B"},
        {"label": "미채택 비율", "value": fmt_rate(len(gap) / len(candidates) if len(candidates) else None)},
        {"label": "비교한 셀럽 영상 제목", "value": fmt_int(len(titles)), "sub": f"최근 {days}일 · 자체 채널 제외"},
    ])
    if not titles:
        callout("주의", "비교할 셀럽 영상 제목이 없어 모든 엔티티가 미채택으로 보입니다. videos 수집 상태를 확인하세요.", "warn")
    if gap.empty:
        empty_state("조건에 맞는 미채택 엔티티가 없습니다.", "최소 채널 수를 낮추거나 단계를 넓혀 보세요.")
    else:
        full = gap.merge(ents.drop(columns=[c for c in gap.columns if c != "entity_id"], errors="ignore"),
                         on="entity_id", how="left")
        render('<div style="height:10px"></div>')
        render(grid_html([entity_card_html(r) for _, r in full.head(9).iterrows()], 3))
        with st.expander(f"미채택 엔티티 전체 {len(gap)}개 표로 보기", icon=":material/table:"):
            st.dataframe(
                gap, hide_index=True,
                column_config={
                    "entity_id": None, "entity_name": "엔티티", "category": "카테고리", "trend_stage": "단계",
                    "distinct_channels_30d": st.column_config.NumberColumn("채널30d", format="%d"),
                    "mention_count_30d": st.column_config.NumberColumn("언급30d", format="%d"),
                    "median_view_over_baseline": st.column_config.NumberColumn("중앙 배수", format="%.2f×"),
                    "recommended_action": "실행", "suggested_angle": st.column_config.TextColumn("앵글", width="large"),
                },
            )

# ── 소스 분포 ─────────────────────────────────────────────
section("Track B 수집 소스", "trend_sources active=true", kicker="03")
sources = get_trend_sources(client)
if sources.empty:
    empty_state("활성 trend_sources 가 없습니다.")
else:
    now = datetime.now(timezone.utc)
    fresh = sources["last_collected_at"].map(parse_ts).map(lambda t: t is not None and now - t <= timedelta(hours=36))
    kpi_strip([
        {"label": "활성 소스", "value": fmt_int(len(sources)), "tone": "B"},
        {"label": "36시간 내 수집", "value": fmt_int(fresh.sum()), "sub": fmt_rate(fresh.mean())},
        {"label": "누적 수집 영상", "value": fmt_int(pd.to_numeric(sources["total_collected"], errors="coerce").sum())},
        {"label": "소스 유형", "value": ", ".join(sources["source_type"].dropna().unique()[:3]) or "—"},
    ])
    c1, c2 = st.columns([1, 1.3])
    with c1:
        hints = split_hints(sources["category_hint"]).head(14)
        hints["카테고리"] = hints["hint"].map(lambda h: CATEGORY_LABELS.get(h, h))
        bar_h(hints, "sources", "카테고리", color=TRACK_B, x_title="소스 수", text_format="d",
              tooltip=["hint", "sources"])
    with c2:
        top = sources.assign(total_collected=pd.to_numeric(sources["total_collected"], errors="coerce"),
                             last_collected_at=pd.to_datetime(sources["last_collected_at"], errors="coerce", utc=True))
        st.dataframe(
            top.sort_values("total_collected", ascending=False).head(40)[
                ["source_name", "category_hint", "priority", "total_collected", "last_collected_at"]],
            hide_index=True, height=360,
            column_config={
                "source_name": st.column_config.TextColumn("채널", width="medium"),
                "category_hint": "힌트", "priority": st.column_config.NumberColumn("우선순위", format="%d"),
                "total_collected": st.column_config.NumberColumn("수집", format="%d"),
                "last_collected_at": st.column_config.DatetimeColumn("최근 수집", format="MM.DD HH:mm"),
            },
        )

# ── 고효율 레퍼런스 피드 ──────────────────────────────────
section("고효율 레퍼런스 피드", "trend_videos 최근 30일 · 채널 평균 대비 view_over_baseline 상위", kicker="04")
v1, v2 = st.columns([1, 3])
with v1:
    vtype = st.segmented_control("영상 유형", ["전체", "longform", "shorts"], default="전체", required=True, key="b2_vtype")
feed = get_top_trend_videos(client, days=30, limit=80)
if feed.empty:
    empty_state("최근 30일 trend_videos 가 없습니다.")
else:
    if vtype and vtype != "전체":
        feed = feed[feed["video_type"] == vtype]
    feed = feed.assign(link=feed["video_id"].map(video_id_link),
                       published_at=pd.to_datetime(feed["published_at"], errors="coerce", utc=True))
    st.dataframe(
        feed[["thumbnail_url", "title", "channel_title", "view_over_baseline", "view_count", "channel_avg_views",
              "video_type", "published_at", "link"]],
        hide_index=True, height=560, row_height=54,
        column_config={
            "thumbnail_url": st.column_config.ImageColumn("", width="small"),
            "title": st.column_config.TextColumn("제목", width="large"),
            "channel_title": "채널",
            "view_over_baseline": st.column_config.NumberColumn("기준선 대비", format="%.1f×"),
            "view_count": st.column_config.NumberColumn("조회", format="compact"),
            "channel_avg_views": st.column_config.NumberColumn("채널 평균", format="compact"),
            "video_type": "유형",
            "published_at": st.column_config.DatetimeColumn("업로드", format="MM.DD"),
            "link": st.column_config.LinkColumn("영상", display_text="열기"),
        },
    )

sources_footer([
    ("trend_clusters", "최신 computed_at 배치(24h 창) · mention_count_7d, distinct_channels_7d, velocity/convergence/fatigue"),
    ("trend_entity_scores × videos.title", "최신 scored_at 엔티티명 정규화 매칭 — 최근 N일 셀럽 제목에 미등장"),
    ("trend_sources", "active=true · category_hint, priority, last_collected_at"),
    ("trend_videos", "최근 30일 view_over_baseline desc limit 80 (전수 스캔 없음)"),
])
