"""순수 변환 / 포맷 헬퍼 (Streamlit 비의존 → 단위 테스트 대상).

원칙: 빈 DataFrame · None · 누락 JSONB 필드에서도 예외 없이 빈 결과를 돌려준다.
"""

from __future__ import annotations

import json
import math
import re
from datetime import date, datetime, timedelta, timezone
from typing import Iterable

import pandas as pd

from lib.constants import (
    B_ACTION_ORDER,
    EMOTIONS,
    HIT_MULTIPLIER,
    OWN_CHANNEL,
    STAGE_ORDER,
)

KST = timezone(timedelta(hours=9))
DASH = "—"


# ─────────────────────────────────────────────────────────
# 숫자 / 날짜 포맷
# ─────────────────────────────────────────────────────────
def to_num(value) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def fmt_int(value) -> str:
    n = to_num(value)
    return DASH if n is None else f"{int(round(n)):,}"


def fmt_views(value) -> str:
    """한국식 단위: 1.2억 / 34.5만 / 9,876."""
    n = to_num(value)
    if n is None:
        return DASH
    a = abs(n)
    if a >= 1e8:
        return f"{n / 1e8:.1f}".rstrip("0").rstrip(".") + "억"
    if a >= 1e4:
        return f"{n / 1e4:.1f}".rstrip("0").rstrip(".") + "만"
    return f"{int(round(n)):,}"


def fmt_ratio(value, digits: int = 1) -> str:
    n = to_num(value)
    return DASH if n is None else f"{n:.{digits}f}×"


def fmt_rate(value, digits: int = 0) -> str:
    """0~1 비율 → 퍼센트."""
    n = to_num(value)
    return DASH if n is None else f"{n * 100:.{digits}f}%"


def fmt_score(value) -> str:
    n = to_num(value)
    return DASH if n is None else f"{int(round(n))}"


def parse_ts(value) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, date):
        dt = datetime(value.year, value.month, value.day)
    else:
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def fmt_date(value, pattern: str = "%m.%d") -> str:
    dt = parse_ts(value)
    if dt is None:
        return DASH
    # date-only 문자열은 KST 변환 시 날짜가 밀리지 않도록 그대로 사용
    if isinstance(value, str) and len(value) == 10:
        return dt.strftime(pattern)
    return dt.astimezone(KST).strftime(pattern)


def freshness(value, now: datetime | None = None) -> tuple[str, str]:
    """수집 시각 → (라벨, 톤)."""
    dt = parse_ts(value)
    if dt is None:
        return "데이터 없음", "muted"
    now = now or datetime.now(timezone.utc)
    if isinstance(value, str) and len(value) == 10:
        days = (now.astimezone(KST).date() - dt.date()).days
    else:
        days = (now - dt).total_seconds() / 86400
    if days <= 1.05:
        return "최신", "up"
    if days <= 3:
        return f"{int(days)}일 전", "warn"
    return f"{int(days)}일 지연", "down"


def truncate(text, limit: int = 80) -> str:
    s = "" if text is None else str(text).strip()
    return s if len(s) <= limit else s[: limit - 1].rstrip() + "…"


# ─────────────────────────────────────────────────────────
# JSONB 안전 파싱
# ─────────────────────────────────────────────────────────
def parse_jsonish(value):
    """JSON 문자열이면 파싱, 아니면 그대로."""
    if isinstance(value, str):
        s = value.strip()
        if s[:1] in ("[", "{"):
            try:
                return json.loads(s)
            except ValueError:
                return value
    return value


def normalize_list(value) -> list[str]:
    """None / list / JSON 문자열 / dict / 일반 문자열 → 문자열 리스트."""
    value = parse_jsonish(value)
    if value is None:
        return []
    if isinstance(value, float) and math.isnan(value):
        return []
    if isinstance(value, dict):
        return [f"{k}: {v}" for k, v in value.items() if v not in (None, "", [])]
    if isinstance(value, (list, tuple)):
        out = []
        for item in value:
            if item is None:
                continue
            if isinstance(item, dict):
                text = (item.get("topic") or item.get("request") or item.get("text")
                        or item.get("format") or item.get("title") or item.get("dimension"))
                extra = item.get("rationale") or item.get("signal") or item.get("reason") or item.get("action")
                if text is None:
                    text = ", ".join(f"{k}: {v}" for k, v in item.items())
                out.append(f"{text} — {extra}" if extra else str(text))
            else:
                s = str(item).strip()
                if s:
                    out.append(s)
        return out
    s = str(value).strip()
    if not s or s in ("없음", "None", "null"):
        return []
    return [s]


def as_dict(value) -> dict:
    value = parse_jsonish(value)
    return value if isinstance(value, dict) else {}


def parse_rule_trace(trace) -> list[tuple[str, list[str]]]:
    """video_scores.rule_trace(dict[str, list[str]]) → [(key, [rules])]."""
    trace = parse_jsonish(trace)
    if isinstance(trace, dict):
        out = []
        for key, items in trace.items():
            rules = normalize_list(items)
            if rules:
                out.append((str(key), rules))
        return out
    rules = normalize_list(trace)
    return [("trace", rules)] if rules else []


# ─────────────────────────────────────────────────────────
# 공통 DataFrame 유틸
# ─────────────────────────────────────────────────────────
def ensure_columns(df: pd.DataFrame | None, columns: Iterable[str]) -> pd.DataFrame:
    df = pd.DataFrame() if df is None else df.copy()
    for col in columns:
        if col not in df.columns:
            df[col] = None
    return df


def exclude_own_channel(df: pd.DataFrame, column: str = "channel_title") -> pd.DataFrame:
    if df is None or df.empty or column not in df.columns:
        return pd.DataFrame() if df is None else df
    return df[df[column].fillna("").str.strip() != OWN_CHANNEL].reset_index(drop=True)


def latest_per_key(df: pd.DataFrame, key: str = "video_id", order: str = "analysis_date") -> pd.DataFrame:
    if df is None or df.empty or key not in df.columns:
        return pd.DataFrame() if df is None else df
    if order in df.columns:
        df = df.sort_values(order, ascending=False, na_position="last", kind="stable")
    return df.drop_duplicates(subset=[key], keep="first").reset_index(drop=True)


def expand_emotions(df: pd.DataFrame, column: str = "emotion_temperature",
                    emotions: Iterable[str] = EMOTIONS) -> pd.DataFrame:
    df = pd.DataFrame() if df is None else df.copy()
    emotions = tuple(emotions)
    if df.empty:
        for e in emotions:
            df[e] = pd.Series(dtype="float")
        return df
    source = df[column] if column in df.columns else pd.Series([None] * len(df), index=df.index)
    dicts = source.map(as_dict)
    for e in emotions:
        df[e] = dicts.map(lambda d, e=e: to_num(d.get(e)))
    return df


# ─────────────────────────────────────────────────────────
# Track A · 히트 패턴
# ─────────────────────────────────────────────────────────
def add_view_ratio(videos: pd.DataFrame, group: str = "channel_title",
                   hit_multiplier: float = HIT_MULTIPLIER) -> pd.DataFrame:
    """채널 중앙값 대비 조회수 배수(view_ratio)와 히트 여부."""
    df = ensure_columns(videos, [group, "view_count"])
    if df.empty:
        for col in ("channel_median", "view_ratio", "is_hit"):
            df[col] = pd.Series(dtype="float")
        return df
    df["view_count"] = pd.to_numeric(df["view_count"], errors="coerce")
    df["channel_median"] = df.groupby(group)["view_count"].transform("median")
    ratio = df["view_count"] / df["channel_median"].where(df["channel_median"] > 0)
    df["view_ratio"] = ratio
    df["is_hit"] = ratio >= hit_multiplier
    return df


def channel_leaderboard(videos: pd.DataFrame, hit_multiplier: float = HIT_MULTIPLIER) -> pd.DataFrame:
    cols = ["channel_title", "videos", "median_views", "mean_views", "hit_count", "hit_rate", "latest_upload"]
    df = add_view_ratio(exclude_own_channel(videos), hit_multiplier=hit_multiplier)
    df = df[df["channel_title"].notna()] if not df.empty else df
    if df.empty:
        return pd.DataFrame(columns=cols)
    if "published_at" not in df.columns:
        df["published_at"] = None
    board = (
        df.groupby("channel_title")
        .agg(videos=("view_count", "size"),
             median_views=("view_count", "median"),
             mean_views=("view_count", "mean"),
             hit_count=("is_hit", "sum"),
             latest_upload=("published_at", "max"))
        .reset_index()
    )
    board["hit_rate"] = board["hit_count"] / board["videos"].where(board["videos"] > 0)
    return board[cols].sort_values(["median_views", "hit_rate"], ascending=False).reset_index(drop=True)


def baseline_hit_rate(df: pd.DataFrame) -> float | None:
    if df is None or df.empty or "is_hit" not in df.columns:
        return None
    valid = df[df["view_ratio"].notna()] if "view_ratio" in df.columns else df
    return float(valid["is_hit"].mean()) if len(valid) else None


def lift_table(df: pd.DataFrame, feature: str, min_samples: int = 3) -> pd.DataFrame:
    """feature 값별 표본수 · 히트율 · 중앙 배수 · lift."""
    cols = ["value", "n", "hit_rate", "median_ratio", "lift"]
    if df is None or df.empty or feature not in df.columns or "view_ratio" not in df.columns:
        return pd.DataFrame(columns=cols)
    data = df[df["view_ratio"].notna() & df[feature].notna()]
    data = data[data[feature].astype(str).str.strip() != ""]
    if data.empty:
        return pd.DataFrame(columns=cols)
    base = baseline_hit_rate(df)
    grouped = (
        data.groupby(data[feature].astype(str))
        .agg(n=("view_ratio", "size"), hit_rate=("is_hit", "mean"), median_ratio=("view_ratio", "median"))
        .reset_index()
        .rename(columns={feature: "value"})
    )
    grouped = grouped[grouped["n"] >= min_samples]
    grouped["lift"] = grouped["hit_rate"] / base if base else None
    return grouped[cols].sort_values(["hit_rate", "n"], ascending=False).reset_index(drop=True)


def add_thumbnail_signals(df: pd.DataFrame) -> pd.DataFrame:
    """thumbnail_analysis 컬럼 → 커플/2인 얼굴/놀람/음식 불리언."""
    df = ensure_columns(df, ["couple_present", "face_count", "main_emotion", "food_present"])
    has_thumb = df["face_count"].notna() | df["main_emotion"].notna() | df["couple_present"].notna()
    faces = pd.to_numeric(df["face_count"], errors="coerce")
    df["couple"] = (df["couple_present"] == True)  # noqa: E712 — None 안전 비교
    df["two_faces"] = faces == 2
    df["surprise"] = df["main_emotion"].fillna("").astype(str).str.contains("놀람")
    df["food"] = (df["food_present"] == True)  # noqa: E712
    df["has_thumbnail"] = has_thumb
    return df


def signal_lift(df: pd.DataFrame, signals: Iterable[tuple[str, str]]) -> pd.DataFrame:
    """불리언 시그널 True 그룹의 히트율 vs 전체."""
    cols = ["signal", "label", "n", "hit_rate", "median_ratio", "lift"]
    if df is None or df.empty or "view_ratio" not in df.columns:
        return pd.DataFrame(columns=cols)
    pool = df[df["view_ratio"].notna()]
    if "has_thumbnail" in pool.columns:
        pool = pool[pool["has_thumbnail"] == True]  # noqa: E712
    base = baseline_hit_rate(pool)
    rows = []
    for key, label in signals:
        if key not in pool.columns:
            continue
        sub = pool[pool[key] == True]  # noqa: E712
        if sub.empty:
            continue
        rate = float(sub["is_hit"].mean())
        rows.append({
            "signal": key, "label": label, "n": len(sub), "hit_rate": rate,
            "median_ratio": float(sub["view_ratio"].median()),
            "lift": rate / base if base else None,
        })
    return pd.DataFrame(rows, columns=cols)


def emotion_lift(df: pd.DataFrame, emotions: Iterable[str] = EMOTIONS,
                 threshold: float = 20) -> pd.DataFrame:
    """감정온도 고온(≥threshold) vs 저온 그룹 히트율 비교."""
    cols = ["emotion", "high_n", "high_hit_rate", "low_n", "low_hit_rate", "avg_temp"]
    if df is None or df.empty or "view_ratio" not in df.columns:
        return pd.DataFrame(columns=cols)
    pool = df[df["view_ratio"].notna()]
    rows = []
    for e in emotions:
        if e not in pool.columns:
            continue
        temp = pd.to_numeric(pool[e], errors="coerce")
        valid = pool[temp.notna()]
        if valid.empty:
            continue
        t = temp[temp.notna()]
        high = valid[t >= threshold]
        low = valid[t < threshold]
        rows.append({
            "emotion": e,
            "high_n": len(high),
            "high_hit_rate": float(high["is_hit"].mean()) if len(high) else None,
            "low_n": len(low),
            "low_hit_rate": float(low["is_hit"].mean()) if len(low) else None,
            "avg_temp": float(t.mean()),
        })
    return pd.DataFrame(rows, columns=cols)


def score_signal_badges(row) -> list[tuple[str, str]]:
    """점수 카드용 썸네일·감정온도·리스크 시그널 배지 [(라벨, 톤)]."""
    get = row.get if hasattr(row, "get") else (lambda k, d=None: d)
    badges: list[tuple[str, str]] = []
    if get("couple_present") is True:
        badges.append(("커플 썸네일", "up"))
    if to_num(get("face_count")) == 2:
        badges.append(("2인 얼굴", "up"))
    if "놀람" in str(get("main_emotion") or ""):
        badges.append(("놀람 표정", "up"))
    if get("food_present") is True:
        badges.append(("음식 노출", "warn"))
    envy, asp = to_num(get("envy")) or 0, to_num(get("aspiration")) or 0
    if (envy + asp) / 2 >= 20:
        badges.append((f"부러움·동경 {int((envy + asp) / 2)}°", "info"))
    fat = max(to_num(get("fatigue")) or 0, to_num(get("cringe")) or 0)
    if fat >= 15:
        badges.append((f"피로·오글 {int(fat)}°", "down"))
    if get("ad_boost_suspected") is True:
        badges.append(("광고 부스팅 의심", "down"))
    return badges


# ─────────────────────────────────────────────────────────
# Track A · 자체 채널
# ─────────────────────────────────────────────────────────
CHECKPOINT_WINDOWS = {3: (2.0, 4.5), 6: (4.5, 8.0), 12: (9.0, 15.0), 24: (20.0, 30.0)}


def hourly_checkpoints(hourly: pd.DataFrame, windows: dict | None = None) -> pd.DataFrame:
    """own_channel_hourly_snapshots → video_id 별 3h/6h/12h/24h 조회수 (창 안에서 목표 시간에 가장 가까운 관측)."""
    windows = windows or CHECKPOINT_WINDOWS
    cols = ["video_id"] + [f"h{h}" for h in windows]
    if hourly is None or hourly.empty or not {"video_id", "hours_since_upload", "view_count"} <= set(hourly.columns):
        return pd.DataFrame(columns=cols)
    df = hourly.copy()
    df["hours_since_upload"] = pd.to_numeric(df["hours_since_upload"], errors="coerce")
    df["view_count"] = pd.to_numeric(df["view_count"], errors="coerce")
    df = df.dropna(subset=["hours_since_upload", "view_count"])
    out = []
    for vid, grp in df.groupby("video_id"):
        rec = {"video_id": vid}
        for target, (lo, hi) in windows.items():
            win = grp[(grp["hours_since_upload"] >= lo) & (grp["hours_since_upload"] <= hi)]
            if win.empty:
                rec[f"h{target}"] = None
            else:
                idx = (win["hours_since_upload"] - target).abs().idxmin()
                rec[f"h{target}"] = float(win.loc[idx, "view_count"])
        out.append(rec)
    return pd.DataFrame(out, columns=cols)


# ─────────────────────────────────────────────────────────
# Track B
# ─────────────────────────────────────────────────────────
_NORMALIZE_RE = re.compile(r"[\s\-_·•.,!?'\"()\[\]#/]+")


def normalize_text(text) -> str:
    if text is None:
        return ""
    try:
        if pd.isna(text):
            return ""
    except (TypeError, ValueError):
        pass
    return _NORMALIZE_RE.sub("", str(text).lower())


def celeb_unadopted(entities: pd.DataFrame, celeb_titles: Iterable[str], *,
                    min_channels: int = 3,
                    stages: Iterable[str] = ("breakout", "rising", "mainstream"),
                    min_name_len: int = 2) -> pd.DataFrame:
    """Track B 에서 여러 채널이 다루지만 최근 Track A 셀럽 영상 제목엔 없는 엔티티."""
    cols = ["entity_id", "entity_name", "category", "trend_stage", "distinct_channels_30d",
            "mention_count_30d", "median_view_over_baseline", "recommended_action", "suggested_angle"]
    if entities is None or entities.empty or "entity_name" not in entities.columns:
        return pd.DataFrame(columns=cols)
    df = ensure_columns(entities, cols)
    df["distinct_channels_30d"] = pd.to_numeric(df["distinct_channels_30d"], errors="coerce").fillna(0)
    df["mention_count_30d"] = pd.to_numeric(df["mention_count_30d"], errors="coerce").fillna(0)
    stages = tuple(stages or ())
    if stages:
        df = df[df["trend_stage"].isin(stages)]
    df = df[df["distinct_channels_30d"] >= min_channels]
    if df.empty:
        return pd.DataFrame(columns=cols)
    corpus = " ".join(norm for t in (celeb_titles or []) if (norm := normalize_text(t)))
    keys = df["entity_name"].map(normalize_text)
    keep = keys.map(lambda k: len(k) >= min_name_len and k not in corpus)
    out = df[keep].sort_values(
        ["distinct_channels_30d", "mention_count_30d", "median_view_over_baseline"],
        ascending=False, na_position="last",
    )
    return out[cols].reset_index(drop=True)


def stage_rank(stage) -> int:
    try:
        return STAGE_ORDER.index(stage)
    except ValueError:
        return len(STAGE_ORDER)


def b_action_rank(action) -> int:
    try:
        return B_ACTION_ORDER.index(action)
    except ValueError:
        return len(B_ACTION_ORDER)


def top_b_signals(entities: pd.DataFrame, n: int = 6,
                  stages: Iterable[str] = ("breakout", "rising")) -> pd.DataFrame:
    if entities is None or entities.empty or "trend_stage" not in entities.columns:
        return pd.DataFrame()
    df = entities[entities["trend_stage"].isin(tuple(stages))].copy()
    if df.empty:
        return df
    df = ensure_columns(df, ["recommended_action", "median_view_over_baseline", "distinct_channels_30d"])
    df["_s"] = df["trend_stage"].map(stage_rank)
    df["_a"] = df["recommended_action"].map(b_action_rank)
    df["_v"] = pd.to_numeric(df["median_view_over_baseline"], errors="coerce").fillna(0)
    df["_c"] = pd.to_numeric(df["distinct_channels_30d"], errors="coerce").fillna(0)
    df = df.sort_values(["_s", "_a", "_v", "_c"], ascending=[True, True, False, False])
    return df.drop(columns=["_s", "_a", "_v", "_c"]).head(n).reset_index(drop=True)


def split_hints(series: pd.Series | None) -> pd.DataFrame:
    """trend_sources.category_hint('fashion,lifestyle') → 카테고리별 소스 수."""
    if series is None or len(series) == 0:
        return pd.DataFrame(columns=["hint", "sources"])
    hints = (
        series.dropna().astype(str).str.split(",").explode().str.strip().replace("", pd.NA).dropna()
    )
    if hints.empty:
        return pd.DataFrame(columns=["hint", "sources"])
    out = hints.value_counts().rename_axis("hint").reset_index(name="sources")
    return out


def video_id_link(video_id, url=None) -> str | None:
    if url and str(url).startswith(("http://", "https://")):
        return str(url)
    return f"https://youtu.be/{video_id}" if video_id else None
