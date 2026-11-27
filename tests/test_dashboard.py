"""대시보드 단위 테스트 — 실제 DB 접속 없이 인메모리 Fake Supabase 로 검증.

실행: python3 -m unittest discover -s tests -p "test_dashboard.py" -v
"""

import copy
import os
import sys
import unittest
import warnings
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
DASHBOARD = ROOT / "dashboard"
if str(DASHBOARD) not in sys.path:
    sys.path.insert(0, str(DASHBOARD))

warnings.filterwarnings("ignore")
import logging  # noqa: E402

logging.getLogger("streamlit").setLevel(logging.ERROR)

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from lib import constants, queries, transforms  # noqa: E402
from lib.constants import OWN_CHANNEL  # noqa: E402


# ─────────────────────────────────────────────────────────
# Fake Supabase (PostgREST 체인 흉내)
# ─────────────────────────────────────────────────────────
class _Result:
    def __init__(self, data):
        self.data = data


class FakeQuery:
    def __init__(self, client, table):
        self.client = client
        self.table_name = table
        self.filters = []
        self.orders = []
        self._limit = None
        self._range = None

    def _log(self, op, *args):
        self.client.calls.append((self.table_name, op, args))
        return self

    def select(self, columns="*", **_):
        return self._log("select", columns)

    def eq(self, col, val):
        self.filters.append(lambda r: r.get(col) == val)
        return self._log("eq", col, val)

    def neq(self, col, val):
        self.filters.append(lambda r: r.get(col) != val)
        return self._log("neq", col, val)

    def _cmp(self, op, col, val, fn):
        self.filters.append(lambda r: r.get(col) is not None and fn(str(r.get(col)), str(val)))
        return self._log(op, col, val)

    def gte(self, col, val):
        return self._cmp("gte", col, val, lambda a, b: a >= b)

    def gt(self, col, val):
        return self._cmp("gt", col, val, lambda a, b: a > b)

    def lte(self, col, val):
        return self._cmp("lte", col, val, lambda a, b: a <= b)

    def lt(self, col, val):
        return self._cmp("lt", col, val, lambda a, b: a < b)

    def in_(self, col, vals):
        vals = set(vals)
        self.filters.append(lambda r: r.get(col) in vals)
        return self._log("in_", col, tuple(vals))

    def order(self, col, desc=False, nullsfirst=None, **_):
        self.orders.append((col, desc))
        return self._log("order", col, desc)

    def limit(self, n):
        self._limit = n
        return self._log("limit", n)

    def range(self, start, end):
        self._range = (start, end)
        return self._log("range", start, end)

    def execute(self):
        if self.table_name in self.client.fail_tables:
            raise RuntimeError(f"relation {self.table_name} does not exist")
        rows = [copy.deepcopy(r) for r in self.client.tables.get(self.table_name, [])]
        rows = [r for r in rows if all(f(r) for f in self.filters)]
        for col, desc in reversed(self.orders):
            present = [r for r in rows if r.get(col) is not None]
            missing = [r for r in rows if r.get(col) is None]
            present.sort(key=lambda r: r.get(col), reverse=desc)
            rows = present + missing
        if self._range is not None:
            start, end = self._range
            rows = rows[start:end + 1]
        if self._limit is not None:
            rows = rows[: self._limit]
        self.client.executions.append((self.table_name, len(rows)))
        return _Result(rows)


class FakeClient:
    def __init__(self, tables=None, fail_tables=()):
        self.tables = tables or {}
        self.fail_tables = set(fail_tables)
        self.calls = []
        self.executions = []

    def table(self, name):
        return FakeQuery(self, name)

    def ops(self, table, op):
        return [args for t, o, args in self.calls if t == table and o == op]


# ─────────────────────────────────────────────────────────
# 샘플 데이터
# ─────────────────────────────────────────────────────────
NOW = datetime.now(timezone.utc)
TODAY = NOW.astimezone(transforms.KST).date()


def _iso(days_ago=0, hours_ago=0):
    return (NOW - timedelta(days=days_ago, hours=hours_ago)).isoformat()


def _date(days_ago=0):
    return (TODAY - timedelta(days=days_ago)).isoformat()


def sample_tables():
    channels = ["이민정 MJ", "자유부인 한가인", "오늘의 지혜"]
    videos, scores, thumbs, initial = [], [], [], []
    for ci, ch in enumerate(channels):
        for i in range(8):
            vid = f"v{ci}{i}"
            views = 10_000 * (i + 1) * (3 if i == 7 else 1)
            videos.append({"video_id": vid, "channel_title": ch, "title": f"{ch} 브이로그 {i} 데님 자켓" if i == 0 else f"{ch} 토크 {i}",
                           "published_at": _iso(i * 3), "view_count": views, "like_count": 100, "comment_count": 10,
                           "duration": "PT12M", "thumbnail_url": None, "video_url": None, "collected_at": _iso(0, 2)})
            for version in ("v2.0", "v1.2"):
                p = 40 + i * 6 + (5 if version == "v2.0" else 0)
                action = "바로 기획화" if p >= 75 else "각색 후 기획" if p >= 60 else "아이디어 보관" if p >= 48 else "우선순위 낮음"
                scores.append({"video_id": vid, "score_version": version, "source_table": "initial_analysis",
                               "scored_at": _date(0), "priority_score": p, "recommended_action": action,
                               "hangoeun_fit_score": 70, "execution_score": 60, "repeatability_score": 55,
                               "novelty_score": 65, "risk_score": 20, "ppl_potential_score": 50 + i,
                               "trend_lifespan_score": 60, "upload_delay_risk": 40, "evergreen_score": 55,
                               "content_lifespan_type": "EVERGREEN", "strategy_reason": "적합도가 높고.",
                               "rule_trace": {"novelty": ["기본값 35", "content_structure=토크형 +20"]}})
            thumbs.append({"video_id": vid, "face_count": 2 if i % 2 else 1, "main_emotion": "놀람" if i == 7 else "기쁨",
                           "couple_present": i % 3 == 0, "food_present": i == 1})
            initial.append({"id": len(initial) + 1, "video_id": vid, "analysis_date": _date(10),
                            "category": "라이프스타일" if i % 2 else "토크", "title_pattern": "단정형" if i > 4 else "질문형",
                            "ppl_likely": i % 2 == 0, "ad_boost_suspected": i == 3, "ad_boost_score": 10,
                            "content_structure": "토크형" if i % 2 else "브이로그형", "title_emotion_tone": "놀람",
                            "emotion_temperature": {"envy": 30 if i > 4 else 5, "aspiration": 25, "fatigue": 10, "cringe": 2}})
    # 자체 채널 영상이 videos 에 섞여 들어와도 제외되어야 한다
    videos.append({"video_id": "own_leak", "channel_title": OWN_CHANNEL, "title": "자체 영상", "published_at": _iso(1),
                   "view_count": 999_999})
    scores.append({"video_id": "own_leak", "score_version": "v2.0", "priority_score": 99,
                   "recommended_action": "바로 기획화"})

    own = [{"video_id": f"o{i}", "title": f"고은언니 영상 {i}", "published_at": _iso(i * 4),
            "video_type": "shorts" if i % 3 == 0 else "longform", "view_count": 50_000 + i * 1000,
            "like_count": 500, "comment_count": 40, "tracking_active": i < 3} for i in range(6)]
    hourly = [{"video_id": f"o{i}", "snapshot_at": _iso(i * 4), "hours_since_upload": h,
               "view_count": int(1000 * h * (i + 1)), "view_growth": 100} for i in range(6) for h in (1, 3.1, 6, 12.5, 24)]
    weekly = [{"video_id": f"o{i}", "snapshot_date": _date(i), "week_number": w, "view_count": 10_000 * w,
               "view_growth": 2000} for i in range(6) for w in (1, 2, 3)]
    own_analysis = [{"video_id": "o1", "analysis_date": _date(1), "predicted_performance": "HIGH",
                     "strengths": '["담백함", "진정성"]', "improvement_points": ["썸네일 텍스트"],
                     "next_video_requests": ["쇼핑 브이로그"], "praise_points": None, "complaints": [],
                     "sentiment": {"positive": 90}, "content_structure": "토크형"}]

    entity_rows, cards = [], []
    for day_offset, scored in ((0, _date(0)), (1, _date(1))):
        for j, (name, stage) in enumerate([("데님 자켓", "breakout"), ("발레코어", "rising"), ("바라클라바", "rising"),
                                           ("로퍼", "mainstream"), ("니트 베스트", "seed"), ("어글리 슈즈", "fatigue")]):
            entity_rows.append({"entity_id": j + 1, "entity_name": name, "entity_type": "item", "category": "fashion",
                                "scored_at": scored, "trend_stage": stage, "distinct_channels_30d": 10 - j,
                                "mention_count_30d": 20 - j, "median_view_over_baseline": 2.5 - j * 0.3,
                                "best_format": ["styling", "haul", "lookbook", "review", "vlog", "routine"][j],
                                "recommended_action": "기획 후보" if stage == "rising" else "관찰",
                                "commerce_signal": 0.5, "shopping_intent_score": 0.4, "ppl_potential": 0.6,
                                "originality_room": 0.7, "engagement_score": 0.3, "item_heat_score": 0.5,
                                "channel_fit_score": 0.6, "production_difficulty": 0.4,
                                "suggested_angle": f"{name} 코디", "best_examples": []})
            cards.append({"entity_id": j + 1, "entity_name": name, "card_date": scored, "question_type": "entity_reaction",
                          "answer_summary": f"{name} 요약 d{day_offset}", "evidence_bullets": ["근거"],
                          "caution_bullets": ["주의"], "recommended_content_ideas": ["아이디어"],
                          "best_examples": [{"title": "예시", "video_url": "https://youtu.be/x", "view_over_baseline": 3.1}]})

    latest_cluster = NOW - timedelta(hours=1)
    clusters = [
        {"id": 1, "cluster_name": "발레코어", "category": "fashion", "trend_stage": "rising", "mention_count_7d": 12,
         "distinct_channels_7d": 6, "total_views_7d": 100_000, "velocity_score": 0.3, "computed_at": latest_cluster.isoformat()},
        {"id": 2, "cluster_name": "로퍼", "category": "fashion", "trend_stage": "mainstream", "mention_count_7d": 30,
         "distinct_channels_7d": 15, "total_views_7d": 300_000, "velocity_score": 0.6,
         "computed_at": (latest_cluster - timedelta(minutes=5)).isoformat()},
        {"id": 3, "cluster_name": "옛날 클러스터", "category": "beauty", "trend_stage": "early", "mention_count_7d": 2,
         "distinct_channels_7d": 1, "total_views_7d": 100, "velocity_score": 0.04,
         "computed_at": (latest_cluster - timedelta(days=5)).isoformat()},
    ]
    return {
        "videos": videos, "video_scores": scores, "thumbnail_analysis": thumbs,
        "initial_analysis": initial, "daily_analysis": [],
        "own_channel_videos": own, "own_channel_hourly_snapshots": hourly, "own_channel_snapshots": weekly,
        "own_channel_analysis": own_analysis,
        "weekly_market_state": [{"report_date": _date(2), "dominant_emotion": "위안", "market_summary": "요약",
                                 "hangoeun_opportunity_score": 72, "comfort_content_score": 64,
                                 "rising_topics": ["친밀 리얼리티"], "hangoeun_recommended_topics": ["30년 지기 밥상"],
                                 "competitive_gap": ["손작업 취미"], "oversaturated_formats": None}],
        "weekly_reports": [{"report_date": _date(2), "week_summary": "주간 요약", "next_video_suggestions": ["제안"],
                            "actionable_insights_for_hangoeun": "단일 문자열 인사이트"}],
        "video_snapshots": [{"id": i, "video_id": f"v0{i}", "channel_title": "이민정 MJ", "snapshot_date": _date(1),
                             "growth_pattern": ["EVERGREEN", "LONGTAIL", "STEADY", "FAST_DECAY"][i % 4]} for i in range(8)],
        "trend_classified": [{"id": 1, "analysis_date": _date(0), "track": "A", "video_id": "v01", "title": "급상승",
                              "channel_title": "이민정 MJ", "view_count": 1000, "track_score_a": 80, "track_score_b": 0,
                              "matched_keywords": ["가족"], "irregular_reasons": []},
                             {"id": 2, "analysis_date": _date(0), "track": "IRREGULAR", "video_id": "t1",
                              "title": "이상 급등", "channel_title": "x", "view_count": 5000,
                              "irregular_reasons": ["조회 급증"]}],
        "trend_entity_scores": entity_rows, "trend_answer_cards": cards, "trend_clusters": clusters,
        "trend_sources": [{"id": i, "source_type": "youtube_channel", "source_name": f"src{i}",
                           "category_hint": "fashion,lifestyle" if i % 2 else "beauty", "priority": 1,
                           "active": i != 0, "last_collected_at": _iso(0, 3), "total_collected": 10 * i} for i in range(5)],
        "trend_videos": [{"video_id": f"t{i}", "channel_title": "패션채널", "title": f"룩북 {i}", "published_at": _iso(i),
                          "view_count": 1000 * i, "channel_avg_views": 500, "view_over_baseline": float(i),
                          "video_type": "shorts" if i % 2 else "longform"} for i in range(6)],
    }


class DashboardTestCase(unittest.TestCase):
    def setUp(self):
        st.cache_data.clear()


# ─────────────────────────────────────────────────────────
# 1) 페이지네이션
# ─────────────────────────────────────────────────────────
class ReadPagesTest(DashboardTestCase):
    def test_reads_all_rows_beyond_postgrest_1000_cap(self):
        client = FakeClient({"big": [{"id": i} for i in range(1203)]})
        rows = queries._read_pages(lambda: client.table("big").select("id").order("id"), page_size=500)
        self.assertEqual(len(rows), 1203)
        self.assertEqual([r["id"] for r in rows], list(range(1203)))
        self.assertEqual(client.ops("big", "range"), [(0, 499), (500, 999), (1000, 1499)])

    def test_exact_multiple_issues_one_extra_empty_page(self):
        client = FakeClient({"big": [{"id": i} for i in range(1000)]})
        rows = queries._read_pages(lambda: client.table("big").select("id"), page_size=500)
        self.assertEqual(len(rows), 1000)
        self.assertEqual(len(client.ops("big", "range")), 3)

    def test_max_rows_caps_reads(self):
        client = FakeClient({"big": [{"id": i} for i in range(2000)]})
        rows = queries._read_pages(lambda: client.table("big").select("id"), page_size=500, max_rows=700)
        self.assertEqual(len(rows), 700)

    def test_empty_table(self):
        client = FakeClient({"big": []})
        self.assertEqual(queries._read_pages(lambda: client.table("big").select("id")), [])

    def test_competitor_videos_paginates(self):
        rows = [{"video_id": f"v{i}", "channel_title": "이민정 MJ", "published_at": _iso(i % 30)} for i in range(1300)]
        client = FakeClient({"videos": rows})
        df = queries.get_competitor_videos(client)
        self.assertEqual(len(df), 1300)


# ─────────────────────────────────────────────────────────
# 2) Track A 점수 버전 분리 / 자체 채널 제외
# ─────────────────────────────────────────────────────────
class StrategyScoreTest(DashboardTestCase):
    def setUp(self):
        super().setUp()
        self.client = FakeClient(sample_tables())

    def test_default_version_is_v2_and_matches_config(self):
        self.assertEqual(constants.DEFAULT_SCORE_VERSION, "v2.0")
        df = queries.get_strategy_scores(self.client)
        self.assertFalse(df.empty)
        self.assertEqual(set(df["score_version"]), {"v2.0"})
        self.assertIn(("score_version", "v2.0"), self.client.ops("video_scores", "eq"))

    def test_versions_never_mix(self):
        v12 = queries.get_strategy_scores(self.client, "v1.2")
        v20 = queries.get_strategy_scores(self.client, "v2.0")
        self.assertEqual(set(v12["score_version"]), {"v1.2"})
        self.assertEqual(set(v20["score_version"]), {"v2.0"})
        self.assertEqual(v12["video_id"].is_unique, True)
        self.assertEqual(len(v12), len(v20))

    def test_score_versions_listed(self):
        self.assertEqual(queries.get_score_versions(self.client), ["v2.0", "v1.2"])

    def test_scores_joined_with_signals_and_sorted(self):
        df = queries.get_strategy_scores(self.client)
        for col in ("title", "channel_title", "couple_present", "face_count", "envy", "fatigue", "ad_boost_suspected"):
            self.assertIn(col, df.columns)
        self.assertTrue(df["priority_score"].is_monotonic_decreasing)

    def test_own_channel_excluded_from_competitor_views(self):
        videos = queries.get_competitor_videos(self.client)
        self.assertNotIn(OWN_CHANNEL, set(videos["channel_title"]))
        self.assertIn(("channel_title", OWN_CHANNEL), self.client.ops("videos", "neq"))
        scores = queries.get_strategy_scores(self.client)
        self.assertNotIn("own_leak", set(scores["video_id"]))

    def test_exclude_own_channel_client_side(self):
        df = pd.DataFrame({"channel_title": [OWN_CHANNEL, " 고은언니 한고은 ", "이민정 MJ", None]})
        out = transforms.exclude_own_channel(df)
        self.assertEqual(len(out), 2)
        self.assertEqual(out["channel_title"].iloc[0], "이민정 MJ")
        self.assertTrue(pd.isna(out["channel_title"].iloc[1]))

    def test_own_videos_labelled_with_new_channel_name(self):
        own = queries.get_own_videos(self.client)
        self.assertEqual(set(own["channel_title"]), {"고은언니 한고은"})
        self.assertNotIn("한고은 daily", OWN_CHANNEL)

    def test_missing_table_returns_empty(self):
        client = FakeClient({}, fail_tables={"video_scores"})
        self.assertTrue(queries.get_strategy_scores(client).empty)

    def test_constants_match_pipeline_config(self):
        try:
            sys.path.insert(0, str(ROOT))
            import config  # noqa: WPS433
        except Exception as exc:  # pragma: no cover - 환경 의존
            self.skipTest(f"config import 불가: {exc}")
        finally:
            if sys.path[0] == str(ROOT):
                sys.path.pop(0)
        self.assertEqual(config.OWN_CHANNEL, constants.OWN_CHANNEL)
        self.assertEqual(config.SCORE_VERSION, constants.DEFAULT_SCORE_VERSION)
        self.assertEqual(tuple(config.TARGET_CHANNELS), constants.COMPETITOR_CHANNELS)
        self.assertEqual(len(constants.COMPETITOR_CHANNELS), 16)


# ─────────────────────────────────────────────────────────
# 3) Track B 최신 스냅샷 조회
# ─────────────────────────────────────────────────────────
class TrackBQueryTest(DashboardTestCase):
    def setUp(self):
        super().setUp()
        self.client = FakeClient(sample_tables())

    def test_entity_scores_latest_snapshot_only(self):
        df = queries.get_b_track_entity_scores(self.client)
        self.assertEqual(set(df["scored_at"]), {_date(0)})
        self.assertEqual(len(df), 6)
        self.assertIn(("scored_at", _date(0)), self.client.ops("trend_entity_scores", "eq"))
        selects = self.client.ops("trend_entity_scores", "select")
        self.assertTrue(all(args[0] != "*" for args in selects), "명시 컬럼 select 필요")

    def test_entity_scores_explicit_date(self):
        df = queries.get_b_track_entity_scores(self.client, scored_at=_date(1))
        self.assertEqual(set(df["scored_at"]), {_date(1)})

    def test_answer_cards_latest_card_date(self):
        df = queries.get_b_track_answer_cards(self.client)
        self.assertEqual(set(df["card_date"]), {_date(0)})
        self.assertTrue(df["answer_summary"].str.endswith("d0").all())
        self.assertTrue(df["entity_id"].is_unique)

    def test_clusters_latest_batch_window(self):
        df = queries.get_b_track_clusters(self.client)
        self.assertEqual(set(df["cluster_name"]), {"발레코어", "로퍼"})
        self.assertTrue(self.client.ops("trend_clusters", "gte"))

    def test_b_track_empty_tables(self):
        client = FakeClient({})
        self.assertTrue(queries.get_b_track_entity_scores(client).empty)
        self.assertTrue(queries.get_b_track_answer_cards(client).empty)
        self.assertTrue(queries.get_b_track_clusters(client).empty)
        self.assertTrue(queries.get_top_trend_videos(client).empty)
        self.assertTrue(queries.get_trend_sources(client).empty)

    def test_top_trend_videos_limited_and_sorted(self):
        df = queries.get_top_trend_videos(self.client, days=30, limit=3)
        self.assertEqual(len(df), 3)
        self.assertEqual(df["view_over_baseline"].tolist(), [5.0, 4.0, 3.0])
        self.assertEqual(self.client.ops("trend_videos", "limit"), [(3,)])

    def test_active_sources_only(self):
        df = queries.get_trend_sources(self.client)
        self.assertTrue(df["active"].all())
        self.assertEqual(len(df), 4)


# ─────────────────────────────────────────────────────────
# 4) 셀럽 미채택 · 변환 방어
# ─────────────────────────────────────────────────────────
class TransformTest(DashboardTestCase):
    def test_celeb_unadopted_excludes_entities_in_celeb_titles(self):
        ents = pd.DataFrame(sample_tables()["trend_entity_scores"][:6])
        titles = ["이민정 데님자켓 하울", "한가인 로퍼 추천"]
        out = transforms.celeb_unadopted(ents, titles, min_channels=3)
        names = out["entity_name"].tolist()
        self.assertNotIn("데님 자켓", names)  # 공백 무시 매칭
        self.assertNotIn("로퍼", names)
        self.assertEqual(names, ["발레코어", "바라클라바"])  # 채널 수 내림차순 · breakout/rising/mainstream

    def test_celeb_unadopted_min_channels_and_empty(self):
        ents = pd.DataFrame(sample_tables()["trend_entity_scores"][:6])
        self.assertTrue(transforms.celeb_unadopted(ents, [], min_channels=99).empty)
        self.assertTrue(transforms.celeb_unadopted(None, ["x"]).empty)
        self.assertTrue(transforms.celeb_unadopted(pd.DataFrame(), None).empty)
        partial = pd.DataFrame([{"entity_name": "발레코어", "trend_stage": "rising", "distinct_channels_30d": None}])
        self.assertTrue(transforms.celeb_unadopted(partial, [], min_channels=1).empty)

    def test_normalize_list_variants(self):
        self.assertEqual(transforms.normalize_list(None), [])
        self.assertEqual(transforms.normalize_list(float("nan")), [])
        self.assertEqual(transforms.normalize_list("없음"), [])
        self.assertEqual(transforms.normalize_list('["a", "b"]'), ["a", "b"])
        self.assertEqual(transforms.normalize_list("단일"), ["단일"])
        self.assertEqual(transforms.normalize_list([{"topic": "t", "rationale": "r"}, None, " "]), ["t — r"])
        self.assertEqual(transforms.normalize_list({"k": "v", "e": None}), ["k: v"])

    def test_rule_trace_parsing(self):
        self.assertEqual(transforms.parse_rule_trace(None), [])
        self.assertEqual(transforms.parse_rule_trace({"risk": ["기본값 10"], "x": []}), [("risk", ["기본값 10"])])
        self.assertEqual(transforms.parse_rule_trace('{"novelty": ["+20"]}'), [("novelty", ["+20"])])

    def test_expand_emotions_handles_missing(self):
        df = pd.DataFrame({"emotion_temperature": [{"envy": 30}, None, "not json"]})
        out = transforms.expand_emotions(df)
        self.assertEqual(out["envy"].tolist()[0], 30)
        self.assertTrue(pd.isna(out["envy"].tolist()[1]))
        self.assertIn("cringe", transforms.expand_emotions(pd.DataFrame()).columns)
        self.assertIn("fatigue", transforms.expand_emotions(pd.DataFrame({"x": [1]})).columns)

    def test_hit_pattern_helpers(self):
        client = FakeClient(sample_tables())
        data = transforms.add_view_ratio(queries.get_celeb_hit_dataset(client))
        board = transforms.channel_leaderboard(data)
        self.assertEqual(len(board), 3)
        self.assertTrue(((board["hit_rate"] >= 0) & (board["hit_rate"] <= 1)).all())
        self.assertFalse(transforms.lift_table(data, "content_structure", min_samples=2).empty)
        self.assertFalse(transforms.signal_lift(data, constants.THUMBNAIL_SIGNALS).empty)
        self.assertFalse(transforms.emotion_lift(data).empty)

    def test_hit_pattern_helpers_empty(self):
        empty = pd.DataFrame()
        self.assertTrue(transforms.channel_leaderboard(empty).empty)
        self.assertTrue(transforms.channel_leaderboard(None).empty)
        self.assertTrue(transforms.lift_table(empty, "category").empty)
        self.assertTrue(transforms.signal_lift(empty, constants.THUMBNAIL_SIGNALS).empty)
        self.assertTrue(transforms.emotion_lift(None).empty)
        self.assertIsNone(transforms.baseline_hit_rate(empty))
        self.assertTrue(transforms.add_view_ratio(None).empty)
        self.assertTrue(transforms.add_thumbnail_signals(pd.DataFrame()).empty)

    def test_hourly_checkpoints(self):
        hourly = pd.DataFrame(sample_tables()["own_channel_hourly_snapshots"])
        cp = transforms.hourly_checkpoints(hourly)
        row = cp[cp["video_id"] == "o0"].iloc[0]
        self.assertEqual(row["h3"], 3100)
        self.assertEqual(row["h6"], 6000)
        self.assertEqual(row["h12"], 12500)
        self.assertEqual(row["h24"], 24000)
        self.assertTrue(transforms.hourly_checkpoints(None).empty)
        self.assertTrue(transforms.hourly_checkpoints(pd.DataFrame({"video_id": ["x"]})).empty)

    def test_signal_badges_null_safe(self):
        self.assertEqual(transforms.score_signal_badges({}), [])
        self.assertEqual(transforms.score_signal_badges(None), [])
        labels = [b[0] for b in transforms.score_signal_badges(
            {"couple_present": True, "face_count": 2, "main_emotion": "놀람", "envy": 40, "aspiration": 20,
             "fatigue": 30, "ad_boost_suspected": True})]
        self.assertIn("커플 썸네일", labels)
        self.assertIn("광고 부스팅 의심", labels)

    def test_formatters(self):
        self.assertEqual(transforms.fmt_views(123456), "12.3만")
        self.assertEqual(transforms.fmt_views(None), "—")
        self.assertEqual(transforms.fmt_views(float("nan")), "—")
        self.assertEqual(transforms.fmt_rate(0.256), "26%")
        self.assertEqual(transforms.fmt_date(None), "—")
        self.assertEqual(transforms.freshness(None)[0], "데이터 없음")
        self.assertEqual(transforms.split_hints(pd.Series(["a,b", "a", None])).iloc[0].tolist(), ["a", 2])
        self.assertTrue(transforms.top_b_signals(None).empty)


# ─────────────────────────────────────────────────────────
# 5) 페이지 import / AppTest 스모크
# ─────────────────────────────────────────────────────────
PAGES = {
    "views/home.py": "오늘의 전략 브리핑",
    "views/track_a_scores.py": "기획 후보 큐",
    "views/track_a_celeb.py": "셀럽 경쟁 채널",
    "views/track_a_own.py": OWN_CHANNEL,
    "views/track_a_weekly.py": "주간 시장 리포트",
    "views/track_b_radar.py": "패션·유행 트렌드 레이더",
    "views/track_b_clusters.py": "셀럽 미채택 기회",
}


class AppSmokeTest(DashboardTestCase):
    def _run_all_pages(self, client):
        try:
            from streamlit.testing.v1 import AppTest
        except ImportError:  # pragma: no cover
            self.skipTest("streamlit.testing 사용 불가")
        env = {"SUPABASE_URL": "https://example.invalid", "SUPABASE_KEY": "test-key"}
        with mock.patch.dict(os.environ, env), \
                mock.patch("lib.supabase_client.get_client", return_value=client):
            at = AppTest.from_file(str(DASHBOARD / "app.py"), default_timeout=60)
            at.run()
            self.assertFalse(at.exception, f"app.py: {[e.value for e in at.exception]}")
            for page in PAGES:
                st.cache_data.clear()
                before = len(client.calls)
                at.switch_page(page).run()
                self.assertFalse(at.exception, f"{page}: {[e.value for e in at.exception]}")
                self.assertGreater(len(at.get("html")), 2, f"{page} 가 렌더되지 않음")
                self.assertGreater(len(client.calls), before, f"{page} 가 실행되지 않음")
                bodies = " ".join(el.proto.body for el in at.get("html"))
                self.assertIn(PAGES[page], bodies, f"{page} 헤더 미렌더")
        return at

    def test_all_pages_render_with_sample_data(self):
        self._run_all_pages(FakeClient(sample_tables()))

    def test_all_pages_render_with_empty_database(self):
        self._run_all_pages(FakeClient({}))

    def test_all_pages_render_when_tables_fail(self):
        self._run_all_pages(FakeClient(sample_tables(), fail_tables={"trend_answer_cards", "thumbnail_analysis",
                                                                      "own_channel_analysis", "trend_clusters"}))

    def test_missing_credentials_shows_setup(self):
        try:
            from streamlit.testing.v1 import AppTest
        except ImportError:  # pragma: no cover
            self.skipTest("streamlit.testing 사용 불가")
        with mock.patch("lib.supabase_client.credentials_present", return_value=False):
            at = AppTest.from_file(str(DASHBOARD / "app.py"), default_timeout=60)
            at.run()
            self.assertFalse(at.exception)

    def test_dashboard_never_writes(self):
        import re

        write_call = re.compile(r"(?<!path)\.(insert|upsert|update|delete|rpc)\(")
        for path in DASHBOARD.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            self.assertIsNone(write_call.search(text), f"{path.name} 에 쓰기 호출")

    def test_no_legacy_pages_dir_and_no_hardcoded_secret(self):
        self.assertFalse((DASHBOARD / "pages").exists(), "구식 pages/ 자동 등록과 충돌")
        self.assertFalse((DASHBOARD / ".env").exists(), "dashboard/.env 는 생성 금지")
        self.assertTrue((DASHBOARD / ".env.example").exists())
        for path in DASHBOARD.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("supabase.co", text)
            self.assertNotIn("eyJhbGci", text)


if __name__ == "__main__":
    unittest.main()
