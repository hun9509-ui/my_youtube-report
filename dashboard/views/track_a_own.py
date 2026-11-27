"""A3 · 자체 채널 — 고은언니 한고은 (롱폼/쇼츠 분리 · 초기 궤적 · 주차별 성장 · AI 분석)."""

import pandas as pd
import streamlit as st

from components.charts import lines
from components.ui import (
    callout,
    empty_state,
    kpi_strip,
    list_block_html,
    page_header,
    pill_html,
    render,
    section,
    sources_footer,
)
from lib.constants import OWN_CHANNEL
from lib.queries import get_own_analysis, get_own_analysis_index, get_own_hourly, get_own_videos, get_own_weekly
from lib.supabase_client import get_client
from lib.transforms import (
    CHECKPOINT_WINDOWS,
    as_dict,
    fmt_date,
    fmt_int,
    fmt_views,
    hourly_checkpoints,
    normalize_list,
    truncate,
    video_id_link,
)

client = get_client()
videos = get_own_videos(client)

page_header(
    OWN_CHANNEL,
    "자체 채널 성과를 롱폼/쇼츠로 분리해 업로드 직후 3·6·12·24시간 궤적, 주차별 성장, AI 댓글 분석을 함께 봅니다.",
    track="A", code="A3",
    meta=[pill_html(f"영상 {len(videos)}편")] if not videos.empty else [],
)
if videos.empty:
    empty_state("own_channel_videos 데이터가 없습니다.", "자체 채널 수집 파이프라인 실행 후 확인하세요.")
    st.stop()

videos = videos.copy()
for c in ("view_count", "like_count", "comment_count"):
    videos[c] = pd.to_numeric(videos[c], errors="coerce")
videos["label"] = videos.apply(
    lambda r: f"{fmt_date(r.get('published_at'), '%y.%m.%d')} · {truncate(r.get('title'), 34)}", axis=1)

FORMAT_OPTIONS = {"전체": None, "롱폼": "longform", "쇼츠": "shorts"}
fmt_choice = st.segmented_control("포맷", list(FORMAT_OPTIONS), default="롱폼", required=True, key="a3_fmt") or "전체"
fmt_value = FORMAT_OPTIONS[fmt_choice]
pool = videos[videos["video_type"] == fmt_value] if fmt_value else videos
if pool.empty:
    empty_state(f"{fmt_choice} 영상이 없습니다.")
    st.stop()

eng = (pool["like_count"].fillna(0) + pool["comment_count"].fillna(0)).sum() / max(pool["view_count"].fillna(0).sum(), 1)
kpi_strip([
    {"label": f"{fmt_choice} 영상", "value": fmt_int(len(pool)),
     "sub": f"추적 중 {fmt_int((pool['tracking_active'] == True).sum())}"},  # noqa: E712
    {"label": "중앙 조회", "value": fmt_views(pool["view_count"].median()), "tone": "A"},
    {"label": "평균 조회", "value": fmt_views(pool["view_count"].mean())},
    {"label": "최고 조회", "value": fmt_views(pool["view_count"].max()),
     "sub": truncate(pool.loc[pool["view_count"].idxmax(), "title"], 24) if pool["view_count"].notna().any() else ""},
    {"label": "참여율", "value": f"{eng * 100:.2f}%", "sub": "(좋아요+댓글)/조회"},
    {"label": "최근 업로드", "value": fmt_date(pool["published_at"].max())},
])

# ── 초기 궤적 ─────────────────────────────────────────────
section("업로드 직후 궤적", "own_channel_hourly_snapshots · 3h / 6h / 12h / 24h 체크포인트", kicker="01")
hourly = get_own_hourly(client)
hourly = hourly[hourly["video_id"].isin(pool["video_id"])] if not hourly.empty else hourly
cp = hourly_checkpoints(hourly)
if cp.empty:
    empty_state("시간별 스냅샷이 없습니다.", "업로드 후 24시간 이내 영상만 시간별로 추적됩니다.")
else:
    cp = cp.merge(pool[["video_id", "label", "published_at", "view_count"]], on="video_id", how="left")
    cp = cp.sort_values("published_at", ascending=False)
    all_cols = [f"h{h}" for h in CHECKPOINT_WINDOWS]
    cp[all_cols] = cp[all_cols].apply(pd.to_numeric, errors="coerce")
    missing = [h[1:] + "h" for h in all_cols if cp[h].isna().all()]
    hcols = [h for h in all_cols if cp[h].notna().any()]
    medians = cp[hcols].median()
    if missing:
        st.caption(f"{', '.join(missing)} 관측 없음 — 시간별 수집이 해당 시점 이후에 시작됩니다 (수집 파이프라인 확인 필요).")
    c1, c2 = st.columns([1.1, 1])
    with c1:
        recent = cp.head(8)
        long = recent.melt(id_vars=["label"], value_vars=hcols, var_name="cp", value_name="조회수").dropna()
        long["시간"] = long["cp"].str[1:].astype(int)
        med = pd.DataFrame({"label": f"{fmt_choice} 중앙값", "시간": [int(h[1:]) for h in hcols],
                            "조회수": medians.values})
        lines(pd.concat([long[["label", "시간", "조회수"]], med.dropna()]), x="시간", y="조회수", color="label",
              x_title="업로드 후 시간", y_title="누적 조회수", height=320)
    with c2:
        table = cp[["label"] + hcols].head(15).copy()
        for h in hcols:
            m = medians.get(h)
            table[f"{h}_idx"] = table[h] / m if m and m == m and m > 0 else float("nan")
        st.dataframe(
            table, hide_index=True, height=360,
            column_config={
                "label": st.column_config.TextColumn("영상", width="medium"),
                **{h: st.column_config.NumberColumn(h[1:] + "h", format="compact") for h in hcols},
                **{f"{h}_idx": st.column_config.NumberColumn(f"{h[1:]}h 지수", format="%.2f×",
                                                             help="같은 포맷 중앙값 대비") for h in hcols},
            },
        )

# ── 주차별 성장 ───────────────────────────────────────────
section("주차별 성장", "own_channel_snapshots · 업로드 후 week_number 별 누적 조회", kicker="02")
weekly = get_own_weekly(client)
weekly = weekly[weekly["video_id"].isin(pool["video_id"])] if not weekly.empty else weekly
if weekly.empty:
    empty_state("주차별 스냅샷이 없습니다.")
else:
    weekly = weekly.merge(pool[["video_id", "label", "published_at"]], on="video_id", how="left")
    recent_ids = pool["video_id"].head(10).tolist()
    w = weekly[weekly["video_id"].isin(recent_ids)].copy()
    w["view_count"] = pd.to_numeric(w["view_count"], errors="coerce")
    w = w.rename(columns={"week_number": "주차", "view_count": "조회수"})
    lines(w[["label", "주차", "조회수"]].dropna(), x="주차", y="조회수", color="label", x_title="업로드 후 주차",
          y_title="누적 조회수", height=320, x_type="O")
    growth = (weekly.assign(view_growth=pd.to_numeric(weekly["view_growth"], errors="coerce"))
              .groupby("week_number")["view_growth"].median().reset_index())
    if not growth.empty:
        st.caption("주차별 중앙 증가분: " + " · ".join(
            f"W{int(r.week_number)} +{fmt_views(r.view_growth)}" for r in growth.itertuples() if r.view_growth == r.view_growth))

# ── 영상 디테일 + AI 분석 ─────────────────────────────────
section("영상별 AI 분석", "own_channel_analysis · 강점 · 보완점 · 시청자 요청", kicker="03")
selected = st.selectbox("영상 선택", pool["video_id"].tolist(),
                        format_func=lambda v: pool.loc[pool["video_id"] == v, "label"].iloc[0], key="a3_video")
row = pool[pool["video_id"] == selected].iloc[0]
analysis = get_own_analysis(client, selected)

c1, c2 = st.columns([1, 2.2])
with c1:
    if row.get("thumbnail_url"):
        st.image(row["thumbnail_url"], width="stretch")
    st.markdown(f"[{truncate(row.get('title'), 60)}]({video_id_link(selected, row.get('video_url'))})")
    kpi_strip([
        {"label": "조회", "value": fmt_views(row.get("view_count"))},
        {"label": "좋아요", "value": fmt_views(row.get("like_count"))},
        {"label": "댓글", "value": fmt_int(row.get("comment_count"))},
    ])
with c2:
    if not analysis:
        empty_state("이 영상의 AI 분석이 아직 없습니다.")
    else:
        pred = analysis.get("predicted_performance")
        if pred:
            callout(f"예측 {pred}", analysis.get("predicted_performance_reason") or "", "info")
        a, b, c = st.columns(3)
        with a:
            render(list_block_html("강점", normalize_list(analysis.get("strengths")), "up", "+", limit=5))
        with b:
            render(list_block_html("보완점", normalize_list(analysis.get("improvement_points")), "warn", "!", limit=5))
        with c:
            render(list_block_html("시청자 요청", normalize_list(analysis.get("next_video_requests")), "A", "→", limit=5))
        a, b = st.columns(2)
        with a:
            render(list_block_html("칭찬 포인트", normalize_list(analysis.get("praise_points")), "info", "♥", limit=4))
        with b:
            render(list_block_html("불만", normalize_list(analysis.get("complaints")), "down", "✕", limit=4))
        sent = as_dict(analysis.get("sentiment"))
        meta_bits = [f"구조 {analysis.get('content_structure') or '—'}", f"제목 {analysis.get('title_pattern') or '—'}",
                     f"재방문 의향 {analysis.get('revisit_intent') or '—'}"]
        if sent:
            meta_bits.append("감성 " + " / ".join(f"{k} {v}" for k, v in sent.items()))
        st.caption(" · ".join(meta_bits))
        if analysis.get("comments_summary"):
            callout("댓글 요약", analysis["comments_summary"], "neutral")

# ── 누적 시청자 요청 ─────────────────────────────────────
index = get_own_analysis_index(client)
if not index.empty:
    idx = index[index["video_id"].isin(pool["video_id"])].sort_values("analysis_date", ascending=False).head(15)
    requests = [r for v in idx["next_video_requests"] for r in normalize_list(v)]
    if requests:
        section("최근 시청자 요청 모음", f"최근 분석 {len(idx)}편의 next_video_requests", kicker="04")
        render(list_block_html("다음 영상으로 원하는 것", requests, "A", limit=12))

sources_footer([
    ("own_channel_videos", "video_type(longform/shorts), view/like/comment_count, tracking_active"),
    ("own_channel_hourly_snapshots", "hours_since_upload, view_count — 3/6/12/24h 창 내 최근접 관측"),
    ("own_channel_snapshots", "week_number, view_count, view_growth"),
    ("own_channel_analysis", "strengths, improvement_points, next_video_requests, praise_points, complaints"),
])
