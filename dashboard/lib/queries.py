"""Supabase 조회 함수 — 100% read-only.

규칙
- 모든 조회는 `@st.cache_data(ttl=300, show_spinner=False)`.
- PostgREST 기본 1,000행 절단을 피하려고 `_read_pages(lambda: query)` 로 페이지네이션.
- 대용량 B-track 테이블은 최신 스냅샷(scored_at / card_date / computed_at) 하나만,
  필요한 컬럼만 명시해서 읽는다.
- 실패/빈 테이블은 빈 DataFrame · None 으로 돌려주고 화면은 빈 상태를 그린다.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Callable

import pandas as pd
import streamlit as st

from lib.constants import DEFAULT_SCORE_VERSION, OWN_CHANNEL
from lib.transforms import (
    KST,
    add_thumbnail_signals,
    exclude_own_channel,
    expand_emotions,
    latest_per_key,
    parse_ts,
)

log = logging.getLogger(__name__)
CACHE_TTL = 300
PAGE_SIZE = 500


# ─────────────────────────────────────────────────────────
# 공통 헬퍼
# ─────────────────────────────────────────────────────────
def _read_pages(build_query: Callable, page_size: int = PAGE_SIZE, max_rows: int | None = None) -> list[dict]:
    """`build_query` 팩토리로 매 페이지 새 쿼리를 만들어 range() 페이지네이션."""
    rows: list[dict] = []
    offset = 0
    while True:
        upper = offset + page_size - 1
        if max_rows is not None:
            upper = min(upper, max_rows - 1)
        page = build_query().range(offset, upper).execute().data or []
        rows.extend(page)
        if len(page) < (upper - offset + 1):
            break
        offset += len(page)
        if max_rows is not None and offset >= max_rows:
            break
    return rows


def _safe(fn: Callable, default):
    try:
        return fn()
    except Exception as exc:  # 테이블 누락 · 네트워크 오류 → 빈 상태
        log.warning("dashboard query failed: %s", exc)
        return default


def _latest_value(client, table: str, column: str):
    """table 에서 column 의 최댓값(최신 스냅샷 키) 1건."""
    def run():
        data = (client.table(table).select(column).order(column, desc=True, nullsfirst=False).limit(1).execute().data) or []
        return data[0].get(column) if data else None
    return _safe(run, None)


def _select_names(select: str) -> list[str]:
    """'a,b:c->>d' → ['a', 'b'] (alias 우선)."""
    names = []
    for part in select.split(","):
        part = part.strip()
        if part and part != "*":
            names.append(part.split(":", 1)[0].split("->", 1)[0].strip())
    return names


def _frame(rows, select: str | None = None) -> pd.DataFrame:
    """rows → DataFrame. select 를 주면 누락 컬럼을 None 으로 보장(희소 JSON/빈 결과 방어)."""
    df = pd.DataFrame(rows or [])
    if select:
        for col in _select_names(select):
            if col not in df.columns:
                df[col] = None
    return df


def _kst_cutoff(days: int) -> str:
    return (datetime.now(KST) - timedelta(days=days)).date().isoformat()


# ─────────────────────────────────────────────────────────
# Track A · 경쟁 채널 영상
# ─────────────────────────────────────────────────────────
VIDEO_COLUMNS = ("video_id,channel_title,title,published_at,view_count,like_count,"
                 "comment_count,duration,thumbnail_url,video_url")


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def get_competitor_videos(_client) -> pd.DataFrame:
    """videos 전체(경쟁 16채널) — 자체 채널은 서버/클라이언트 양쪽에서 제외."""
    rows = _safe(lambda: _read_pages(
        lambda: _client.table("videos").select(VIDEO_COLUMNS)
        .neq("channel_title", OWN_CHANNEL)
        .order("published_at", desc=True).order("video_id")
    ), [])
    return exclude_own_channel(_frame(rows, VIDEO_COLUMNS))


_COLS_RECENT_CELEB_TITLES = (
    "video_id,channel_title,title"
)


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def get_recent_celeb_titles(_client, days: int = 90) -> list[str]:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    rows = _safe(lambda: _read_pages(
        lambda: _client.table("videos").select(_COLS_RECENT_CELEB_TITLES)
        .neq("channel_title", OWN_CHANNEL)
        .gte("published_at", cutoff).order("published_at", desc=True).order("video_id")
    ), [])
    df = exclude_own_channel(_frame(rows, _COLS_RECENT_CELEB_TITLES))
    return [] if df.empty or "title" not in df.columns else df["title"].dropna().astype(str).tolist()


# ─────────────────────────────────────────────────────────
# Track A · 전략 점수
# ─────────────────────────────────────────────────────────
SCORE_COLUMNS = ("video_id,score_version,source_table,scored_at,priority_score,recommended_action,"
                 "hangoeun_fit_score,execution_score,repeatability_score,novelty_score,risk_score,"
                 "ppl_potential_score,trend_lifespan_score,upload_delay_risk,evergreen_score,"
                 "content_lifespan_type,strategy_reason,rule_trace")


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def get_score_versions(_client) -> list[str]:
    rows = _safe(lambda: _read_pages(
        lambda: _client.table("video_scores").select("score_version").order("score_version", desc=True)
        .order("video_id")
    ), [])
    versions = {r.get("score_version") for r in rows if r.get("score_version")}
    return sorted(versions, reverse=True)


_COLS_THUMBNAIL_SIGNALS = (
    "video_id,face_count,main_emotion,couple_present,food_present,family_present,home_visible,"
    "luxury_signal,text_overlay,thumbnail_style,ctr_prediction"
)


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def get_thumbnail_signals(_client) -> pd.DataFrame:
    rows = _safe(lambda: _read_pages(
        lambda: _client.table("thumbnail_analysis")
        .select(_COLS_THUMBNAIL_SIGNALS)
        .order("video_id")
    ), [])
    return latest_per_key(_frame(rows, _COLS_THUMBNAIL_SIGNALS), order="video_id")


ANALYSIS_FEATURE_SELECT = (
    "video_id,analysis_date,category,title_pattern,ppl_likely,ppl_signals,ad_boost_suspected,"
    "ad_boost_score,content_structure:gemini_raw->>content_structure,"
    "title_emotion_tone:gemini_raw->>title_emotion_tone,"
    "emotion_temperature:deepseek_raw->emotion_temperature"
)


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def get_analysis_features(_client) -> pd.DataFrame:
    """initial_analysis + daily_analysis 의 영상별 최신 분석 피처.

    거대한 gemini_raw/deepseek_raw 대신 JSON path 로 필요한 키만 가져온다.
    """
    rows: list[dict] = []
    for table in ("initial_analysis", "daily_analysis"):
        part = _safe(lambda table=table: _read_pages(
            lambda: _client.table(table).select(ANALYSIS_FEATURE_SELECT).order("id")
        ), [])
        rows.extend({**r, "source_table": table} for r in part)
    df = latest_per_key(_frame(rows, ANALYSIS_FEATURE_SELECT), order="analysis_date")
    return expand_emotions(df)


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def get_strategy_scores(_client, score_version: str = DEFAULT_SCORE_VERSION) -> pd.DataFrame:
    """video_scores(단일 score_version) × videos × 썸네일/감정 시그널.

    score_version 은 항상 eq 필터 → v1.2 / v2.0 이 섞이지 않는다.
    """
    version = score_version or DEFAULT_SCORE_VERSION
    rows = _safe(lambda: _read_pages(
        lambda: _client.table("video_scores").select(SCORE_COLUMNS)
        .eq("score_version", version)
        .order("priority_score", desc=True, nullsfirst=False).order("video_id")
    ), [])
    scores = _frame(rows, SCORE_COLUMNS)
    if scores.empty:
        return scores
    scores = scores[scores["score_version"] == version]

    videos = get_competitor_videos(_client)
    if not videos.empty:
        meta = videos[["video_id", "title", "channel_title", "view_count", "published_at",
                       "thumbnail_url", "video_url"]]
        # inner join: 경쟁 16채널 영상만 남긴다 (자체 채널/메타 없는 점수 제외)
        scores = scores.merge(meta, on="video_id", how="inner")
    thumbs = get_thumbnail_signals(_client)
    if not thumbs.empty:
        scores = scores.merge(
            thumbs[["video_id", "face_count", "main_emotion", "couple_present", "food_present"]],
            on="video_id", how="left")
    feats = get_analysis_features(_client)
    if not feats.empty:
        keep = [c for c in ("video_id", "envy", "aspiration", "fatigue", "cringe",
                            "ad_boost_suspected", "ppl_likely", "category") if c in feats.columns]
        scores = scores.merge(feats[keep], on="video_id", how="left")
    scores = exclude_own_channel(scores)
    return scores.sort_values("priority_score", ascending=False, na_position="last").reset_index(drop=True)


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def get_celeb_hit_dataset(_client) -> pd.DataFrame:
    """경쟁 영상 × 분석 피처 × 썸네일 — 히트 패턴 분석용 원천."""
    videos = get_competitor_videos(_client)
    if videos.empty:
        return videos
    df = videos.copy()
    feats = get_analysis_features(_client)
    if not feats.empty:
        df = df.merge(feats.drop(columns=["emotion_temperature"], errors="ignore"), on="video_id", how="left")
    thumbs = get_thumbnail_signals(_client)
    if not thumbs.empty:
        df = df.merge(thumbs, on="video_id", how="left")
    return add_thumbnail_signals(df)


# ─────────────────────────────────────────────────────────
# Track A · 자체 채널 (고은언니 한고은)
# ─────────────────────────────────────────────────────────
_COLS_OWN_VIDEOS = (
    "video_id,title,published_at,video_type,view_count,like_count,comment_count,duration_seconds,"
    "thumbnail_url,video_url,tracking_active,last_tracked_at"
)


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def get_own_videos(_client) -> pd.DataFrame:
    rows = _safe(lambda: _read_pages(
        lambda: _client.table("own_channel_videos")
        .select(_COLS_OWN_VIDEOS)
        .order("published_at", desc=True).order("video_id")
    ), [])
    df = _frame(rows, _COLS_OWN_VIDEOS)
    if not df.empty:
        df["channel_title"] = OWN_CHANNEL
    return df


_COLS_OWN_HOURLY = (
    "video_id,snapshot_at,hours_since_upload,view_count,like_count,comment_count,view_growth"
)


@st.cache_data(ttl=120, show_spinner=False)
def get_own_hourly(_client) -> pd.DataFrame:
    rows = _safe(lambda: _read_pages(
        lambda: _client.table("own_channel_hourly_snapshots")
        .select(_COLS_OWN_HOURLY)
        .order("video_id").order("hours_since_upload")
    ), [])
    return _frame(rows, _COLS_OWN_HOURLY)


_COLS_OWN_WEEKLY = (
    "video_id,snapshot_date,week_number,view_count,like_count,comment_count,view_growth"
)


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def get_own_weekly(_client) -> pd.DataFrame:
    rows = _safe(lambda: _read_pages(
        lambda: _client.table("own_channel_snapshots")
        .select(_COLS_OWN_WEEKLY)
        .order("video_id").order("week_number")
    ), [])
    return _frame(rows, _COLS_OWN_WEEKLY)


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def get_own_analysis(_client, video_id: str) -> dict | None:
    def run():
        data = (_client.table("own_channel_analysis")
                .select("video_id,analysis_date,topic,content_structure,title_pattern,title_emotion_tone,"
                        "predicted_performance,predicted_performance_reason,strengths,improvement_points,"
                        "thumbnail_suggestion,competitor_angle,sentiment,praise_points,complaints,"
                        "next_video_requests,fan_engagement,viral_signals,revisit_intent,comments_summary")
                .eq("video_id", video_id).order("analysis_date", desc=True).limit(1).execute().data) or []
        return data[0] if data else None
    return _safe(run, None)


_COLS_OWN_ANALYSIS_INDEX = (
    "video_id,analysis_date,predicted_performance,content_structure,next_video_requests"
)


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def get_own_analysis_index(_client) -> pd.DataFrame:
    rows = _safe(lambda: _read_pages(
        lambda: _client.table("own_channel_analysis")
        .select(_COLS_OWN_ANALYSIS_INDEX)
        .order("video_id").order("analysis_date", desc=True)
    ), [])
    return latest_per_key(_frame(rows, _COLS_OWN_ANALYSIS_INDEX), order="analysis_date")


# ─────────────────────────────────────────────────────────
# Track A · 주간 리포트 / 스냅샷 / 급상승
# ─────────────────────────────────────────────────────────
@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def get_weekly_market_states(_client, limit: int = 12) -> list[dict]:
    return _safe(lambda: (
        _client.table("weekly_market_state")
        .select("report_date,dominant_emotion,rising_topics,declining_topics,oversaturated_formats,"
                "emerging_formats,viewer_fatigue_signals,comfort_content_score,hangoeun_opportunity_score,"
                "hangoeun_recommended_topics,competitive_gap,market_summary")
        .order("report_date", desc=True).limit(limit).execute().data
    ) or [], [])


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def get_weekly_reports(_client, limit: int = 12) -> list[dict]:
    return _safe(lambda: (
        _client.table("weekly_reports")
        .select("report_date,week_summary,top_performing_channels,rising_topics,format_trends,"
                "ppl_observations,actionable_insights_for_hangoeun,next_video_suggestions")
        .order("report_date", desc=True).limit(limit).execute().data
    ) or [], [])


_COLS_LATEST_SNAPSHOTS = (
    "video_id,channel_title,snapshot_date,days_since_publish,view_count,view_growth,"
    "view_growth_rate,comment_growth_rate,growth_pattern,d7_view_ratio,d30_view_ratio,source_type"
)


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def get_latest_snapshots(_client, days: int = 14) -> pd.DataFrame:
    """video_snapshots 최근 N일 → video_id 별 최신 1건."""
    cutoff = _kst_cutoff(days)
    rows = _safe(lambda: _read_pages(
        lambda: _client.table("video_snapshots")
        .select(_COLS_LATEST_SNAPSHOTS)
        .gte("snapshot_date", cutoff).order("snapshot_date", desc=True).order("id")
    ), [])
    return latest_per_key(_frame(rows, _COLS_LATEST_SNAPSHOTS), order="snapshot_date")


_COLS_TREND_CLASSIFIED = (
    "analysis_date,track,video_id,title,channel_title,view_count,published_at,track_score_a,"
    "track_score_b,matched_keywords,irregular_reasons"
)


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def get_trend_classified(_client, days: int = 7) -> pd.DataFrame:
    cutoff = _kst_cutoff(days)
    rows = _safe(lambda: _read_pages(
        lambda: _client.table("trend_classified")
        .select(_COLS_TREND_CLASSIFIED)
        .gte("analysis_date", cutoff).order("analysis_date", desc=True).order("id")
    ), [])
    return _frame(rows, _COLS_TREND_CLASSIFIED)


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def get_collection_health(_client) -> dict:
    """테이블별 최신 수집 시각 — 홈/사이드바 헬스 표시."""
    probes = {
        "videos": ("videos", "collected_at"),
        "video_snapshots": ("video_snapshots", "snapshot_date"),
        "video_scores": ("video_scores", "scored_at"),
        "own_hourly": ("own_channel_hourly_snapshots", "snapshot_at"),
        "trend_classified": ("trend_classified", "analysis_date"),
        "weekly_market_state": ("weekly_market_state", "report_date"),
        "trend_entity_scores": ("trend_entity_scores", "scored_at"),
        "trend_clusters": ("trend_clusters", "computed_at"),
    }
    return {key: _latest_value(_client, table, col) for key, (table, col) in probes.items()}


# ─────────────────────────────────────────────────────────
# Track B · 패션 + 유행
# ─────────────────────────────────────────────────────────
ENTITY_SCORE_COLUMNS = (
    "entity_id,entity_name,entity_type,category,scored_at,mention_count_7d,mention_count_30d,"
    "distinct_channels_7d,distinct_channels_30d,avg_view_over_baseline,median_view_over_baseline,"
    "best_view_over_baseline,hit_ratio,total_views_30d,velocity_7d,acceleration_score,trend_stage,"
    "stage_reason,saturation_score,freshness_score,best_format,format_distribution,engagement_score,"
    "commerce_signal,brand_heat_score,item_heat_score,shopping_intent_score,ppl_potential,"
    "channel_fit_score,timing_score,production_difficulty,originality_room,recommended_action,"
    "suggested_angle,confidence"
)
NUMERIC_ENTITY_COLUMNS = (
    "mention_count_7d", "mention_count_30d", "distinct_channels_7d", "distinct_channels_30d",
    "avg_view_over_baseline", "median_view_over_baseline", "best_view_over_baseline", "hit_ratio",
    "total_views_30d", "velocity_7d", "acceleration_score", "saturation_score", "freshness_score",
    "engagement_score", "commerce_signal", "brand_heat_score", "item_heat_score",
    "shopping_intent_score", "ppl_potential", "channel_fit_score", "timing_score",
    "production_difficulty", "originality_room",
)


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def get_b_track_entity_scores(_client, scored_at: str | None = None) -> pd.DataFrame:
    """trend_entity_scores 최신 scored_at 단일 스냅샷 (~27K행 전수 스캔 금지)."""
    snapshot = scored_at or _latest_value(_client, "trend_entity_scores", "scored_at")
    if not snapshot:
        return pd.DataFrame()
    rows = _safe(lambda: _read_pages(
        lambda: _client.table("trend_entity_scores").select(ENTITY_SCORE_COLUMNS)
        .eq("scored_at", snapshot).order("distinct_channels_30d", desc=True, nullsfirst=False).order("entity_id")
    ), [])
    df = _frame(rows, ENTITY_SCORE_COLUMNS)
    if df.empty:
        return df
    df = df[df["scored_at"].astype(str) == str(snapshot)]
    for col in NUMERIC_ENTITY_COLUMNS:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df.reset_index(drop=True)


_COLS_B_TRACK_ANSWER_CARDS = (
    "entity_id,entity_name,card_date,question_type,answer_summary,evidence_bullets,"
    "caution_bullets,recommended_content_ideas,best_examples,confidence"
)


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def get_b_track_answer_cards(_client, card_date: str | None = None) -> pd.DataFrame:
    """trend_answer_cards 최신 card_date 단일 스냅샷."""
    snapshot = card_date or _latest_value(_client, "trend_answer_cards", "card_date")
    if not snapshot:
        return pd.DataFrame()
    rows = _safe(lambda: _read_pages(
        lambda: _client.table("trend_answer_cards")
        .select(_COLS_B_TRACK_ANSWER_CARDS)
        .eq("card_date", snapshot).order("entity_id")
    ), [])
    df = _frame(rows, _COLS_B_TRACK_ANSWER_CARDS)
    if df.empty:
        return df
    df = df[df["card_date"].astype(str) == str(snapshot)]
    return latest_per_key(df, key="entity_id", order="card_date")


_COLS_B_TRACK_CLUSTERS = (
    "cluster_name,category,trend_stage,mention_count_7d,distinct_channels_7d,total_views_7d,"
    "velocity_score,duplication_score,convergence_score,fatigue_score,summary,computed_at"
)


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def get_b_track_clusters(_client, window_hours: int = 24) -> pd.DataFrame:
    """trend_clusters 최신 computed_at 배치(최신 시각 - window_hours 이후)만."""
    latest = _latest_value(_client, "trend_clusters", "computed_at")
    latest_dt = parse_ts(latest)
    if latest_dt is None:
        return pd.DataFrame()
    cutoff = (latest_dt - timedelta(hours=window_hours)).isoformat()
    rows = _safe(lambda: _read_pages(
        lambda: _client.table("trend_clusters")
        .select(_COLS_B_TRACK_CLUSTERS)
        .gte("computed_at", cutoff).order("mention_count_7d", desc=True, nullsfirst=False).order("id")
    ), [])
    df = _frame(rows, _COLS_B_TRACK_CLUSTERS)
    if df.empty:
        return df
    stamps = df["computed_at"].map(parse_ts)
    df = df[stamps.map(lambda t: t is not None and t >= latest_dt - timedelta(hours=window_hours))]
    for col in ("mention_count_7d", "distinct_channels_7d", "total_views_7d", "velocity_score",
                "duplication_score", "convergence_score", "fatigue_score"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df.reset_index(drop=True)


_COLS_TREND_SOURCES = (
    "id,source_type,source_name,source_ref,category_hint,priority,active,last_collected_at,"
    "total_collected"
)


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def get_trend_sources(_client) -> pd.DataFrame:
    rows = _safe(lambda: _read_pages(
        lambda: _client.table("trend_sources")
        .select(_COLS_TREND_SOURCES)
        .eq("active", True).order("id")
    ), [])
    return _frame(rows, _COLS_TREND_SOURCES)


_COLS_TOP_TREND_VIDEOS = (
    "video_id,channel_title,title,published_at,view_count,channel_avg_views,view_over_baseline,"
    "thumbnail_url,video_type"
)


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def get_top_trend_videos(_client, days: int = 30, limit: int = 60) -> pd.DataFrame:
    """trend_videos(~79K) 최근 N일 중 view_over_baseline 상위 limit — 전수 스캔 없음."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    rows = _safe(lambda: (
        _client.table("trend_videos")
        .select(_COLS_TOP_TREND_VIDEOS)
        .gte("published_at", cutoff).order("view_over_baseline", desc=True, nullsfirst=False).limit(limit).execute().data
    ) or [], [])
    return _frame(rows, _COLS_TOP_TREND_VIDEOS)
