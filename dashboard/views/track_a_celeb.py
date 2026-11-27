"""A2 · 셀럽 경쟁 채널 & 히트 패턴 — 16채널 리더보드 + 실증 히트 드라이버."""

import altair as alt
import pandas as pd
import streamlit as st

from components.charts import TRACK_A, _theme, bar_h, lift_chart
from components.ui import (
    callout,
    empty_state,
    kpi_strip,
    page_header,
    section,
    sources_footer,
)
from lib.constants import DEFAULT_SCORE_VERSION, EMOTION_LABELS, EMOTIONS, HIT_MULTIPLIER, THUMBNAIL_SIGNALS
from lib.queries import get_celeb_hit_dataset, get_strategy_scores
from lib.supabase_client import get_client
from lib.transforms import (
    add_view_ratio,
    baseline_hit_rate,
    channel_leaderboard,
    emotion_lift,
    fmt_date,
    fmt_int,
    fmt_rate,
    lift_table,
    signal_lift,
    to_num,
    video_id_link,
)

client = get_client()
raw = get_celeb_hit_dataset(client)

page_header(
    "셀럽 경쟁 채널 · 히트 패턴",
    f"채널 중앙값 대비 {HIT_MULTIPLIER:.1f}배 이상을 '히트'로 정의하고, 어떤 카테고리·구조·제목·썸네일·댓글 감정이 히트율을 끌어올리는지 봅니다.",
    track="A", code="A2",
)
if raw.empty:
    empty_state("경쟁 채널 영상 데이터가 없습니다.", "videos 수집 파이프라인 실행 후 확인하세요.")
    st.stop()

data = add_view_ratio(raw)
channels = sorted(data["channel_title"].dropna().unique().tolist())
f1, f2 = st.columns([3, 1])
with f1:
    picked = st.multiselect("채널", channels, placeholder=f"전체 {len(channels)}개 채널", key="a2_channels")
with f2:
    min_n = st.slider("최소 표본", 2, 20, 5, key="a2_min_n", help="표본이 이보다 적은 그룹은 숨깁니다.")
pool = data[data["channel_title"].isin(picked)] if picked else data
base = baseline_hit_rate(pool)

kpi_strip([
    {"label": "경쟁 채널", "value": fmt_int(pool["channel_title"].nunique())},
    {"label": "분석 영상", "value": fmt_int(len(pool))},
    {"label": f"{HIT_MULTIPLIER:.1f}배+ 히트", "value": fmt_int(pool["is_hit"].sum()), "tone": "A"},
    {"label": "기준 히트율", "value": fmt_rate(base), "sub": "채널 중앙값 대비"},
    {"label": "AI 분석 커버리지", "value": fmt_rate(pool["category"].notna().mean() if "category" in pool and len(pool) else None)},
    {"label": "최근 업로드", "value": fmt_date(pool["published_at"].max() if len(pool) else None)},
])

# ── 리더보드 ──────────────────────────────────────────────
section("채널 리더보드", "중앙값 · 평균 조회수와 2.0배+ 히트율", kicker="01")
board = channel_leaderboard(pool)
if board.empty:
    empty_state()
else:
    board["latest_upload"] = pd.to_datetime(board["latest_upload"], errors="coerce", utc=True)
    c1, c2 = st.columns([1, 1.25])
    with c1:
        bar_h(board, "median_views", "channel_title", color=TRACK_A, x_title="중앙 조회수", x_format="~s",
              tooltip=["channel_title", "videos", "median_views", "hit_rate"], text_format=".2s")
    with c2:
        st.dataframe(
            board, hide_index=True, height=min(640, 38 + 35 * len(board)),
            column_config={
                "channel_title": st.column_config.TextColumn("채널", width="medium"),
                "videos": st.column_config.NumberColumn("영상", format="%d"),
                "median_views": st.column_config.NumberColumn("중앙 조회", format="compact"),
                "mean_views": st.column_config.NumberColumn("평균 조회", format="compact"),
                "hit_count": st.column_config.NumberColumn("히트", format="%d"),
                "hit_rate": st.column_config.ProgressColumn("히트율", min_value=0, max_value=1, format="percent"),
                "latest_upload": st.column_config.DatetimeColumn("최근 업로드", format="YY.MM.DD"),
            },
        )

# ── 히트 패턴 ─────────────────────────────────────────────
section("실증 히트 패턴", f"그룹별 {HIT_MULTIPLIER:.1f}배+ 히트율 · 붉은 점선 = 기준 히트율 {fmt_rate(base)}", kicker="02")
tabs = st.tabs(["카테고리", "콘텐츠 구조", "제목 패턴", "썸네일 비주얼", "댓글 감정온도", "광고 부스팅 · PPL"])

for tab, feature, note in zip(tabs[:3], ("category", "content_structure", "title_pattern"),
                              ("Gemini 분류 카테고리", "토크형·스토리텔링형·브이로그형…", "단정형·감정형·숫자형…")):
    with tab:
        table = lift_table(pool, feature, min_samples=min_n)
        st.caption(note)
        if table.empty:
            empty_state("표본이 충분한 그룹이 없습니다.", "최소 표본을 낮춰 보세요.")
        else:
            lift_chart(table.head(14), baseline=base)
            best = table.iloc[0]
            lift = to_num(best["lift"])
            lift_txt = f", 기준 대비 {lift:.1f}배" if lift is not None else ""
            callout("TOP", f"'{best['value']}' — 히트율 {fmt_rate(best['hit_rate'])} (표본 {int(best['n'])}{lift_txt})", "up")

with tabs[3]:
    st.caption("thumbnail_analysis — 커플 / 2인 얼굴 / 놀람 감정 / 음식 노출")
    sig = signal_lift(pool, THUMBNAIL_SIGNALS)
    if sig.empty:
        empty_state("썸네일 분석 데이터가 없습니다.")
    else:
        sig = sig[sig["n"] >= min_n]
        lift_chart(sig.rename(columns={"label": "value"}), baseline=baseline_hit_rate(
            pool[pool.get("has_thumbnail", pd.Series(False, index=pool.index)) == True]),  # noqa: E712
            color=TRACK_A)
        st.caption("음식 노출은 v2.0 스코어러에서 신선도 감점(-12) 요인입니다 — 히트율과 함께 비교하세요.")

with tabs[4]:
    st.caption("initial/daily_analysis.deepseek_raw → emotion_temperature · 고온(≥20°) vs 저온 그룹")
    emo = emotion_lift(pool, EMOTIONS)
    if emo.empty:
        empty_state("감정온도 데이터가 없습니다.")
    else:
        long = pd.concat([
            emo.assign(그룹="고온 ≥20°", hit_rate=emo["high_hit_rate"], n=emo["high_n"]),
            emo.assign(그룹="저온 <20°", hit_rate=emo["low_hit_rate"], n=emo["low_n"]),
        ])
        long["감정"] = long["emotion"].map(lambda e: f"{EMOTION_LABELS.get(e, e)} ({e})")
        chart = alt.Chart(long.dropna(subset=["hit_rate"])).mark_bar(cornerRadiusEnd=3).encode(
            y=alt.Y("감정:N", title=None), yOffset="그룹:N",
            x=alt.X("hit_rate:Q", title="히트율", axis=alt.Axis(format="%")),
            color=alt.Color("그룹:N", scale=alt.Scale(range=[TRACK_A, "#CFCABD"]), title=None),
            tooltip=["감정", "그룹", alt.Tooltip("hit_rate:Q", format=".0%"), "n"],
        ).properties(height=260)
        st.altair_chart(_theme(chart), width="stretch")
        st.dataframe(
            emo.assign(emotion=emo["emotion"].map(lambda e: EMOTION_LABELS.get(e, e))), hide_index=True,
            column_config={
                "emotion": "감정", "high_n": "고온 표본", "low_n": "저온 표본",
                "high_hit_rate": st.column_config.NumberColumn("고온 히트율", format="percent"),
                "low_hit_rate": st.column_config.NumberColumn("저온 히트율", format="percent"),
                "avg_temp": st.column_config.NumberColumn("평균 온도", format="%.1f°"),
            },
        )

with tabs[5]:
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**광고 부스팅 의심 채널**")
        if "ad_boost_suspected" in pool.columns and pool["ad_boost_suspected"].notna().any():
            ad = (pool.assign(sus=pool["ad_boost_suspected"] == True)  # noqa: E712
                  .groupby("channel_title").agg(영상=("video_id", "size"), 의심=("sus", "sum")).reset_index())
            ad["의심률"] = ad["의심"] / ad["영상"]
            bar_h(ad.sort_values("의심률", ascending=False), "의심률", "channel_title", color="#B8442F",
                  x_format="%", text_format=".0%", tooltip=["channel_title", "영상", "의심", "의심률"])
            sus_rate = pool[pool["ad_boost_suspected"] == True]["is_hit"].mean()  # noqa: E712
            st.caption(f"의심 영상의 히트율 {fmt_rate(sus_rate)} vs 기준 {fmt_rate(base)} — 부풀려진 히트 주의")
        else:
            empty_state("ad_boost_suspected 데이터 없음")
    with c2:
        st.markdown("**PPL 기회 · 실제 PPL 감지 영상의 v2.0 PPL 잠재력**")
        scores = get_strategy_scores(client, DEFAULT_SCORE_VERSION)
        if scores.empty or "ppl_likely" not in scores.columns:
            empty_state("PPL 데이터 없음")
        else:
            ppl = scores[scores["ppl_likely"] == True]  # noqa: E712
            if picked:
                ppl = ppl[ppl["channel_title"].isin(picked)]
            ppl = ppl.sort_values("ppl_potential_score", ascending=False).head(25)
            ppl = ppl.assign(link=ppl.apply(lambda r: video_id_link(r.get("video_id"), r.get("video_url")), axis=1))
            st.dataframe(
                ppl[["title", "channel_title", "ppl_potential_score", "risk_score", "recommended_action", "link"]],
                hide_index=True, height=420,
                column_config={
                    "title": st.column_config.TextColumn("제목", width="large"),
                    "channel_title": "채널",
                    "ppl_potential_score": st.column_config.ProgressColumn("PPL 잠재력", min_value=0, max_value=100, format="%d"),
                    "risk_score": st.column_config.ProgressColumn("리스크", min_value=0, max_value=100, format="%d"),
                    "recommended_action": "등급",
                    "link": st.column_config.LinkColumn("영상", display_text="열기"),
                },
            )

# ── 히트 영상 레퍼런스 ────────────────────────────────────
section("최근 히트 레퍼런스", "채널 중앙값 대비 배수 상위", kicker="03")
hits = pool[pool["is_hit"] == True].sort_values("published_at", ascending=False).head(30)  # noqa: E712
if hits.empty:
    empty_state("히트 영상이 없습니다.")
else:
    hits = hits.assign(link=hits.apply(lambda r: video_id_link(r.get("video_id"), r.get("video_url")), axis=1),
                       published_at=pd.to_datetime(hits["published_at"], errors="coerce", utc=True))
    st.dataframe(
        hits[[c for c in ["thumbnail_url", "title", "channel_title", "view_count", "view_ratio", "content_structure",
                          "title_pattern", "published_at", "link"] if c in hits.columns]],
        hide_index=True, height=520, row_height=54,
        column_config={
            "thumbnail_url": st.column_config.ImageColumn("", width="small"),
            "title": st.column_config.TextColumn("제목", width="large"),
            "channel_title": "채널",
            "view_count": st.column_config.NumberColumn("조회", format="compact"),
            "view_ratio": st.column_config.NumberColumn("중앙 대비", format="%.1f×"),
            "content_structure": "구조", "title_pattern": "제목 패턴",
            "published_at": st.column_config.DatetimeColumn("업로드", format="YY.MM.DD"),
            "link": st.column_config.LinkColumn("영상", display_text="열기"),
        },
    )

sources_footer([
    ("videos", "16개 경쟁 채널 view_count · 자체 채널 제외 · 채널 중앙값 대비 배수"),
    ("initial/daily_analysis", "category, title_pattern, gemini_raw→content_structure, deepseek_raw→emotion_temperature, ad_boost_suspected, ppl_likely"),
    ("thumbnail_analysis", "couple_present, face_count, main_emotion, food_present"),
    ("video_scores", f"{DEFAULT_SCORE_VERSION} ppl_potential_score, risk_score"),
])
