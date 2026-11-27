"""Read-only pattern learning from existing YouTube analysis and snapshots."""

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import json
import math
from pathlib import Path
import re
from statistics import median
import unicodedata

import config
from gemini_client import has_text, safe_error
from strategy_scorer import PRIORITY_WEIGHTS
import supabase_writer


_ANALYSIS_COLUMNS = (
    "id,video_id,analysis_date,topic,category,title_pattern,title_keywords,"
    "ppl_likely,applicability,gemini_raw,deepseek_raw,ad_boost_suspected"
)
_SCORE_WEIGHTS_BY_VERSION = {
    "v1.2": {
        "hangoeun_fit_score": 0.30,
        "execution_score": 0.07,
        "repeatability_score": 0.18,
        "novelty_score": 0.12,
        "risk_score": -0.15,
        "ppl_potential_score": 0.10,
        "trend_lifespan_score": 0.08,
        "upload_delay_risk": -0.10,
        "evergreen_score": 0.15,
    },
    "v2.0": PRIORITY_WEIGHTS,
}
_SCORE_FIELDS = tuple(dict.fromkeys(
    field for weights in _SCORE_WEIGHTS_BY_VERSION.values() for field in weights
))
_ACTIONS = ("바로 기획화", "각색 후 기획", "아이디어 보관", "우선순위 낮음")
_EMOTIONS = ("comfort", "healing", "nostalgia", "trust", "intimacy",
             "aspiration", "envy", "fatigue", "cringe")
_STOPWORDS = frozenset("""
추천 일상 브이로그 영상 동영상 쇼핑 코디 하울 리뷰 꿀템 신상 요즘 진짜 제품 아이템
트렌드 메이크업 스킨케어 패션 뷰티 요리 레시피 스타일 데일리 오늘 이번 사용 후기
소개 정보 인기 특별 공개 신제품 브랜드 생활 콘텐츠 루틴 관리 유튜브 채널 광고
화장 화장품 음식 가방 옷 악세사리 액세서리 vlog haul review fashion beauty makeup
skincare shopping trend daily video shorts style product recommendation youtube
""".split())
_CHECKPOINTS = {3: (2.0, 4.5), 6: (4.5, 8.0), 12: (9.0, 15.0), 24: (20.0, 30.0)}
_VIDEO_TYPES = ("longform", "shorts")
_DURATION = re.compile(r"P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?")
_START_OF_TIME = datetime.min.replace(tzinfo=timezone.utc)


def _ranks(values):
    order = sorted(range(len(values)), key=values.__getitem__)
    ranks = [0.0] * len(order)
    start = 0
    while start < len(order):
        end = start + 1
        while end < len(order) and values[order[start]] == values[order[end]]:
            end += 1
        average_rank = (start + 1 + end) / 2
        for index in order[start:end]:
            ranks[index] = average_rank
        start = end
    return ranks


def spearman_correlation(xs, ys) -> float | None:
    """Return Pearson correlation of average ranks, or None if undefined."""
    if len(xs) != len(ys) or len(xs) < 3:
        return None
    center = (len(xs) + 1) / 2
    x = [rank - center for rank in _ranks(xs)]
    y = [rank - center for rank in _ranks(ys)]
    denominator = math.sqrt(sum(rank * rank for rank in x) * sum(rank * rank for rank in y))
    if denominator == 0:
        return None
    return sum(a * b for a, b in zip(x, y)) / denominator


def percentile(sorted_vals, p) -> float | None:
    """Linearly interpolate sorted values at a fraction p in [0, 1]."""
    if not 0 <= p <= 1:
        raise ValueError("p must be a fraction between 0 and 1")
    if not sorted_vals:
        return None
    position = (len(sorted_vals) - 1) * p
    lower = math.floor(position)
    upper = math.ceil(position)
    return sorted_vals[lower] + (position - lower) * (sorted_vals[upper] - sorted_vals[lower])


def parse_iso_datetime(val) -> datetime | None:
    """Normalize ISO timestamps to UTC, treating naive timestamps as UTC."""
    if isinstance(val, str):
        val = val.strip()
        if val.endswith("Z"):
            val = val[:-1] + "+00:00"
        try:
            val = datetime.fromisoformat(val)
        except ValueError:
            return None
    if not isinstance(val, datetime):
        return None
    if val.tzinfo is None:
        return val.replace(tzinfo=timezone.utc)
    return val.astimezone(timezone.utc)


def parse_iso_duration_seconds(dur) -> int | None:
    """Parse a YouTube ISO duration expressed in days, hours, minutes and seconds."""
    match = _DURATION.fullmatch(dur) if isinstance(dur, str) else None
    if not match or not any(match.groups()) or dur.endswith("T"):
        return None
    days, hours, minutes, seconds = (int(part or 0) for part in match.groups())
    return days * 86400 + hours * 3600 + minutes * 60 + seconds


def confidence_tier(n: int) -> str:
    if n >= 15:
        return "high"
    if n >= 7:
        return "medium"
    return "low"


def load_dataset_from_supabase(
    client=None, *, lookback_days: int = 210, skip_btrack: bool = False,
    now: datetime | None = None,
) -> dict:
    """Fetch read-only table snapshots with deterministic page ordering."""
    if lookback_days < 1:
        raise ValueError("lookback_days must be positive")
    if client is None:
        client = supabase_writer.get_client()
    if client is None:
        raise RuntimeError("Supabase 연결 설정 누락")
    reference = parse_iso_datetime(now) or datetime.now(timezone.utc)
    cutoff = (reference - timedelta(days=lookback_days)).isoformat()

    def read(table, columns, *order, filter_query=None):
        def build_query():
            query = client.table(table).select(columns)
            if filter_query is not None:
                query = filter_query(query)
            for column in order:
                query = query.order(column)
            return query

        return list(supabase_writer._read_pages(build_query, page_size=1000))

    dataset = {
        "videos": read("videos", "video_id,channel_title,title,view_count,duration,published_at",
                       "video_id"),
        "initial_analysis": read("initial_analysis", _ANALYSIS_COLUMNS, "id"),
        "daily_analysis": read("daily_analysis", _ANALYSIS_COLUMNS, "id"),
        "thumbnail_analysis": read(
            "thumbnail_analysis",
            "video_id,face_count,main_emotion,food_present,couple_present,family_present,"
            "home_visible,luxury_signal,text_overlay,thumbnail_style,camera_distance,ctr_prediction",
            "video_id",
        ),
        "video_scores": read(
            "video_scores",
            "video_id,score_version,source_table,priority_score,recommended_action,"
            + ",".join(_SCORE_FIELDS),
            "video_id", "score_version",
        ),
        "video_snapshots": read(
            "video_snapshots", "video_id,snapshot_date,days_since_publish,growth_pattern",
            "video_id", "snapshot_date",
            filter_query=lambda query: query.in_("days_since_publish", [1, 7, 30]),
        ),
        "own_channel_videos": read(
            "own_channel_videos", "video_id,title,published_at,video_type,view_count", "video_id",
        ),
        "own_channel_analysis": read(
            "own_channel_analysis",
            "video_id,analysis_date,topic,content_structure,title_pattern,predicted_performance,gemini_raw",
            "video_id", "analysis_date",
        ),
        "own_channel_snapshots": read(
            "own_channel_snapshots", "video_id,snapshot_date,week_number,view_count",
            "video_id", "snapshot_date", "week_number", "view_count",
        ),
        "own_channel_hourly_snapshots": read(
            "own_channel_hourly_snapshots", "video_id,snapshot_at,hours_since_upload,view_count",
            "video_id", "hours_since_upload",
        ),
        "trend_sources": [],
        "trend_entities": [],
        "trend_videos": [],
        "entity_mentions": [],
    }
    if not skip_btrack:
        def recent(query):
            return query.gte("published_at", cutoff)

        dataset.update(
            trend_sources=read(
                "trend_sources", "id,source_name,source_ref,category_hint,active,priority", "id",
            ),
            trend_entities=read(
                "trend_entities", "id,name,normalized_name,entity_type,category", "id",
            ),
            trend_videos=read(
                "trend_videos", "video_id,channel_id,channel_title,published_at,view_over_baseline",
                "video_id", filter_query=recent,
            ),
            entity_mentions=read(
                "entity_mentions", "id,entity_id,video_id,channel_id,published_at", "id",
                filter_query=recent,
            ),
        )
    return dataset


def _number(value):
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) else None


def _views(row):
    value = _number(row.get("view_count"))
    return value if value is not None and value >= 0 else None


def _object(value):
    return value if isinstance(value, dict) else {}


def _valid_analysis(row):
    return has_text(row, "topic") and has_text(row.get("gemini_raw"), "topic")


def _analysis_order(row):
    date = parse_iso_datetime(row.get("analysis_date")) or _START_OF_TIME
    row_id = row.get("id")
    row_id = int(row_id) if isinstance(row_id, int) or str(row_id).isdigit() else 0
    return date, row_id, row.get("source_table") == "daily_analysis"


def _latest_analyses(rows, *, valid_only=True):
    latest = {}
    for row in rows:
        video_id = row.get("video_id")
        if not video_id or (valid_only and not _valid_analysis(row)):
            continue
        if video_id not in latest or _analysis_order(row) > _analysis_order(latest[video_id]):
            latest[video_id] = row
    return latest


def _competition_analyses(dataset):
    return [
        {**row, "source_table": table}
        for table in ("initial_analysis", "daily_analysis")
        for row in dataset.get(table, ())
    ]


def _contaminated_daily_scores(video_scores, comp_analyses):
    # A score names its source table, but the available schema has no analysis
    # row reference. Use the newest source row as the provenance proxy.
    daily_rows = (
        row for row in comp_analyses if row.get("source_table") in (None, "daily_analysis")
    )
    latest_daily = _latest_analyses(daily_rows, valid_only=False)
    return {
        index for index, score in enumerate(video_scores)
        if score.get("source_table") == "daily_analysis"
        and not _valid_analysis(latest_daily.get(score.get("video_id"), {}))
    }


def audit_data_quality(dataset) -> dict:
    """Count usable analyses and surface missing or contaminated histories."""
    comp_analyses = _competition_analyses(dataset)
    video_ids = {row["video_id"] for row in dataset.get("videos", ())}
    valid_ids = set(_latest_analyses(comp_analyses)) & video_ids
    daily = dataset.get("daily_analysis", ())
    own = dataset.get("own_channel_analysis", ())
    valid_daily = [row for row in daily if _valid_analysis(row)]
    recent_btrack = sorted(
        dataset.get("trend_videos", ()),
        key=lambda row: parse_iso_datetime(row.get("published_at")) or _START_OF_TIME,
        reverse=True,
    )[:200]
    baseline_ratios = [_number(row.get("view_over_baseline")) for row in recent_btrack]
    baseline_ratios = [value for value in baseline_ratios if value is not None]
    btrack_median = median(baseline_ratios) if baseline_ratios else None
    growth_count = len({
        row["video_id"] for row in dataset.get("video_snapshots", ())
        if row.get("video_id") and has_text(row, "growth_pattern")
    })
    contaminated = len(_contaminated_daily_scores(dataset.get("video_scores", ()), comp_analyses))
    caveats = []
    if btrack_median is not None and btrack_median < 0.3:
        caveats.append("최근 B-track 영상의 기준 대비 조회수 중앙값이 0.3 미만입니다. "
                       "구버전 기준선(stale baseline)의 영향이 있을 수 있습니다.")
    if growth_count == 0:
        caveats.append("growth-pattern 라벨이 비어 있습니다. 성장 패턴 백필이 필요합니다.")
    if contaminated:
        caveats.append(f"실패했거나 누락된 일일 분석과 연결된 전략 점수가 {contaminated}건 있습니다. "
                       "점수 백테스트에서 해당 행을 제외합니다.")
    return {
        "competition_video_count": len(video_ids),
        "valid_competition_analysis_count": len(valid_ids),
        "unanalyzed_competition_video_count": len(video_ids - valid_ids),
        "daily_analysis_count": len(daily),
        "failed_daily_analysis_count": len(daily) - len(valid_daily),
        "duplicate_valid_daily_analysis_count": (
            len(valid_daily) - len({row.get("video_id") for row in valid_daily})
        ),
        "own_analysis_count": len(own),
        "failed_own_analysis_count": sum(not _valid_analysis(row) for row in own),
        "recent_btrack_median_vob": btrack_median,
        "recent_btrack_baseline_n": len(baseline_ratios),
        "growth_pattern_video_count": growth_count,
        "contaminated_daily_score_count": contaminated,
        "caveats": caveats,
    }


def _view_ratios(videos, group_key):
    views_by_group = defaultdict(list)
    for video in videos:
        views = _views(video)
        if video.get(group_key) and views is not None:
            views_by_group[video[group_key]].append(views)
    medians = {group: median(values) for group, values in views_by_group.items()}
    ratios = {}
    for video in videos:
        baseline = medians.get(video.get(group_key))
        views = _views(video)
        if baseline and views is not None:
            ratios[video["video_id"]] = views / baseline
    return ratios, medians


def _hit_summary(ratios, hit_multiplier):
    n = len(ratios)
    hits = sum(value >= hit_multiplier for value in ratios)
    return {
        "n": n, "hit_count": hits, "hit_rate": hits / n if n else None,
        "median_vob": median(ratios) if n else None,
    }


def _normalized(text):
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def _entity_definitions(trend_entities, min_entity_len):
    entities = {}
    aliases = {}
    for row in trend_entities:
        names = {
            _normalized(row[field])
            for field in ("name", "normalized_name") if has_text(row, field)
        }
        canonical = next((_normalized(row[field]) for field in ("normalized_name", "name")
                          if has_text(row, field)), "")
        key = "".join(canonical.split())
        if len(key) < min_entity_len or not any(char.isalpha() for char in key):
            continue
        if key in _STOPWORDS or names & _STOPWORDS or row.get("id") is None:
            continue
        aliases[row["id"]] = key
        definition = entities.setdefault(key, {
            "name": row.get("name") or canonical,
            "entity_type": row.get("entity_type"),
            "category": row.get("category"),
            "names": set(),
        })
        definition["names"].update(names)
    return entities, aliases


def _entity_patterns(names):
    latin, other = [], []
    for name in sorted(names):
        if name.isascii():
            # Korean particles can follow a Latin term. Limit the boundaries
            # to Latin letters, digits and underscores to allow that usage.
            latin.append(r"(?<![a-z0-9_])" + re.escape(name) + r"(?![a-z0-9_])")
        else:
            other.append(re.escape("".join(name.split())))
    return tuple(re.compile("|".join(group)) if group else None for group in (latin, other))


def _competition_search_fields(video, analysis):
    raw = _object(analysis.get("gemini_raw"))
    keywords = analysis.get("title_keywords") or raw.get("title_keywords") or []
    if not isinstance(keywords, (list, tuple)):
        keywords = [keywords]
    values = [video.get("title"), analysis.get("topic"), *keywords]
    return [_normalized(value) for value in values if isinstance(value, str) and value.strip()]


def _channel_first_mentions(mentions):
    first = {}
    for timestamp, channel, _ in sorted(mentions):
        first.setdefault(channel, timestamp)
    return first


def analyze_btrack_to_celeb_lead_lag(
    trend_entities, entity_mentions, trend_videos, trend_sources, comp_videos, comp_analyses,
    *, min_btrack_channels=3, max_lag_days=60, min_entity_len=2, now=None,
) -> dict:
    """Match entity mentions to dated competitor uploads and rank leading sources."""
    if min_btrack_channels < 1 or min_entity_len < 1 or max_lag_days < 0:
        raise ValueError("invalid lead-lag threshold")
    entities, aliases = _entity_definitions(trend_entities, min_entity_len)
    analyses = _latest_analyses(comp_analyses)
    ratios, _ = _view_ratios(comp_videos, "channel_title")
    trend_by_id = {video["video_id"]: video for video in trend_videos}
    competitors = []
    reference = parse_iso_datetime(now)
    latest_date = _START_OF_TIME
    for video in comp_videos:
        timestamp = parse_iso_datetime(video.get("published_at"))
        if timestamp is None or (reference and timestamp > reference):
            continue
        latest_date = max(latest_date, timestamp)
        video_id = video["video_id"]
        fields = _competition_search_fields(video, analyses.get(video_id, {}))
        competitors.append((timestamp, video_id, video.get("channel_title"), video.get("title"),
                            fields, ["".join(field.split()) for field in fields]))

    mentions_by_entity = defaultdict(set)
    for row in entity_mentions:
        video = trend_by_id.get(row.get("video_id"), {})
        timestamp = parse_iso_datetime(row.get("published_at") or video.get("published_at"))
        if timestamp is None or (reference and timestamp > reference):
            continue
        latest_date = max(latest_date, timestamp)
        key = aliases.get(row.get("entity_id"))
        channel_id = row.get("channel_id") or video.get("channel_id")
        if key and channel_id and row.get("video_id"):
            mentions_by_entity[key].add((timestamp, channel_id, row["video_id"]))
    if reference is None:
        for video in trend_videos:
            timestamp = parse_iso_datetime(video.get("published_at"))
            if timestamp:
                latest_date = max(latest_date, timestamp)
        reference = latest_date

    adoptions = []
    timelines = []
    radar = []
    lag_counts = Counter(dict.fromkeys(("btrack_led", "coincident", "celeb_led"), 0))
    lag_observations = {"0-14d": [], "15-30d": [], "31-60d": []}
    if max_lag_days > 60:
        lag_observations[f"61-{max_lag_days}d"] = []
    channel_leads = defaultdict(list)
    for key in sorted(mentions_by_entity):
        mentions = mentions_by_entity[key]
        first_by_channel = _channel_first_mentions(mentions)
        if len(first_by_channel) < min_btrack_channels:
            continue
        first_seen = min(first_by_channel.values())
        spike = list(first_by_channel.values())[min_btrack_channels - 1]
        definition = entities[key]
        latin_pattern, other_pattern = _entity_patterns(definition["names"])
        btrack_ratios = [
            _number(trend_by_id.get(video_id, {}).get("view_over_baseline"))
            for video_id in {video_id for _, _, video_id in mentions}
        ]
        btrack_ratios = [value for value in btrack_ratios if value is not None]
        timeline = {
            "entity": definition["name"], "normalized_name": key,
            "entity_type": definition["entity_type"], "category": definition["category"],
            "first_seen_at": first_seen.isoformat(), "spike_at": spike.isoformat(),
            "channel_count": len(first_by_channel), "mention_count": len(mentions),
            "median_vob": median(btrack_ratios) if btrack_ratios else None,
        }
        timelines.append(timeline)
        matched = False
        for published, video_id, channel_title, title, fields, compact_fields in competitors:
            if not ((latin_pattern and any(latin_pattern.search(field) for field in fields))
                    or (other_pattern and any(other_pattern.search(field) for field in compact_fields))):
                continue
            matched = True
            lag_days = (published - spike).days
            if lag_days > max_lag_days:
                continue
            ratio = ratios.get(video_id)
            is_hit = ratio is not None and ratio >= 2.0
            if lag_days >= 0:
                relation = "btrack_led"
                if lag_days <= 14:
                    bucket = "0-14d"
                elif lag_days <= 30:
                    bucket = "15-30d"
                elif lag_days <= 60:
                    bucket = "31-60d"
                else:
                    bucket = f"61-{max_lag_days}d"
                lag_observations[bucket].append(ratio)
                for channel, first in first_by_channel.items():
                    if first < published:
                        channel_leads[channel].append((key, video_id, (published - first).days, is_hit))
            else:
                relation = "coincident" if lag_days >= -7 else "celeb_led"
            lag_counts[relation] += 1
            adoptions.append({
                "entity": definition["name"], "normalized_name": key,
                "video_id": video_id, "channel_title": channel_title, "title": title,
                "published_at": published.isoformat(), "spike_at": spike.isoformat(),
                "lag_days": lag_days, "relation": relation,
                "view_over_channel_median": ratio, "is_hit": is_hit if ratio is not None else None,
            })
        if not matched:
            cutoff = reference - timedelta(days=30)
            recent = {(date, channel, video_id) for date, channel, video_id in mentions if date >= cutoff}
            recent_channels = {channel for _, channel, _ in recent}
            if len(recent_channels) >= min_btrack_channels:
                radar.append({
                    **timeline, "recent_channel_count": len(recent_channels),
                    "recent_mention_count": len(recent),
                    "last_seen_at": max(date for date, _, _ in recent).isoformat(),
                })

    sources = {row.get("source_ref"): row for row in trend_sources}
    titles = {row.get("channel_id"): row.get("channel_title") for row in trend_videos}
    trendsetters = []
    for channel, leads in channel_leads.items():
        source = sources.get(channel, {})
        trendsetters.append({
            "channel_id": channel, "source_id": source.get("id"),
            "source_name": source.get("source_name") or titles.get(channel) or channel,
            "category_hint": source.get("category_hint"), "active": source.get("active"),
            "priority": source.get("priority"),
            "celeb_hit_lead_count": len({video_id for _, video_id, _, hit in leads if hit}),
            "lead_entity_count": len({key for key, _, _, _ in leads}),
            "median_lead_days": median(days for _, _, days, _ in leads),
        })
    return {
        "min_btrack_channels": min_btrack_channels, "max_lag_days": max_lag_days,
        "relation_counts": dict(lag_counts), "entity_timelines": timelines,
        "adoptions": adoptions,
        "lag_buckets": {
            name: {**_hit_summary([value for value in values if value is not None], 2.0),
                   "adoption_count": len(values)}
            for name, values in lag_observations.items()
        },
        "trendsetters": sorted(trendsetters, key=lambda row: (
            -row["celeb_hit_lead_count"], -row["lead_entity_count"],
            -row["median_lead_days"], row["channel_id"],
        ))[:20],
        "unadopted_radar": sorted(radar, key=lambda row: (
            -row["recent_channel_count"], -row["recent_mention_count"],
            -(row["median_vob"] or 0), row["normalized_name"],
        ))[:20],
    }


def _feature_value(value):
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, bool):
        return value
    return _number(value) if isinstance(value, (int, float)) else None


def _field_value(row, raw, key):
    value = _feature_value(row.get(key))
    return value if value is not None else _feature_value(raw.get(key))


def _length_bucket(seconds):
    if seconds is None or seconds < 300:
        return None
    if seconds < 600:
        return "5-10m"
    if seconds < 1200:
        return "10-20m"
    if seconds < 1800:
        return "20-30m"
    return "30m+"


def _face_bucket(value):
    faces = _number(value)
    if faces is None or faces < 0 or not faces.is_integer():
        return None
    return str(int(faces)) if faces < 3 else "3+"


def _correlation(pairs):
    xs, ys = zip(*pairs) if pairs else ((), ())
    return {"r": spearman_correlation(xs, ys), "n": len(pairs)}


def _lift(summary, baseline):
    rate = summary["hit_rate"]
    return rate / baseline if baseline and rate is not None else None


def _lift_rows(groups, min_samples, hit_multiplier, baseline):
    rows = []
    for value, ratios in groups.items():
        if len(ratios) < min_samples:
            continue
        summary = _hit_summary(ratios, hit_multiplier)
        rows.append({
            "value": list(value) if isinstance(value, tuple) else value, **summary,
            "lift": _lift(summary, baseline), "confidence": confidence_tier(summary["n"]),
        })
    return sorted(rows, key=lambda row: (-row["hit_rate"], -row["n"], str(row["value"])))


def _weight_recommendation(weight, correlation):
    r, n = correlation["r"], correlation["n"]
    aligned_r = -r if r is not None and weight < 0 else r
    if aligned_r is None:
        recommendation = "상관 산출 불가 · 추가 관측 필요"
    elif aligned_r >= 0.15:
        recommendation = "현재 방향 유지 · 가중치 절댓값 상향 검토"
    elif aligned_r <= 0.05:
        recommendation = "가중치 절댓값 축소 또는 설계 재검토"
    else:
        recommendation = "현재 가중치 유지 · 추가 관측"
    if n < 30:
        recommendation += " (표본 부족, n < 30)"
    return {"current_weight": weight, "spearman_r": r, "n": n, "recommendation": recommendation}


def _scorer_backtest(
    video_scores, comp_analyses, analyses, ratios, baseline, hit_multiplier,
    score_version: str | None = None,
):
    resolved_version = score_version or getattr(config, "SCORE_VERSION", "v1.2")
    weights = _SCORE_WEIGHTS_BY_VERSION.get(resolved_version, _SCORE_WEIGHTS_BY_VERSION["v2.0"])
    candidates = [row for row in video_scores if row.get("score_version") in (None, "", resolved_version)]
    contaminated = _contaminated_daily_scores(candidates, comp_analyses)
    valid_initial = {
        row["video_id"] for row in comp_analyses
        if row.get("source_table") in (None, "initial_analysis") and _valid_analysis(row)
        and row.get("video_id")
    }
    scored = {}
    excluded_initial = 0
    excluded_without_analysis = 0
    for index, row in enumerate(candidates):
        video_id = row.get("video_id")
        if index in contaminated:
            continue
        if video_id not in analyses:
            excluded_without_analysis += 1
            continue
        if row.get("source_table") == "initial_analysis" and video_id not in valid_initial:
            excluded_initial += 1
            continue
        if video_id in ratios and (video_id not in scored or row.get("score_version") == resolved_version):
            scored[video_id] = row

    correlations = {}
    for field in ("priority_score", *weights):
        pairs = []
        for video_id, row in scored.items():
            value = _number(row.get(field))
            if value is not None:
                pairs.append((value, ratios[video_id]))
        correlations[field] = _correlation(pairs)
    prioritized = [
        (value, video_id) for video_id, row in scored.items()
        if (value := _number(row.get("priority_score"))) is not None
    ]
    prioritized.sort(key=lambda item: (-item[0], item[1]))
    top_count = math.ceil(len(prioritized) / 5)
    top20 = _hit_summary([ratios[video_id] for _, video_id in prioritized[:top_count]], hit_multiplier)
    actions = defaultdict(list)
    for video_id, row in scored.items():
        actions[row.get("recommended_action")].append(ratios[video_id])
    return {
        "score_version": resolved_version, "eligible_scored_video_count": len(scored),
        "excluded_contaminated_daily_score_count": len(contaminated),
        "excluded_invalid_initial_score_count": excluded_initial,
        "excluded_without_valid_analysis_count": excluded_without_analysis,
        "correlations": correlations,
        "precision_at_top20": {
            **top20, "precision": top20["hit_rate"], "scored_n": len(prioritized),
            "baseline_hit_rate": baseline, "lift": _lift(top20, baseline),
        },
        "recommended_actions": {
            action: _hit_summary(actions[action], hit_multiplier) for action in _ACTIONS
        },
        "weight_recommendations": {
            field: _weight_recommendation(weight, correlations[field])
            for field, weight in weights.items()
        },
    }


def analyze_hit_drivers_and_calibrate_scorer(
    comp_videos, comp_analyses, thumbnail_analyses, video_scores, video_snapshots,
    *, hit_multiplier=2.0, min_samples=5, score_version: str | None = None,
) -> dict:
    """Measure cohort lift and rank correlations against channel view medians."""
    if min_samples < 1 or not math.isfinite(hit_multiplier) or hit_multiplier <= 0:
        raise ValueError("invalid hit-driver threshold")
    ratios, medians = _view_ratios(comp_videos, "channel_title")
    baseline = _hit_summary(list(ratios.values()), hit_multiplier)
    baseline_rate = baseline["hit_rate"]
    analyses = _latest_analyses(comp_analyses)
    thumbnails = {row["video_id"]: row for row in thumbnail_analyses}
    gemini_fields = ("category", "title_pattern", "title_emotion_tone", "content_structure",
                     "ppl_likely", "applicability")
    thumbnail_fields = ("thumbnail_style", "camera_distance", "main_emotion", "food_present",
                        "couple_present", "family_present", "home_visible", "luxury_signal",
                        "text_overlay", "ctr_prediction")
    features = {
        field: defaultdict(list)
        for field in (*gemini_fields, *thumbnail_fields, "face_count_bucket", "ad_boost_suspected",
                      "duration_bucket", "kst_weekday")
    }
    combinations = {(left, right): defaultdict(list) for left, right in (
        ("category", "title_pattern"), ("category", "thumbnail_style"),
    )}
    emotion_pairs = {emotion: [] for emotion in _EMOTIONS}
    ad_boost_pairs = []
    for video in comp_videos:
        video_id = video["video_id"]
        if video_id not in ratios:
            continue
        ratio = ratios[video_id]
        analysis = analyses.get(video_id, {})
        gemini = _object(analysis.get("gemini_raw"))
        comments = _object(analysis.get("deepseek_raw"))
        thumbnail = thumbnails.get(video_id, {})
        values = {key: _field_value(analysis, gemini, key) for key in gemini_fields}
        values.update({key: _feature_value(thumbnail.get(key)) for key in thumbnail_fields})
        values["face_count_bucket"] = _face_bucket(thumbnail.get("face_count"))
        values["duration_bucket"] = _length_bucket(parse_iso_duration_seconds(video.get("duration")))
        published = parse_iso_datetime(video.get("published_at"))
        if published is not None:
            values["kst_weekday"] = "월화수목금토일"[published.astimezone(config.KST).weekday()]
        if "error" not in comments:
            ad_boost = _field_value(analysis, comments, "ad_boost_suspected")
            values["ad_boost_suspected"] = ad_boost
            if isinstance(ad_boost, bool):
                ad_boost_pairs.append((int(ad_boost), ratio))
            temperatures = _object(comments.get("emotion_temperature"))
            for emotion, pairs in emotion_pairs.items():
                value = _number(temperatures.get(emotion))
                if value is not None:
                    pairs.append((value, ratio))
        for key, value in values.items():
            if value is not None:
                features[key][value].append(ratio)
        for (left, right), groups in combinations.items():
            if values[left] is not None and values[right] is not None:
                groups[(values[left], values[right])].append(ratio)

    return {
        "hit_multiplier": hit_multiplier, "min_samples": min_samples,
        "baseline_hit_rate": baseline_rate, "baseline": baseline,
        "channel_medians": medians, "view_over_channel_median": ratios,
        "features": {
            field: _lift_rows(groups, min_samples, hit_multiplier, baseline_rate)
            for field, groups in features.items()
        },
        "combinations": {
            f"{left} × {right}": _lift_rows(groups, min_samples, hit_multiplier, baseline_rate)
            for (left, right), groups in combinations.items()
        },
        "ad_boost_correlation": _correlation(ad_boost_pairs),
        "emotion_temperature_correlations": {
            emotion: _correlation(pairs) for emotion, pairs in emotion_pairs.items()
        },
        "growth_pattern_video_count": len({
            row["video_id"] for row in video_snapshots
            if row.get("video_id") in ratios and has_text(row, "growth_pattern")
        }),
        "scorer_backtest": _scorer_backtest(
            video_scores, comp_analyses, analyses, ratios, baseline_rate, hit_multiplier,
            score_version=score_version,
        ),
    }


def _distribution(values):
    values = sorted(values)
    return {
        "n": len(values),
        **{label: percentile(values, fraction)
           for label, fraction in (("p25", 0.25), ("p50", 0.5), ("p75", 0.75))},
    }


def _checkpoint_observations(snapshots, reference):
    closest = {}
    for row in snapshots:
        hours = _number(row.get("hours_since_upload"))
        views = _views(row)
        observed = parse_iso_datetime(row.get("snapshot_at"))
        if hours is None or views is None or (observed is not None and observed > reference):
            continue
        for target, (start, end) in _CHECKPOINTS.items():
            if start <= hours <= end:
                key = (row["video_id"], target)
                candidate = (abs(hours - target), hours, views)
                if key not in closest or candidate < closest[key]:
                    closest[key] = candidate
    return closest


def _week1_views(snapshots, reference):
    latest = {}
    for row in snapshots:
        views = _views(row)
        date = parse_iso_datetime(row.get("snapshot_date")) or _START_OF_TIME
        if row.get("week_number") != 1 or views is None or date > reference:
            continue
        video_id = row["video_id"]
        candidate = (date, views)
        if video_id not in latest or candidate > latest[video_id]:
            latest[video_id] = candidate
    return {video_id: views for video_id, (_, views) in latest.items()}


def _learn_checkpoint(target, mature_videos, closest, week1):
    early_views, week_ratios, final_ratios, final_pairs = [], [], [], []
    for video in mature_videos:
        video_id = video["video_id"]
        observation = closest.get((video_id, target))
        if observation is None:
            continue
        views = observation[2]
        early_views.append(views)
        final = _views(video)
        if final is not None:
            final_pairs.append((views, final))
            if views > 0:
                final_ratios.append(final / views)
        if views > 0 and video_id in week1:
            week_ratios.append(week1[video_id] / views)
    return {
        "views": _distribution(early_views),
        "week1_multiplier": _distribution(week_ratios),
        "final_multiplier": _distribution(final_ratios),
        "views_vs_final": _correlation(final_pairs),
    }


def _own_analysis_patterns(mature_videos, analyses, type_median):
    features = {key: defaultdict(list) for key in (
        "content_structure", "title_pattern", "predicted_performance",
    )}
    for video in mature_videos:
        analysis = analyses.get(video["video_id"])
        views = _views(video)
        if analysis is None or views is None or not type_median:
            continue
        raw = _object(analysis.get("gemini_raw"))
        for key, groups in features.items():
            value = _field_value(analysis, raw, key)
            if value is not None:
                groups[value].append(views / type_median)
    return {
        key: [{"value": value, "view_over_type_median": _distribution(ratios)}
              for value, ratios in sorted(groups.items(), key=lambda item: str(item[0]))]
        for key, groups in features.items()
    }


def _intervention_candidates(recent, closest, thresholds):
    candidates = []
    for published, video in recent:
        video_id = video["video_id"]
        for target in (24, 6):
            observation = closest.get((video_id, target))
            if observation is None:
                continue
            _, hours, views = observation
            cutoff = thresholds[f"{target}h"]
            if cutoff is not None and views < cutoff:
                candidates.append({
                    "video_id": video_id, "title": video.get("title"), "checkpoint": f"{target}h",
                    "observed_hours": hours, "views": views, "p25": cutoff,
                    "published_at": published.isoformat(),
                })
            break
    return sorted(candidates, key=lambda row: row["published_at"], reverse=True)


def analyze_own_channel_early_trajectory(
    own_videos, own_hourly_snapshots, own_weekly_snapshots, own_analyses,
    *, mature_days=14, now=None,
) -> dict:
    """Learn format-specific checkpoint bands from mature uploads."""
    if mature_days < 1:
        raise ValueError("mature_days must be positive")
    reference = parse_iso_datetime(now) or datetime.now(timezone.utc)
    mature_cutoff = reference - timedelta(days=mature_days)
    closest = _checkpoint_observations(own_hourly_snapshots, reference)
    week1 = _week1_views(own_weekly_snapshots, reference)
    analyses = _latest_analyses(own_analyses)
    mature_by_type = defaultdict(list)
    recent_by_type = defaultdict(list)
    for video in own_videos:
        published = parse_iso_datetime(video.get("published_at"))
        if published is None or published > reference:
            continue
        if published <= mature_cutoff:
            mature_by_type[video.get("video_type")].append(video)
        else:
            recent_by_type[video.get("video_type")].append((published, video))

    by_type = {}
    for video_type in _VIDEO_TYPES:
        mature = mature_by_type[video_type]
        final_views = [views for row in mature if (views := _views(row)) is not None]
        type_median = median(final_views) if final_views else None
        checkpoints = {
            f"{target}h": _learn_checkpoint(target, mature, closest, week1)
            for target in _CHECKPOINTS
        }
        thresholds = {key: checkpoints[key]["views"]["p25"] for key in ("6h", "24h")}
        recent = recent_by_type[video_type]
        by_type[video_type] = {
            "mature_count": len(mature), "immature_count": len(recent),
            "mature_median_views": type_median, "checkpoints": checkpoints,
            "intervention_thresholds": thresholds,
            "intervention_candidates": _intervention_candidates(recent, closest, thresholds),
            "analysis_patterns": _own_analysis_patterns(mature, analyses, type_median),
        }
    return {"mature_days": mature_days, "by_video_type": by_type}


def build_learned_playbook(report, *, min_samples=7) -> dict:
    """Keep only signals with sufficient observations in their own cohort."""
    if min_samples < 1:
        raise ValueError("min_samples must be positive")

    def sufficient(row):
        return row["n"] >= min_samples

    def keep_lift(groups):
        retained = {}
        for feature, rows in groups.items():
            eligible = [
                row for row in rows
                if sufficient(row) and row["lift"] is not None
            ]
            if eligible:
                retained[feature] = eligible
        return retained

    btrack = report["btrack_lead_lag"]
    drivers = report["hit_drivers"]
    backtest = drivers["scorer_backtest"]
    own = {}
    for video_type, row in report["own_channel_trajectory"]["by_video_type"].items():
        checkpoints = {}
        for label, statistics in row["checkpoints"].items():
            eligible = {
                key: value for key, value in statistics.items()
                if sufficient(value) and (key != "views_vs_final" or value["r"] is not None)
            }
            if eligible:
                checkpoints[label] = eligible
        if checkpoints:
            own[video_type] = checkpoints
    return {
        "min_samples": min_samples,
        "lead_lag": {key: value for key, value in btrack.get("lag_buckets", {}).items()
                     if sufficient(value)},
        "hit_drivers": keep_lift(drivers["features"]),
        "combinations": keep_lift(drivers["combinations"]),
        "emotion_temperature": {
            key: row for key, row in drivers["emotion_temperature_correlations"].items()
            if sufficient(row) and row["r"] is not None
        },
        "scorer_weights": {
            key: row for key, row in backtest["weight_recommendations"].items()
            if sufficient(row) and row["spearman_r"] is not None
        },
        "own_channel_checkpoints": own,
    }


def _number_label(value, suffix=""):
    return "—" if value is None else f"{value:,.2f}{suffix}"


def _rate_label(value):
    return "—" if value is None else f"{value:.1%}"


def _cell(value):
    if value is None:
        return "—"
    if isinstance(value, list):
        value = " × ".join(map(str, value))
    return (" ".join(str(value).split()).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace("|", r"\|"))


def _table(lines, headers, rows):
    rows = list(rows)
    if not rows:
        lines.extend(("관측 자료가 없습니다.", ""))
        return
    lines.append("| " + " | ".join(headers) + " |")
    lines.append("| " + " | ".join("---" for _ in headers) + " |")
    for row in rows:
        lines.append("| " + " | ".join(_cell(value) for value in row) + " |")
    lines.append("")


def _hit_cells(row):
    return row["n"], row["hit_count"], _rate_label(row["hit_rate"]), _number_label(row["median_vob"], "배")


def _lift_table(lines, groups):
    _table(lines, ("피처", "값", "표본", "히트", "히트율", "중앙 배수", "리프트", "표본 등급"), (
        (feature, row["value"], *_hit_cells(row), _number_label(row["lift"], "배"), row["confidence"])
        for feature, rows in groups.items() for row in rows
    ))


def _render_btrack(lines, btrack):
    if btrack.get("skipped"):
        lines.extend(("이번 실행에서는 B-track을 생략했습니다.", ""))
        return
    counts = btrack["relation_counts"]
    lines.extend((
        f"- B-track 선행 {counts['btrack_led']}건 · 동시 {counts['coincident']}건 · "
        f"셀럽 선행 {counts['celeb_led']}건",
        "- 집계 단위는 엔티티와 채택 영상의 쌍입니다. 히트율은 채널 중앙 조회수 대비 "
        "배수를 산출한 영상 중 2배 이상인 비율입니다.",
        "",
    ))
    _table(lines, ("선행 시차", "채택", "표본", "히트", "히트율", "중앙 배수"), (
        (bucket, row["adoption_count"], *_hit_cells(row))
        for bucket, row in btrack["lag_buckets"].items()
    ))
    adoptions = sorted(
        btrack["adoptions"], key=lambda row: row["published_at"], reverse=True,
    )[:10]
    if adoptions:
        lines.extend(("### 최근 채택 사례", ""))
        _table(lines, ("엔티티", "경쟁 영상", "시차(일)", "채널 중앙 대비 조회수"), (
            (row["entity"], row["title"] or row["video_id"], row["lag_days"],
             _number_label(row["view_over_channel_median"], "배"))
            for row in adoptions
        ))
    lines.extend(("### 트렌드세터 채널", ""))
    _table(lines, ("채널", "카테고리", "선행 히트 영상", "선행 엔티티", "중앙 선행 일수"), (
        (row["source_name"], row["category_hint"], row["celeb_hit_lead_count"],
         row["lead_entity_count"], _number_label(row["median_lead_days"]))
        for row in btrack["trendsetters"]
    ))
    lines.extend(("### 미채택 레이더 · 최근 30일", ""))
    _table(lines, ("엔티티", "카테고리", "채널", "언급", "기준 대비 중앙 조회수"), (
        (row["entity"], row["category"], row["recent_channel_count"],
         row["recent_mention_count"], _number_label(row["median_vob"], "배"))
        for row in btrack["unadopted_radar"]
    ))


def _render_backtest(lines, backtest):
    top = backtest["precision_at_top20"]
    lines.extend((
        f"- Precision@Top20% {_rate_label(top['precision'])} "
        f"({top['hit_count']}/{top['n']}건, 점수 산출 영상 {top['scored_n']}개) · "
        f"전체 히트율 {_rate_label(top['baseline_hit_rate'])} · 리프트 {_number_label(top['lift'], '배')}",
        f"- 오염 일일 점수 {backtest['excluded_contaminated_daily_score_count']}건 · "
        f"유효 초기 원본이 없는 점수 {backtest['excluded_invalid_initial_score_count']}건 · "
        f"유효 분석이 없는 기타 점수 {backtest['excluded_without_valid_analysis_count']}건 제외",
        "- 저장된 점수에는 원본 분석 행 ID가 없어 일일 점수의 출처 검증은 최신 일일 분석 행을 "
        "기준으로 합니다. 상관계수는 표본 내 관측치이며 보정 제안은 후속 검증이 필요합니다.",
        "",
    ))
    _table(lines, ("추천 등급", "표본", "히트", "히트율", "중앙 배수"), (
        (action, *_hit_cells(row)) for action, row in backtest["recommended_actions"].items()
    ))
    weights = backtest["weight_recommendations"]
    _table(lines, ("점수", "표본", "Spearman r", "현 가중치", "보정 제안"), (
        (key, row["n"], _number_label(row["r"]),
         _number_label(weights.get(key, {}).get("current_weight")),
         weights.get(key, {}).get("recommendation"))
        for key, row in backtest["correlations"].items()
    ))


def _band(row):
    return " / ".join(_number_label(row[key]) for key in ("p25", "p50", "p75"))


def _render_own_channel(lines, own):
    lines.extend((
        f"- 업로드 후 {own['mature_days']}일 이상 지난 영상으로 벤치마크를 계산합니다. "
        "6h·24h 중 최근 관측치가 해당 p25보다 낮은 미성숙 영상은 제목·썸네일 검토 후보입니다.",
        "",
    ))
    for video_type, label in (("longform", "롱폼"), ("shorts", "쇼츠")):
        data = own["by_video_type"][video_type]
        lines.extend((f"### {label} · 성숙 {data['mature_count']}개 / 미성숙 {data['immature_count']}개", ""))
        _table(lines, ("시점", "조회수 표본", "초기 조회수 p25/p50/p75", "1주 배수 p25/p50/p75",
                       "최신 배수 p25/p50/p75", "Spearman r (n)"), (
            (key, row["views"]["n"], _band(row["views"]),
             f"{_band(row['week1_multiplier'])} (n={row['week1_multiplier']['n']})",
             f"{_band(row['final_multiplier'])} (n={row['final_multiplier']['n']})",
             f"{_number_label(row['views_vs_final']['r'])} ({row['views_vs_final']['n']})")
            for key, row in data["checkpoints"].items()
        ))
        lines.extend((
            "- 개입 검토 기준 "
            + " · ".join(f"{key} {_number_label(p25)}" for key, p25 in data["intervention_thresholds"].items()),
            "",
        ))
        _table(lines, ("개입 검토 후보", "시점", "관측 시간(h)", "조회수", "p25"), (
            (row["title"] or row["video_id"], row["checkpoint"], row["observed_hours"],
             _number_label(row["views"]), _number_label(row["p25"]))
            for row in data["intervention_candidates"]
        ))
        _table(lines, ("분석 피처", "값", "표본", "영상 유형 중앙값 대비 배수 p25/p50/p75"), (
            (key, row["value"], row["view_over_type_median"]["n"], _band(row["view_over_type_median"]))
            for key, rows in data["analysis_patterns"].items() for row in rows
        ))


def _playbook_rows(playbook):
    for section in ("hit_drivers", "combinations"):
        for key, rows in playbook[section].items():
            for row in rows:
                value = row["value"]
                if isinstance(value, list):
                    value = " × ".join(map(str, value))
                yield "히트 리프트", f"{key} · {value}", row["n"], _number_label(row["lift"], "배")
    for key, row in playbook["lead_lag"].items():
        yield "B-track 선행", key, row["n"], _rate_label(row["hit_rate"])
    for key, row in playbook["emotion_temperature"].items():
        yield "감정온도", key, row["n"], f"r={_number_label(row['r'])}"
    for key, row in playbook["scorer_weights"].items():
        yield "전략 점수", key, row["n"], row["recommendation"]
    for video_type, checkpoints in playbook["own_channel_checkpoints"].items():
        for checkpoint, statistics in checkpoints.items():
            for key, row in statistics.items():
                value = f"r={_number_label(row['r'])}" if key == "views_vs_final" else _band(row)
                yield "자체 채널", f"{video_type} · {checkpoint} · {key}", row["n"], value


def render_markdown_report(report) -> str:
    """Render the six Korean report sections from a computed report."""
    audit = report["data_quality"]
    drivers = report["hit_drivers"]
    playbook = report["learned_playbook"]
    lines = [
        "# 패턴·학습 리포트", "",
        "## 1. 데이터 품질 및 해석 주의사항", "",
        f"- 경쟁 영상 {audit['competition_video_count']}개 · "
        f"유효 분석 {audit['valid_competition_analysis_count']}개 · "
        f"미분석 {audit['unanalyzed_competition_video_count']}개",
        f"- 일일 분석 {audit['daily_analysis_count']}행 · 실패 {audit['failed_daily_analysis_count']}행 · "
        f"중복 유효 {audit['duplicate_valid_daily_analysis_count']}행",
        f"- 자체 채널 분석 {audit['own_analysis_count']}행 · 실패 {audit['failed_own_analysis_count']}행",
        f"- 최근 B-track 기준 대비 조회수 중앙값 {_number_label(audit['recent_btrack_median_vob'], '배')} "
        f"(n={audit['recent_btrack_baseline_n']})",
        f"- 성장 패턴 라벨 영상 {audit['growth_pattern_video_count']}개 (1·7·30일 스냅샷 기준)",
        *(f"- {warning}" for warning in audit["caveats"]),
        "",
        "## 2. B-track → 셀럽 Lead-Lag 및 트렌드세터 채널", "",
    ]
    _render_btrack(lines, report["btrack_lead_lag"])
    lines.extend((
        "## 3. 경쟁 채널 히트 드라이버", "",
        f"- 히트 기준은 채널 중앙 조회수의 {drivers['hit_multiplier']:g}배 이상입니다. "
        f"전체 히트율은 {_rate_label(drivers['baseline_hit_rate'])} "
        f"({drivers['baseline']['hit_count']}/{drivers['baseline']['n']}개)입니다.",
        "",
        "### 카테고리·제목·썸네일·영상 길이·업로드 요일", "",
    ))
    _lift_table(lines, drivers["features"])
    lines.extend(("### 카테고리와 제목·썸네일 조합", ""))
    _lift_table(lines, drivers["combinations"])
    lines.extend(("### 댓글 감정온도와 광고 유입 의심", ""))
    _table(lines, ("지표", "표본", "Spearman r"), (
        (key, row["n"], _number_label(row["r"]))
        for key, row in {"ad_boost_suspected": drivers["ad_boost_correlation"],
                         **drivers["emotion_temperature_correlations"]}.items()
    ))
    backtest = drivers["scorer_backtest"]
    lines.extend((f"## 4. strategy_scorer {backtest['score_version']} 백테스트 및 가중치 보정 제안", ""))
    _render_backtest(lines, backtest)
    lines.extend(("## 5. 자체 채널(고은언니 한고은) 조기 궤적 모델", ""))
    _render_own_channel(lines, report["own_channel_trajectory"])
    lines.extend((f"## 6. 검증된 학습 플레이북 (n ≥ {playbook['min_samples']})", ""))
    _table(lines, ("영역", "관측 신호", "표본", "요약"), _playbook_rows(playbook))
    return "\n".join(lines)


def run_pattern_report(
    *, client=None, lookback_days=210, min_samples=7, skip_btrack=False, now=None,
    out=None, json_out=None, score_version: str | None = None,
) -> dict:
    """Load and analyze the database, optionally writing local report files."""
    if min_samples < 1:
        raise ValueError("min_samples must be positive")
    reference = parse_iso_datetime(now) or datetime.now(timezone.utc)
    dataset = load_dataset_from_supabase(
        client, lookback_days=lookback_days, skip_btrack=skip_btrack, now=reference,
    )
    videos = dataset["videos"]
    analyses = _competition_analyses(dataset)
    btrack = {"skipped": True} if skip_btrack else analyze_btrack_to_celeb_lead_lag(
        dataset["trend_entities"], dataset["entity_mentions"], dataset["trend_videos"],
        dataset["trend_sources"], videos, analyses, now=reference,
    )
    report = {
        "generated_at": reference.isoformat(), "lookback_days": lookback_days,
        "data_quality": audit_data_quality(dataset),
        "btrack_lead_lag": btrack,
        "hit_drivers": analyze_hit_drivers_and_calibrate_scorer(
            videos, analyses, dataset["thumbnail_analysis"], dataset["video_scores"],
            dataset["video_snapshots"], min_samples=min_samples, score_version=score_version,
        ),
        "own_channel_trajectory": analyze_own_channel_early_trajectory(
            dataset["own_channel_videos"], dataset["own_channel_hourly_snapshots"],
            dataset["own_channel_snapshots"], dataset["own_channel_analysis"], now=reference,
        ),
    }
    report["learned_playbook"] = build_learned_playbook(report, min_samples=min_samples)
    if out is not None:
        _write_report(out, render_markdown_report(report))
    if json_out is not None:
        _write_report(json_out, json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    return report


def _write_report(destination, content):
    path = Path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="읽기 전용 YouTube 패턴·학습 리포트")
    parser.add_argument("--out", type=Path, default=Path("docs/learned-patterns-report.md"))
    parser.add_argument("--json-out", type=Path)
    parser.add_argument("--lookback-days", type=int, default=210)
    parser.add_argument("--min-samples", type=int, default=7)
    parser.add_argument(
        "--score-version", choices=sorted(_SCORE_WEIGHTS_BY_VERSION),
        help="백테스트 점수 버전 (예: v1.2, v2.0). 기본값은 config.SCORE_VERSION.",
    )
    parser.add_argument("--skip-btrack", action="store_true")
    args = parser.parse_args(argv)
    if args.lookback_days < 1 or args.min_samples < 1:
        parser.error("--lookback-days 및 --min-samples는 양수여야 합니다")
    if args.json_out is not None and args.out.resolve() == args.json_out.resolve():
        parser.error("--out과 --json-out에는 서로 다른 경로를 지정해야 합니다")
    try:
        run_pattern_report(**vars(args))
    except Exception as error:
        parser.exit(1, f"패턴 리포트 생성 실패 · {safe_error(error)}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
