import json
import sys
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from io import StringIO
from types import SimpleNamespace
from unittest.mock import Mock, patch

import httpx
from googleapiclient.errors import HttpError
from httplib2 import Response
from postgrest import SyncPostgrestClient

import b_track_collector as collector
import b_track_config as btc
import b_track_discovery as discovery
import b_track_gemini as gemini
import b_track_scorer as scorer
from b_track_scorer import score_entity


def _mention(video_id, channel_id, days_ago, views, baseline, fmt="review"):
    published = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return {
        "video_id": video_id,
        "channel_id": channel_id,
        "view_count_at_scan": views,
        "published_at": published.isoformat(),
        "view_over_baseline": round(views / baseline, 2),
        "content_format": fmt,
        "title": f"리바이스 데님 추천 {video_id}",
        "channel_title": f"채널 {channel_id}",
        "video_url": f"https://youtu.be/{video_id}",
    }


class BTrackScorerTest(unittest.TestCase):
    def test_score_entity_marks_multi_channel_overperformance_as_breakout(self):
        entity = {
            "id": 7,
            "name": "리바이스",
            "entity_type": "brand",
            "category": "fashion",
        }
        mentions = [
            _mention("v1", "c1", 2, 240_000, 100_000, "review"),
            _mention("v2", "c2", 4, 310_000, 100_000, "styling"),
            _mention("v3", "c3", 9, 180_000, 100_000, "review"),
            _mention("v4", "c4", 20, 95_000, 100_000, "haul"),
        ]

        score, card = score_entity(entity, mentions)

        self.assertEqual(score["entity_id"], 7)
        self.assertEqual(score["mention_count_7d"], 2)
        self.assertEqual(score["mention_count_30d"], 4)
        self.assertEqual(score["distinct_channels_30d"], 4)
        self.assertGreater(score["avg_view_over_baseline"], 1.8)
        self.assertEqual(score["trend_stage"], "breakout")
        self.assertEqual(score["recommended_action"], "바로 제작")
        self.assertEqual(card["confidence"], "high")
        self.assertIn("리바이스", card["answer_summary"])

    def test_score_entity_marks_sparse_low_signal_as_observe(self):
        entity = {
            "id": 8,
            "name": "테스트브랜드",
            "entity_type": "brand",
            "category": "fashion",
        }
        mentions = [
            _mention("v5", "c1", 11, 70_000, 100_000, "review"),
        ]

        score, card = score_entity(entity, mentions)

        self.assertEqual(score["mention_count_30d"], 1)
        self.assertEqual(score["distinct_channels_30d"], 1)
        self.assertEqual(score["trend_stage"], "seed")
        self.assertEqual(score["recommended_action"], "관찰")
        self.assertEqual(card["confidence"], "low")

    def test_fashion_scores_use_recent_commerce_and_engagement(self):
        entity = {"id": 7, "name": "샤넬", "entity_type": "item", "category": "Fashion"}
        mentions = [
            dict(
                _mention("v1", "c1", 1, 1000, 500), title=None, description_short="사이즈 추천 할인",
                view_count=2000, like_count=20, comment_count=4,
            ),
            dict(
                _mention("v2", "c2", 2, 1000, 500), title="일상", view_count_at_scan=0,
                view_count=1000, like_count=0, comment_count=8,
            ),
            dict(
                _mention("v3", "c3", 4, 1000, 500), title="일상", view_count_at_scan=None,
                like_count=500, comment_count=500,
            ),
            dict(_mention("old", "c4", 50, 1000, 100), like_count=500, comment_count=500),
        ]

        score, card = score_entity(entity, mentions)

        expected = {
            "engagement_score": 0.467,
            "commerce_signal": 0.5,
            "item_heat_score": 0.694,
            "shopping_intent_score": 0.5,
            "ppl_potential": 0.7,
            "channel_fit_score": 0.95,
            "production_difficulty": 0.6,
            "originality_room": 0.85,
        }
        for metric, value in expected.items():
            with self.subTest(metric=metric):
                self.assertGreater(score[metric], 0.0)
                self.assertLessEqual(score[metric], 1.0)
                self.assertEqual(score[metric], value)
        self.assertEqual(len(card["evidence_bullets"]), 4)
        self.assertEqual(
            card["evidence_bullets"][-1], "주력 포맷 review | 커머스 신호 0.50 | 참여율 0.47",
        )

        for no_recent_mentions in ([], mentions[-1:]):
            empty_score, _ = score_entity(entity, no_recent_mentions)
            for metric in expected:
                with self.subTest(metric=metric, mentions=len(no_recent_mentions)):
                    self.assertEqual(empty_score[metric], 0.0)

        missing = dict(_mention("missing", "c1", 1, 1000, 500), title="일상")
        zeros = dict(missing, video_id="zeros", like_count=0, comment_count=0)
        no_engagement, _ = score_entity(entity, [missing, zeros])
        self.assertEqual(no_engagement["engagement_score"], 0.0)
        self.assertEqual(no_engagement["shopping_intent_score"], 0.0)

    def test_classify_format_uses_explicit_values_then_ordered_keywords(self):
        cases = [
            ({"title": "OOTD 코디 추천"}, "lookbook"),
            ({"title": "셔츠 입는 법", "description_short": "쇼핑 후기"}, "styling"),
            ({"title": "내돈내산 추천 리뷰"}, "haul"),
            ({"title": "일상", "description_short": "가성비 비교 후기"}, "buying_guide"),
            ({"title": "솔직 착용 후기", "video_type": "shorts"}, "review"),
            ({"description_short": "아침 GRWM"}, "routine"),
            ({"title": "일상", "video_type": "Shorts"}, "shorts_tip"),
            ({"title": "일상", "video_type": "long"}, "vlog"),
            ({"title": "룩북", "content_format": "review", "format": "haul"}, "review"),
            ({"title": "룩북", "content_format": "unknown", "format": "vlog"}, "vlog"),
        ]
        for mention, expected in cases:
            with self.subTest(mention=mention):
                self.assertEqual(scorer.classify_format(mention), expected)

    def test_canonical_entity_key_normalizes_aliases(self):
        aliases = [
            ("NIKE", "나이키"),
            (" New-Balance ", "뉴발란스"),
            ("Arc'teryx", "아크테릭스"),
            ("Ralph_Lauren", "폴로"),
            ("OLIVE.YOUNG", "올영"),
            ("H & M", "에이치앤엠"),
            ("Local.Brand'", "localbrand"),
            (" - _ . & ' ", ""),
        ]
        for name, alias in aliases:
            with self.subTest(name=name):
                self.assertEqual(scorer.canonical_entity_key(name), scorer.canonical_entity_key(alias))
        self.assertEqual(scorer.canonical_entity_key(" Local.Brand' "), "localbrand")

    def test_run_score_merges_aliases_and_deduplicates_video_mentions(self):
        entities = [
            {"id": 1, "name": "Nike", "entity_type": "brand", "category": "fashion"},
            {"id": 2, "name": "나이키", "entity_type": "brand", "category": "fashion"},
        ]
        mentions = [
            dict(_mention("shared", "c1", 1, 1000, 500), entity_id=1, confidence=0.9),
            dict(_mention("v2", "c2", 1, 1000, 500), entity_id=1),
            dict(_mention("shared", "c1", 1, 3000, 1500), entity_id=2, confidence=0.8),
            dict(_mention("v3", "c3", 1, 1000, 500), entity_id=2),
            dict(_mention("v4", "c4", 1, 1000, 500), entity_id=2),
        ]
        for rows, channels, shared_confidence in ((mentions, 4, 0.8), (mentions[:-1], 3, 0.9)):
            with self.subTest(channels=channels):
                rows = [
                    dict(row, confidence=shared_confidence)
                    if row["entity_id"] == 2 and row["video_id"] == "shared" else row
                    for row in rows
                ]
                tables = {name: Mock() for name in (
                    "trend_entities", "entity_mentions", "trend_videos", "trend_entity_scores", "trend_answer_cards",
                )}
                db = Mock()
                db.table.side_effect = tables.__getitem__
                tables["trend_entities"].select.return_value = _select_rows(entities)
                tables["entity_mentions"].select.return_value = _select_rows(rows)
                tables["trend_videos"].select.return_value = _select_rows([])

                with patch.object(scorer, "_db", return_value=db), redirect_stdout(StringIO()):
                    self.assertEqual(scorer.run_score(), 1)

                score, = tables["trend_entity_scores"].upsert.call_args.args[0]
                card, = tables["trend_answer_cards"].upsert.call_args.args[0]
                self.assertEqual(score["entity_name"], "나이키")
                self.assertEqual(score["entity_id"], 2)
                self.assertEqual(card["entity_id"], 2)
                self.assertEqual(score["mention_count_30d"], channels)
                self.assertEqual(score["distinct_channels_30d"], channels)
                expected_shared_views = 1000 if shared_confidence < 0.9 else 3000
                self.assertEqual(score["total_views_30d"], (channels - 1) * 1000 + expected_shared_views)
                tables["trend_entities"].upsert.assert_not_called()
                tables["trend_entities"].update.assert_not_called()

    def test_load_mentions_merges_video_metrics_and_defaults(self):
        tables = {name: Mock() for name in ("entity_mentions", "trend_videos")}
        db = Mock()
        db.table.side_effect = tables.__getitem__
        tables["entity_mentions"].select.return_value = _select_rows([
            {"entity_id": 1, "video_id": "short"},
            {"entity_id": 1, "video_id": "missing"},
        ])
        tables["trend_videos"].select.return_value = _select_rows([{
            "video_id": "short", "title": "일상", "video_type": "shorts", "view_count": 1000,
            "like_count": 40, "comment_count": None, "duration_seconds": 20, "description_short": "오늘",
        }])

        short, missing = scorer._load_mentions_for_entities(db, [1])[1]

        tables["trend_videos"].select.assert_called_once_with(
            "video_id,title,channel_title,view_count,like_count,comment_count,duration_seconds,"
            "description_short,view_over_baseline,video_type"
        )
        self.assertEqual(scorer.classify_format(short), "shorts_tip")
        fields = ("like_count", "comment_count", "duration_seconds", "description_short")
        self.assertEqual(tuple(short[key] for key in fields), (40, 0, 20, "오늘"))
        self.assertEqual(tuple(missing[key] for key in fields), (0, 0, 0, ""))

    def test_mentions_are_scored_across_pages(self):
        db = Mock()
        tables = {name: Mock() for name in ("entity_mentions", "trend_videos")}
        db.table.side_effect = tables.__getitem__
        recent = datetime.now(timezone.utc) - timedelta(days=1)
        rows = [{
            "entity_id": 1,
            "video_id": f"v{i}",
            "channel_id": f"c{i}",
            "published_at": recent.isoformat(),
            "view_over_baseline": 1.4 if i else 1.0,
        } for i in range(1002)]
        mention_query = _select_rows(rows)
        tables["entity_mentions"].select.return_value = mention_query
        tables["trend_videos"].select.return_value = _select_rows([
            {"video_id": "v0", "view_over_baseline": 1.4},
        ])

        mentions = scorer._load_mentions_for_entities(db, [1])[1]
        score, _ = score_entity({"id": 1}, mentions)

        self.assertEqual(score["mention_count_90d"], 1002)
        self.assertEqual(score["distinct_channels_30d"], 1002)
        self.assertEqual(score["avg_view_over_baseline"], 1.4)
        self.assertEqual(mentions[0]["view_over_baseline"], 1.4)
        self.assertEqual([c.args for c in mention_query.range.call_args_list], [(0, 999), (1000, 1999)])
        self.assertEqual([c.args for c in mention_query.in_.call_args_list], [("entity_id", [1])] * 2)
        self.assertIn("published_at.gte.", mention_query.or_.call_args.args[0])

    def test_run_score_limits_entities_after_first_page_and_uses_kst_date(self):
        db = Mock()
        entities, scores, cards = Mock(), Mock(), Mock()
        db.table.side_effect = {
            "trend_entities": entities, "trend_entity_scores": scores, "trend_answer_cards": cards,
        }.__getitem__
        entity_query = _select_rows([{"id": i} for i in range(1, 1004)])
        entities.select.return_value = entity_query
        now = datetime(2026, 11, 26, 16, tzinfo=timezone.utc)
        mention = {"published_at": now.isoformat()}

        class FixedDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return now.astimezone(tz)

        with patch.object(scorer, "_db", return_value=db), \
                patch.object(scorer, "_load_mentions_for_entities", return_value={1001: [mention]}) as load, \
                patch.object(scorer, "datetime", FixedDatetime), \
                redirect_stdout(StringIO()):
            self.assertEqual(scorer.run_score(max_entities=1001), 1)
        self.assertEqual([c.args for c in entity_query.range.call_args_list], [(0, 999), (1000, 1000)])
        load.assert_called_once_with(db, list(range(1, 1002)))
        self.assertEqual(scores.upsert.call_args.args[0][0]["scored_at"], "2026-11-27")
        self.assertEqual(cards.upsert.call_args.args[0][0]["card_date"], "2026-11-27")

    def test_mainstream_takes_precedence_over_rising(self):
        stage, _ = scorer._trend_stage(1, 8, 5, 1.3, 0.2)
        self.assertEqual(stage, "mainstream")


def _select_rows(rows):
    """Mock a PostgREST select with a server-side 1,000-row cap."""
    query = Mock()
    for name in ("eq", "gte", "in_", "is_", "or_", "order"):
        getattr(query, name).return_value = query
    query.not_.is_.return_value = query
    query.execute.return_value = SimpleNamespace(data=rows[:1000])

    def select_range(start, end):
        result = Mock()
        result.execute.return_value = SimpleNamespace(data=rows[start:min(end + 1, start + 1000)])
        return result

    query.range.side_effect = select_range
    return query


class BTrackPaginationTest(unittest.TestCase):
    def test_scorer_postgrest_request_filters_recent_mentions_across_pages(self):
        base_url = "https://example.invalid"
        created_at = datetime.now(timezone.utc) - timedelta(days=1)
        rows = [{
            "entity_id": 7, "video_id": f"v{i}", "channel_id": "example",
            "published_at": None, "created_at": created_at.isoformat(),
        } for i in range(1001)]
        offsets = []
        oldest_allowed = datetime.now(timezone.utc) - timedelta(days=scorer.WINDOW_90D + 1)
        filters = []

        def respond(request):
            if request.url.path == "/trend_videos":
                return httpx.Response(200, json=[])
            self.assertEqual(request.url.path, "/entity_mentions")
            params = request.url.params
            self.assertEqual(params.get_list("limit"), ["1000"])
            self.assertEqual(len(params.get_list("offset")), 1)
            self.assertEqual(params.get_list("entity_id"), ["in.(7)"])
            filters.append(params.get_list("or"))
            offset = int(params["offset"])
            offsets.append(offset)
            return httpx.Response(200, json=rows[offset:offset + 1000])

        with SyncPostgrestClient(base_url) as db, \
                httpx.Client(base_url=base_url, transport=httpx.MockTransport(respond)) as http, \
                patch.object(db.session, "request", side_effect=http.request):
            mentions = scorer._load_mentions_for_entities(db, [7])[7]

        self.assertEqual(offsets, [0, 1000])
        cutoff = filters[0][0].split(",", 1)[0].removeprefix("(published_at.gte.")
        expected = f"(published_at.gte.{cutoff},and(published_at.is.null,created_at.gte.{cutoff}))"
        self.assertEqual(filters, [[expected], [expected]])
        latest_allowed = datetime.now(timezone.utc) - timedelta(days=scorer.WINDOW_90D + 1)
        self.assertLessEqual(oldest_allowed, datetime.fromisoformat(cutoff))
        self.assertLessEqual(datetime.fromisoformat(cutoff), latest_allowed)
        score, _ = score_entity({"id": 7}, mentions)
        self.assertEqual(score["mention_count_7d"], 1001)
        self.assertEqual(score["mention_count_90d"], 1001)


class BTrackCollectorTest(unittest.TestCase):
    def setUp(self):
        self.db = Mock()
        self.sources, self.videos = Mock(), Mock()
        self.sources.select.side_effect = [
            _select_rows([{"id": 1, "source_ref": "UC-example", "source_name": "Example"}]),
            _select_rows([]),
        ]
        self.db.table.side_effect = {"trend_sources": self.sources, "trend_videos": self.videos}.__getitem__
        self.output = StringIO()
        self.enterContext(redirect_stdout(self.output))
        self.enterContext(patch.object(collector, "get_db", return_value=self.db))
        self.enterContext(patch.object(collector, "QuotaGuard"))
        self.collect = self.enterContext(patch.object(collector, "collect_channel_videos"))

    def test_refreshes_recent_views_or_baseline_without_touching_scan_state(self):
        now = datetime.now(timezone.utc)
        self.collect.return_value = [{
            "video_id": video_id,
            "published_at": (now - timedelta(days=age)).isoformat().replace("+00:00", "Z"),
            "view_count": views, "like_count": 8, "comment_count": 3,
            "video_type": "longform", "broad_scan_done": False,
        } for video_id, age, views in [
            ("views-changed", 4, 100), ("baseline-changed", 12, 100),
            ("unchanged", 3, 100), ("old", 91, 100), ("new", 1, 100),
        ]]
        self.videos.select.return_value = _select_rows([
            {"video_id": "views-changed", "view_count": 25, "view_over_baseline": 1.0},
            {"video_id": "baseline-changed", "view_count": 100, "view_over_baseline": 0.99},
            {"video_id": "unchanged", "view_count": 100, "view_over_baseline": 1.0},
            {"video_id": "old", "view_count": 10, "view_over_baseline": 0.1},
        ])

        self.assertEqual(collector.run_collect(), 1)

        self.collect.assert_called_once()
        payload = {
            "view_count": 100, "like_count": 8, "comment_count": 3,
            "channel_avg_views": 100, "view_over_baseline": 1.0,
        }
        self.assertEqual([c.args[0] for c in self.videos.update.call_args_list], [payload, payload])
        self.assertEqual([c.args for c in self.videos.update.return_value.eq.call_args_list],
                         [("video_id", "views-changed"), ("video_id", "baseline-changed")])
        self.assertEqual(self.videos.insert.call_args.args[0][0]["video_id"], "new")
        self.assertEqual(self.output.getvalue().count("갱신 2개"), 2)

    def test_masks_key_in_youtube_http_error_and_skips_channel(self):
        fake_key = "SECRET_KEY"
        self.collect.side_effect = HttpError(
            Response({"status": "403", "reason": "Forbidden"}),
            b'{"error":{"message":"Forbidden"}}',
            uri=f"https://example.invalid/youtube?part=snippet&key={fake_key}&maxResults=50",
        )

        with self.assertRaisesRegex(RuntimeError, "1채널 전부 실패"):
            collector.run_collect()
        self.assertIn("수집 실패, 스킵", self.output.getvalue())
        self.assertIn("&key=[redacted]&maxResults=50", self.output.getvalue())
        self.assertNotIn(fake_key, self.output.getvalue())
        self.videos.update.assert_not_called()

    def test_quota_stop_after_failure_does_not_fail_collection(self):
        self.sources.select.side_effect = [
            _select_rows([
                {"id": 1, "source_ref": "a", "source_name": "A"},
                {"id": 2, "source_ref": "b", "source_name": "B"},
            ]),
            _select_rows([]),
        ]
        self.collect.side_effect = [RuntimeError("unavailable"), collector.QuotaExceeded("quota")]

        self.assertEqual(collector.run_collect(), 0)
        self.assertIn("예산소진 중단", self.output.getvalue())

    def test_empty_success_prevents_total_failure(self):
        self.sources.select.side_effect = [
            _select_rows([
                {"id": 1, "source_ref": "a", "source_name": "A"},
                {"id": 2, "source_ref": "b", "source_name": "B"},
            ]),
            _select_rows([]),
        ]
        self.collect.side_effect = [RuntimeError("unavailable"), []]

        self.assertEqual(collector.run_collect(), 0)
        self.assertIn("성공 1채널 / 실패 1채널", self.output.getvalue())

    def test_masks_query_and_configured_keys_without_shared_formatter(self):
        fake_key = "configured-test-value"
        with patch.dict(sys.modules, {"gemini_client": None}), \
                patch.object(collector.config, "SUPABASE_KEY", fake_key):
            message = collector._safe_error(
                Exception(f"https://example.invalid?key=sample-query-value {fake_key} " + "x" * 501)
            )

        self.assertEqual(len(message), 500)
        self.assertTrue(message.startswith("https://example.invalid?key=[redacted] [redacted] "))


class BTrackGeminiTest(unittest.TestCase):
    def setUp(self):
        self.db = Mock()
        self.output = StringIO()
        self.enterContext(redirect_stdout(self.output))
        self.enterContext(patch.object(gemini, "get_db", return_value=self.db))
        self.enterContext(patch.object(gemini.time, "sleep"))

    def test_cluster_uses_all_recent_mentions_and_looks_up_high_entity_ids(self):
        mentions, entities, clusters = Mock(), Mock(), Mock()
        self.db.table.side_effect = {
            "entity_mentions": mentions, "trend_entities": entities, "trend_clusters": clusters,
        }.__getitem__
        mentions.select.return_value = _select_rows([
            {"entity_id": 1700, "channel_id": f"c{i % 4}", "view_count_at_scan": 3 if i else None}
            for i in range(1001)
        ])
        entities.select.return_value = _select_rows([
            {"id": 1700, "name": "Example", "category": "fashion"},
        ])
        clusters.select.return_value = _select_rows([{"id": 4}])

        gemini.run_cluster()

        mention_query = mentions.select.return_value
        self.assertEqual([c.args for c in mention_query.range.call_args_list], [(0, 999), (1000, 1999)])
        self.assertEqual(mention_query.gte.call_args.args[0], "created_at")
        entities.select.return_value.in_.assert_called_once_with("id", [1700])
        cluster = clusters.update.call_args.args[0]
        self.assertEqual(cluster["mention_count_7d"], 1001)
        self.assertEqual(cluster["distinct_channels_7d"], 4)
        self.assertEqual(cluster["total_views_7d"], 3000)
        clusters.update.return_value.eq.assert_called_once_with("id", 4)

    def _scan_tables(self, *video_ids):
        videos, jobs = Mock(), Mock()
        mentions = Mock()
        self.db.table.side_effect = {
            "trend_videos": videos, "gemini_jobs": jobs, "entity_mentions": mentions,
        }.__getitem__
        videos.select.return_value = _select_rows([{
            "video_id": vid, "channel_id": "c1", "channel_title": "Channel",
            "title": "Video", "description_short": "", "view_count": None, "published_at": None,
        } for vid in video_ids or ("v1",)])
        jobs.insert.return_value.execute.return_value = SimpleNamespace(data=[{"id": 9}])
        return videos, jobs

    def test_broad_scan_records_actual_gemini_model_after_deepseek_failure(self):
        videos, jobs = self._scan_tables()
        response = '[{"video_id": "v1", "entities": []}]'
        deepseek = Mock(side_effect=RuntimeError("provider unavailable"))
        with patch.object(gemini.ds, "call_deepseek_raw", deepseek, create=True), \
                patch.object(gemini.ga, "genai_generate_text_with_meta", create=True,
                             return_value=(response, "served-gemini-model")) as generate:
            self.assertEqual(gemini.run_broad_scan(), 1)

        generate.assert_called_once_with(deepseek.call_args.args[0])
        done = jobs.update.call_args.args[0]
        self.assertEqual(done["status"], "done")
        self.assertEqual(done["model"], "served-gemini-model")
        videos.update.return_value.in_.assert_called_once_with("video_id", ["v1"])

    def test_broad_scan_supports_legacy_gemini_text_helper(self):
        _, jobs = self._scan_tables()
        response = '[{"video_id": "v1", "entities": []}]'
        with patch.object(gemini.ds, "call_deepseek_raw", return_value="{}", create=True), \
                patch.object(gemini, "ga", SimpleNamespace(genai_generate_text=Mock(return_value=response))):
            self.assertEqual(gemini.run_broad_scan(), 1)
            self.assertEqual(gemini.ga.genai_generate_text.call_args.kwargs["model_name"], btc.BROAD_SCAN_MODEL)
        self.assertEqual(jobs.update.call_args.args[0]["model"], btc.BROAD_SCAN_MODEL)

    def test_broad_scan_records_deepseek_model_when_first_provider_succeeds(self):
        _, jobs = self._scan_tables()
        response = '[{"video_id": "v1", "entities": []}]'
        with patch.object(gemini.ds, "call_deepseek_raw", return_value=response, create=True), \
                patch.object(gemini.config, "get_deepseek_model", return_value="served-deepseek-model") as model, \
                patch.object(gemini.ga, "genai_generate_text_with_meta", create=True) as generate:
            self.assertEqual(gemini.run_broad_scan(), 1)
        model.assert_called_once_with(complex_task=False)
        generate.assert_not_called()
        self.assertEqual(jobs.update.call_args.args[0]["model"], "served-deepseek-model")

    def test_broad_scan_masks_failed_job_summary_and_leaves_video_pending(self):
        videos, jobs = self._scan_tables("v1", "v2")
        fake_key = "SECRET_KEY"
        with patch.object(gemini.ds, "call_deepseek_raw", return_value="", create=True), \
                patch.object(btc, "BROAD_SCAN_BATCH_SIZE", 1), \
                patch.object(gemini.ga, "genai_generate_text_with_meta", create=True,
                             side_effect=RuntimeError(f"https://example.invalid?key={fake_key}")):
            with self.assertRaisesRegex(RuntimeError, "2배치에서 완료된 영상 없음"):
                gemini.run_broad_scan()

        failed = jobs.update.call_args.args[0]
        self.assertEqual(failed["status"], "failed")
        self.assertIn("?key=[redacted]", failed["error_summary"])
        self.assertNotIn(fake_key, failed["error_summary"])
        self.assertNotIn(fake_key, self.output.getvalue())
        videos.update.assert_not_called()

    def test_broad_scan_finishes_only_reported_videos_with_successful_writes(self):
        videos, _ = self._scan_tables("written", "failed", "missing", "empty")
        mentions = self.db.table("entity_mentions")
        mentions.upsert.return_value.execute.side_effect = [None, RuntimeError("DB write failed")]
        response = json.dumps([
            {"video_id": "written", "entities": [
                {"name": "Brand A", "confidence": None},
                {"name": "Brand B", "confidence": "bad"},
            ]},
            {"video_id": "failed", "entities": [{"name": "Brand A"}]},
            {"video_id": "empty", "entities": []},
        ])
        with patch.object(gemini.ds, "call_deepseek_raw", return_value=response, create=True), \
                patch.object(gemini, "_upsert_entity", side_effect=[1, 2, 1]):
            self.assertEqual(gemini.run_broad_scan(), 2)

        videos.update.return_value.in_.assert_called_once_with("video_id", ["written", "empty"])
        self.assertTrue(videos.update.call_args.args[0]["broad_scan_done"])
        written = mentions.upsert.call_args_list[0].args[0]
        self.assertEqual([(row["confidence"], row["view_count_at_scan"]) for row in written],
                         [(0.8, 0), (0.8, 0)])
        self.assertEqual(mentions.upsert.call_args_list[1].args[0][0]["video_id"], "failed")

    def test_broad_scan_fails_when_valid_response_has_no_batch_videos(self):
        videos, jobs = self._scan_tables()
        response = '[{"video_id": "different-video", "entities": []}]'
        with patch.object(gemini.ds, "call_deepseek_raw", return_value=response, create=True):
            with self.assertRaisesRegex(RuntimeError, "1배치에서 완료된 영상 없음"):
                gemini.run_broad_scan()
        self.assertEqual(jobs.update.call_args.args[0]["status"], "failed")
        videos.update.assert_not_called()


class BTrackDiscoveryTest(unittest.TestCase):
    def test_saves_only_reported_channels_from_successful_classification_batches(self):
        facts = {cid: {
            "channel_title": cid, "subscriber_count": 10000,
            "description_short": "", "recent_titles": [],
        } for cid in ("success", "omitted", "failed", "failed-peer")}
        response = '[{"channel_id": "success", "seed_fitness_score": 0.7}]'
        generate = Mock(side_effect=[response, RuntimeError("provider unavailable")])
        db = Mock()

        with patch.object(gemini.ga, "genai_generate_text", generate, create=True), \
                patch.object(btc, "DISCOVERY_GEMINI_BATCH", 2), \
                patch.object(discovery.time, "sleep"), redirect_stdout(StringIO()) as output:
            discovery.stage4_save_and_report(db, discovery.stage3_classify(facts))

        self.assertTrue(facts["success"]["_classified"])
        for cid in ("omitted", "failed", "failed-peer"):
            self.assertIsNot(facts[cid].get("_classified"), True)
        db.table.assert_called_once_with("trend_source_candidates")
        saved = db.table.return_value.upsert.call_args.args[0]
        self.assertEqual([row["channel_id"] for row in saved], ["success"])
        self.assertNotIn("_classified", saved[0])
        self.assertIn("Gemini 배치 2 실패", output.getvalue())

    def test_unavailable_classifier_leaves_no_candidates_to_save(self):
        facts = {"channel": {"_classified": True}}
        db = Mock()
        with patch.dict(sys.modules, {"gemini_analyzer": None}), redirect_stdout(StringIO()) as output:
            discovery.stage4_save_and_report(db, discovery.stage3_classify(facts))

        db.table.assert_not_called()
        self.assertIn("Gemini 초기화 실패", output.getvalue())
        self.assertIn("단계4: 분류 성공 후보 없음", output.getvalue())

    def test_discovers_channels_on_later_pages_and_excludes_existing_candidates(self):
        db = Mock()
        seeds, candidates, videos = Mock(), Mock(), Mock()
        db.table.side_effect = {
            "trend_sources": seeds, "trend_source_candidates": candidates, "trend_videos": videos,
        }.__getitem__
        seeds.select.return_value = _select_rows([{"source_ref": "seed"}])
        candidates.select.return_value = _select_rows([
            {"channel_id": f"c{i}"} for i in range(1001)
        ])
        videos.select.return_value = _select_rows([
            {"channel_id": f"c{i}"} for i in range(1001)
        ] + [{"channel_id": "new", "channel_title": "New Channel"}])

        with redirect_stdout(StringIO()):
            exclude = discovery._existing_exclusions(db)
            found = discovery.stage0_db_internal(db, exclude)

        self.assertIn("seed", exclude)
        self.assertIn("c1000", exclude)
        self.assertEqual(found, {"new": {"channel_title": "New Channel", "paths": ["stage0"]}})


if __name__ == "__main__":
    unittest.main()
