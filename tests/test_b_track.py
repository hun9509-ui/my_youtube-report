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
    def test_postgrest_does_not_repeat_range_parameters_on_later_pages(self):
        base_url = "https://example.invalid"
        rows = [{"id": i} for i in range(1001)]
        offsets = []

        def respond(request):
            params = request.url.params
            self.assertEqual(params.get_list("limit"), ["1000"])
            self.assertEqual(len(params.get_list("offset")), 1)
            offset = int(params["offset"])
            offsets.append(offset)
            return httpx.Response(200, json=rows[offset:offset + 1000])

        with SyncPostgrestClient(base_url) as db, \
                httpx.Client(base_url=base_url, transport=httpx.MockTransport(respond)) as http, \
                patch.object(db.session, "request", side_effect=http.request):
            self.assertEqual(btc._read_pages(lambda: db.from_("example").select("id").order("id")), rows)

        self.assertEqual(offsets, [0, 1000])


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

        self.assertEqual(collector.run_collect(), 0)
        self.assertIn("수집 실패, 스킵", self.output.getvalue())
        self.assertIn("&key=[redacted]&maxResults=50", self.output.getvalue())
        self.assertNotIn(fake_key, self.output.getvalue())
        self.videos.update.assert_not_called()

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
            {"entity_id": 1700, "channel_id": f"c{i % 4}", "view_count_at_scan": 3}
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
        self.assertEqual(cluster["total_views_7d"], 3003)
        clusters.update.return_value.eq.assert_called_once_with("id", 4)

    def _scan_tables(self):
        videos, jobs = Mock(), Mock()
        self.db.table.side_effect = {"trend_videos": videos, "gemini_jobs": jobs}.__getitem__
        videos.select.return_value = _select_rows([{
            "video_id": "v1", "channel_id": "c1", "channel_title": "Channel",
            "title": "Video", "description_short": "",
        }])
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
        videos, jobs = self._scan_tables()
        fake_key = "SECRET_KEY"
        with patch.object(gemini.ds, "call_deepseek_raw", return_value="", create=True), \
                patch.object(gemini.ga, "genai_generate_text_with_meta", create=True,
                             side_effect=RuntimeError(f"https://example.invalid?key={fake_key}")):
            self.assertEqual(gemini.run_broad_scan(), 0)

        failed = jobs.update.call_args.args[0]
        self.assertEqual(failed["status"], "failed")
        self.assertIn("?key=[redacted]", failed["error_summary"])
        self.assertNotIn(fake_key, failed["error_summary"])
        self.assertNotIn(fake_key, self.output.getvalue())
        videos.update.assert_not_called()


class BTrackDiscoveryTest(unittest.TestCase):
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
