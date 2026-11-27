import json
import sys
import unittest
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
from io import StringIO
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

import httpx
from postgrest import SyncPostgrestClient

import main
import config
import gemini_client
import supabase_writer
import telegram_notifier
from analysis_schema import OwnVideoAnalysis, VideoAnalysis


def response(status, content):
    return SimpleNamespace(
        ok=200 <= status < 300, status_code=status, reason="mock", json=lambda: content
    )


def video(video_id, days=0):
    return {
        "video_id": video_id,
        "title": video_id,
        "published_at": (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(),
        "comment_count": 0,
    }


class ConfigTests(unittest.TestCase):
    def test_competitor_channel_ids(self):
        channel_ids = config.TARGET_CHANNELS.values()
        self.assertEqual(len(channel_ids), 16)
        self.assertEqual(len(set(channel_ids)), len(channel_ids))
        self.assertTrue(all(channel_id.startswith("UC") for channel_id in channel_ids))

        expected_channels = {
            "안녕하세요 최화정이에요": "UCA9kvKQdNxslWm5IKZgXS_A",
            "한지혜 Han Ji Hye": "UCfcEEsjr6_lUUHQ-1zID2cQ",
            "김나영의 nofilterTV": "UC0wNauoCVIfU_xtWwrczFiA",
            "순풍 선우용여": "UCjJhHk-vpe59lCR5f4YalTQ",
            "채정안TV": "UCktGIKibxR7agzlF62Mbx2A",
            "신애라이프": "UCnnJ34afyg9w25I_AVZIHxA",
            "Umaizing 엄정화TV": "UC6le2DAXq9bLVVYl6nenbEg",
            "황신혜의 cine style": "UCrQ-eA1UWgv0TJBkbO-y13Q",
        }
        for channel, expected_id in expected_channels.items():
            with self.subTest(channel=channel):
                self.assertEqual(config.TARGET_CHANNELS[channel], expected_id)

    def test_initial_batches_cover_all_competitors_once(self):
        self.assertEqual(set(config.INITIAL_BATCHES), set(range(1, 9)))
        channels = [channel for batch in config.INITIAL_BATCHES.values() for channel in batch]
        self.assertCountEqual(channels, config.TARGET_CHANNELS.keys())

    def test_score_version(self):
        self.assertEqual(config.SCORE_VERSION, "v2.0")

    def test_default_daily_gemini_fallback_order(self):
        with patch.dict("os.environ", clear=True):
            self.assertEqual(config.get_gemini_fallback_models("daily"), [
                "gemini-3.5-flash-lite",
                "gemini-2.5-flash-lite",
                "gemini-3.8-flash",
                "gemini-3.5-flash",
            ])


class TrendTests(unittest.TestCase):
    def test_failed_categories_do_not_publish_empty_results(self):
        with patch.object(config, "TRENDING_CATEGORIES", {"A": "1"}), \
                patch.object(main.yt, "get_trending_videos", side_effect=RuntimeError("quota")), \
                patch.object(main.db, "save_trend_classified") as save, \
                patch.object(main.tg, "send_error_alert") as alert:
            self.assertFalse(main.run_trend_collect())
            save.assert_not_called()
            alert.assert_called_once()

    def test_partial_collection_keeps_available_results_and_signals_failure(self):
        with patch.object(config, "TRENDING_CATEGORIES", {"A": "1", "B": "2"}), \
                patch.object(main.yt, "get_trending_videos", side_effect=[[video("김치 레시피")], RuntimeError("quota")]), \
                patch.object(main.db, "save_trend_classified", side_effect=lambda data: sum(len(data[k]) for k in ("track_a", "track_b", "irregular"))) as save, \
                patch.object(main.sheets, "save_trend_classified"), patch.object(main.tg, "send_error_alert"):
            self.assertFalse(main.run_trend_collect())
            save.assert_called_once()

    def test_unconfirmed_weekly_delivery_is_reported(self):
        weekly = {"track_a": [{"title": "<A>", "channel_title": "B&C"}], "track_b": [], "event_keywords": []}
        with patch.object(main.db, "get_weekly_trend_summary", return_value=weekly), \
                patch.object(main.tg, "send_message", return_value=False) as send:
            self.assertFalse(main.run_trend_weekly())
            self.assertIn("&lt;A&gt;", send.call_args.args[0])

    def test_future_and_naive_dates_do_not_trigger_alerts(self):
        for stamp in ("2199-01-01T00:00:00Z", "2026-01-01T00:00:00"):
            self.assertFalse(main.tc.detect_irregular({"published_at": stamp, "view_count": 999999})["is_irregular"])


class DailyPipelineTests(unittest.TestCase):
    def setUp(self):
        self.db = Mock()
        self.db.get_daily_state.return_value = ({}, set())
        self.db.save_videos.side_effect = len
        self.db.save_daily_analysis.side_effect = lambda rows, _: len(rows)
        self.db.save_video_scores.side_effect = len
        self.sheets = Mock()
        self.alert = Mock(return_value=True)
        self.model = Mock()
        self.collect = Mock(return_value=[video("recent", days=3)])
        for target, name, value in (
            (main, "db", self.db), (main, "sheets", self.sheets),
            (main, "gemini", self.model), (main, "collect_channel", self.collect),
            (main.tg, "send_message", self.alert), (main.config, "TARGET_CHANNELS", {"test": "UC"}),
        ):
            patcher = patch.object(target, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_empty_healthy_run_and_failed_collection_are_distinct(self):
        self.collect.return_value = []
        self.assertTrue(main.run_daily_collection())
        self.assertIn("분석 완료", self.alert.call_args.args[0])
        self.collect.side_effect = RuntimeError("upstream error")
        self.assertFalse(main.run_daily_collection())
        self.assertIn("분석 실패", self.alert.call_args.args[0])
        self.db.save_videos.assert_not_called()

    def test_model_failure_is_not_saved_or_scored(self):
        self.model.analyze_videos_batch.return_value = [{"video_id": "recent", "error": "HTTP 429"}]
        self.assertFalse(main.run_daily_collection())
        self.db.save_daily_analysis.assert_not_called()
        self.db.save_video_scores.assert_not_called()
        self.assertIn("Gemini 실패 1", self.alert.call_args.args[0])

    def test_all_five_model_failures_leave_analysis_and_scores_empty(self):
        self.collect.return_value = [video(f"video-{i}", days=1) for i in range(5)]
        self.model.analyze_videos_batch.return_value = [
            {"video_id": v["video_id"], "error": "HTTP 429"} for v in self.collect.return_value
        ]
        self.assertIs(main.run_daily_collection(), False)
        self.db.save_daily_analysis.assert_not_called()
        self.db.save_video_scores.assert_not_called()
        self.assertIn("Gemini 실패 5", self.alert.call_args.args[0])

    def test_valid_analysis_is_deduplicated_and_reused(self):
        self.collect.return_value += [video("recent", days=3), video("old", days=8)]
        result = {"video_id": "recent", "topic": "valid"}
        self.model.analyze_videos_batch.return_value = [result]
        self.assertTrue(main.run_daily_collection())
        self.assertEqual(len(self.db.save_daily_analysis.call_args.args[0]), 1)
        self.assertEqual(len(self.db.save_video_scores.call_args.args[0]), 1)

        self.db.get_daily_state.return_value = ({"recent": {"gemini_raw": result}}, {"recent"})
        self.assertTrue(main.run_daily_collection())
        self.model.analyze_videos_batch.assert_called_once()
        self.db.save_daily_analysis.assert_called_once()
        self.db.save_video_scores.assert_called_once()

    def test_failed_analysis_write_blocks_scoring(self):
        self.model.analyze_videos_batch.return_value = [{"video_id": "recent", "topic": "valid"}]
        self.db.save_daily_analysis.side_effect = None
        self.db.save_daily_analysis.return_value = 0
        self.assertFalse(main.run_daily_collection())
        self.db.save_video_scores.assert_not_called()
        self.sheets.save_daily_analysis.assert_not_called()

    def test_existing_analysis_retries_comments_without_calling_gemini(self):
        self.collect.return_value[0]["comment_count"] = 1
        self.db.get_daily_state.return_value = ({"recent": {
            "processing_key": "recent", "analysis_date": "2026-11-23",
            "gemini_raw": {"topic": "valid"}, "deepseek_raw": {"error": "HTTP 402"},
        }}, {"recent"})
        with patch.object(main.yt, "get_video_comments", return_value=[{"text": "example"}]), \
                patch.object(main.ds, "analyze_comments", return_value={"summary": "valid"}):
            self.assertTrue(main.run_daily_collection())
        self.model.analyze_videos_batch.assert_not_called()
        row = self.db.save_daily_analysis.call_args.args[0][0]
        self.assertEqual(row["analysis_date"], "2026-11-23")
        self.assertEqual(row["comments"]["summary"], "valid")

    def test_delivery_failure_is_a_failed_run(self):
        self.collect.return_value = []
        self.alert.return_value = False
        self.assertFalse(main.run_daily_collection())

    def test_weekly_provider_failure_never_saves_blank_report(self):
        self.db.get_recent_videos_from_db.return_value = [video("recent")]
        with patch.object(main.ds, "generate_weekly_report", return_value={"error": "HTTP 402"}), \
                patch.object(main.tg, "send_error_alert") as alert:
            self.assertFalse(main.run_weekly_report())
        self.db.save_weekly_report.assert_not_called()
        alert.assert_called_once_with("HTTP 402", "주간 리포트")

    def test_score_and_save_forwards_thumbnail_analyses(self):
        high_thumb = {"couple_present": True, "face_count": 2, "main_emotion": "놀람"}
        analyses = [{"video_id": "recent", "gemini": {"topic": "valid", "content_structure": "토크형"}, "comments": {}}]
        self.db.get_thumbnail_analyses.return_value = {}
        self.assertEqual(main._score_and_save(analyses, [video("recent")], "daily_analysis"), 1)
        no_thumb_score = self.db.save_video_scores.call_args.args[0][0]

        self.db.get_thumbnail_analyses.return_value = {"recent": high_thumb}
        self.assertEqual(main._score_and_save(analyses, [video("recent")], "daily_analysis"), 1)
        self.db.get_thumbnail_analyses.assert_called_with(["recent"])
        with_thumb_score = self.db.save_video_scores.call_args.args[0][0]
        self.assertGreater(with_thumb_score["novelty_score"], no_thumb_score["novelty_score"])


class OwnChannelTests(unittest.TestCase):
    def setUp(self):
        self.video = {**video("own-video", days=1), "comment_count": 1}
        self.gemini = Mock(return_value={"topic": "valid"})
        self.db = Mock()
        self.db.get_own_channel_state.return_value = {}
        self.sheets = Mock()
        self.alert = Mock(return_value=True)
        self.error_alert = Mock()
        self.comments = Mock(return_value=[])
        for target, name, value in (
            (main, "db", self.db), (main, "sheets", self.sheets),
            (main.yt, "get_channel_videos", Mock(return_value=([self.video], {}))),
            (main.yt, "get_video_details", Mock(return_value=[self.video])),
            (main.yt, "get_video_comments", self.comments),
            (main.gemini, "analyze_own_video", self.gemini),
            (main.tg, "send_own_channel_alert", self.alert),
            (main.tg, "send_error_alert", self.error_alert),
            (main.time, "sleep", Mock()),
        ):
            patcher = patch.object(target, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_failed_gemini_result_is_never_saved_or_announced(self):
        self.gemini.return_value = {"error": "HTTP 429"}
        self.assertIs(main.run_own_channel_daily(), False)
        self.db.save_own_analysis.assert_not_called()
        self.sheets.save_own_analysis.assert_not_called()
        self.comments.assert_not_called()
        self.alert.assert_not_called()
        self.error_alert.assert_called_once_with("HTTP 429", "자체채널 - own-video")

    def test_comment_failure_still_saves_gemini_analysis(self):
        self.comments.side_effect = RuntimeError("댓글 수집 오류")
        self.assertIs(main.run_own_channel_daily(), False)
        self.comments.assert_called_once_with(
            "own-video", max_comments=config.TOP_COMMENTS_COUNT, raise_on_error=True,
        )
        self.db.save_own_analysis.assert_called_once_with(
            "own-video", {"topic": "valid"}, {"error": "댓글 수집 오류"},
        )
        self.sheets.save_own_analysis.assert_called_once()
        self.alert.assert_called_once()
        self.error_alert.assert_called_once()

    def test_recovers_comments_without_repeating_gemini_analysis(self):
        gemini_raw = {"topic": "previous analysis"}
        self.db.get_own_channel_state.return_value = {
            "own-video": {"gemini_raw": gemini_raw, "deepseek_raw": {"error": "temporary failure"}},
        }
        self.comments.return_value = [{"text": "new comment"}]
        recovered = {"summary": "recovered analysis"}
        with patch.object(main.ds, "analyze_own_video_comments", return_value=recovered) as analyze:
            self.assertIs(main.run_own_channel_daily(), True)

        self.db.get_own_channel_state.assert_called_once_with(["own-video"])
        self.gemini.assert_not_called()
        self.comments.assert_called_once_with(
            "own-video", max_comments=config.TOP_COMMENTS_COUNT, raise_on_error=True,
        )
        analyze.assert_called_once_with("own-video", self.comments.return_value, "longform")
        self.db.save_own_analysis.assert_called_once_with("own-video", gemini_raw, recovered)
        self.sheets.save_own_analysis.assert_called_once_with(self.video, gemini_raw, recovered)
        self.alert.assert_called_once_with(self.video, gemini_raw, recovered)

    def test_failed_comment_retry_leaves_prior_result_intact(self):
        self.db.get_own_channel_state.return_value = {
            "own-video": {"gemini_raw": {"topic": "previous analysis"}, "deepseek_raw": {"error": "retry"}},
        }
        self.comments.side_effect = RuntimeError("temporary failure")
        self.assertIs(main.run_own_channel_daily(), False)
        self.gemini.assert_not_called()
        self.db.save_own_analysis.assert_not_called()
        self.sheets.save_own_analysis.assert_not_called()
        self.alert.assert_not_called()
        self.error_alert.assert_called_once()

    def test_completed_own_analysis_skips_gemini_and_comments(self):
        self.db.get_own_channel_state.return_value = {
            "own-video": {"gemini_raw": {"topic": "valid"}, "deepseek_raw": {"summary": "finished"}},
        }
        with patch.object(main.tg, "send_message", return_value=True):
            self.assertIs(main.run_own_channel_daily(), True)
        self.gemini.assert_not_called()
        self.comments.assert_not_called()
        self.db.save_own_analysis.assert_not_called()

    def test_db_failure_blocks_sheets_and_completion_alert(self):
        self.db.save_own_analysis.return_value = False
        self.assertIs(main.run_own_channel_daily(), False)
        self.sheets.save_own_analysis.assert_not_called()
        self.alert.assert_not_called()
        self.error_alert.assert_called_once()

    def test_previously_saved_own_results_require_valid_raw_and_column_topics(self):
        client = Mock()
        select = client.table.return_value.select
        execute = select.return_value.eq.return_value.execute
        execute.return_value.data = [
            {"topic": "", "gemini_raw": {"topic": "valid"}},
            {"topic": "valid", "gemini_raw": {"error": "HTTP 429"}},
        ]
        with patch.object(supabase_writer, "get_client", return_value=client):
            self.assertIs(supabase_writer.is_own_video_analyzed("own-video"), False)
            execute.return_value.data.append({"topic": "valid", "gemini_raw": {"topic": "valid"}})
            self.assertIs(supabase_writer.is_own_video_analyzed("own-video"), True)
        select.assert_called_with("video_id,topic,gemini_raw")

    def test_own_state_returns_valid_rows_with_comment_status(self):
        valid = {"video_id": "own-video", "topic": "valid", "gemini_raw": {"topic": "valid"},
                 "deepseek_raw": {"error": "temporary failure"}}
        client = Mock()
        select = client.table.return_value.select
        query = select.return_value.in_.return_value.limit.return_value
        query.execute.return_value.data = [
            valid,
            {**valid, "video_id": "blank-topic", "topic": ""},
            {**valid, "video_id": "failed-gemini", "gemini_raw": {"error": "quota"}},
        ]
        with patch.object(supabase_writer, "get_client", return_value=client):
            state = supabase_writer.get_own_channel_state(["own-video", "blank-topic", "failed-gemini"])
        self.assertEqual(state, {"own-video": valid})
        select.assert_called_once_with("video_id,topic,gemini_raw,deepseek_raw")

    def test_own_db_normalizes_text_and_requires_an_upserted_row(self):
        client = Mock()
        upsert = client.table.return_value.upsert
        upsert.return_value.execute.return_value.data = []
        analysis = {"topic": "valid", "strengths": ["강점 A", "강점 B"]}
        with patch.object(supabase_writer, "get_client", return_value=client):
            self.assertIs(supabase_writer.save_own_analysis("own-video", {"error": "quota"}, {}), False)
            upsert.assert_not_called()
            self.assertIs(supabase_writer.save_own_analysis("own-video", analysis, {}), False)
            row = upsert.call_args.args[0]
            self.assertEqual(row["strengths"], "강점 A, 강점 B")
            self.assertEqual(row["gemini_raw"]["strengths"], ["강점 A", "강점 B"])
            self.assertEqual(row["analysis_date"], datetime.now(config.KST).date().isoformat())
            upsert.return_value.execute.return_value.data = [{"video_id": "own-video"}]
            self.assertIs(supabase_writer.save_own_analysis("own-video", analysis, {}), True)

    def test_own_schema_normalizes_text_lists(self):
        result = OwnVideoAnalysis.model_validate({
            "topic": "valid", "strengths": ["첫 번째", "두 번째"], "competitor_angle": ["비교"],
        })
        self.assertEqual(result.strengths, "첫 번째, 두 번째")
        self.assertEqual(result.competitor_angle, "비교")


class CollectionAndConfigTests(unittest.TestCase):
    def test_trending_duration_filter_and_opt_out_use_same_response(self):
        youtube = Mock()
        listing = youtube.videos.return_value.list
        listing.return_value.execute.return_value = {"items": [
            {"id": "short", "contentDetails": {"duration": "PT4M59S"}},
            {"id": "long", "contentDetails": {"duration": "PT5M"}},
        ]}
        with patch.object(main.yt, "get_youtube_client", return_value=youtube):
            filtered = main.yt.get_trending_videos()
            self.assertEqual([(v["video_id"], v["duration_seconds"]) for v in filtered], [("long", 300)])
            self.assertEqual(filtered[0]["duration"], "PT5M")
            listing.assert_called_once()
            unfiltered = main.yt.get_trending_videos(min_duration=0)
        self.assertEqual([(v["video_id"], v["duration_seconds"]) for v in unfiltered],
                         [("short", 299), ("long", 300)])
        self.assertEqual(listing.call_count, 2)

    def test_b_track_score_needs_only_supabase_credentials(self):
        with patch.multiple(config, SUPABASE_URL="test-url", SUPABASE_KEY="test-db-key",
                            GEMINI_PROVIDER="ai_studio", TEXT_ANALYSIS_ENGINE="deepseek",
                            YOUTUBE_API_KEY=None, TELEGRAM_BOT_TOKEN=None, TELEGRAM_CHAT_ID=None,
                            DEEPSEEK_API_KEY=None, GEMINI_API_KEY=None):
            self.assertIs(main.validate_env("b-track-score"), True)

    def test_b_track_run_allows_vertex_adc_without_api_keys(self):
        with patch.multiple(config, SUPABASE_URL="test-url", SUPABASE_KEY="test-db-key",
                            YOUTUBE_API_KEY="test-youtube-key", TEXT_ANALYSIS_ENGINE="deepseek",
                            GEMINI_PROVIDER="vertexai", DEEPSEEK_API_KEY=None, GEMINI_API_KEY=None):
            self.assertIs(main.validate_env("b-track-run"), True)
            with patch.object(config, "GEMINI_PROVIDER", "ai_studio"):
                self.assertIs(main.validate_env("b-track-run"), False)

    def test_b_track_run_stops_before_clustering_when_scan_raises(self):
        worker = Mock()
        worker.run_broad_scan.side_effect = RuntimeError("scan failed")
        with patch.dict(sys.modules, {"b_track_collector": worker, "b_track_gemini": worker}):
            self.assertIs(main.run_b_track("b-track-run", []), False)
        worker.run_collect.assert_called_once_with()
        worker.run_broad_scan.assert_called_once_with()
        worker.run_cluster.assert_not_called()


class PaginationTests(unittest.TestCase):
    @contextmanager
    def postgrest(self, tables, requests):
        base_url = "https://example.invalid"

        def respond(request):
            table = request.url.path.rsplit("/", 1)[-1]
            params = request.url.params
            self.assertEqual(len(params.get_list("offset")), 1)
            self.assertEqual(len(params.get_list("limit")), 1)
            self.assertIn("order", params)
            offset, limit = int(params["offset"]), int(params["limit"])
            requests.append((table, offset, params["order"]))
            return httpx.Response(200, json=tables[table][offset:offset + limit])

        with SyncPostgrestClient(base_url) as db, \
                httpx.Client(base_url=base_url, transport=httpx.MockTransport(respond)) as http, \
                patch.object(db.session, "request", side_effect=http.request), \
                patch.object(supabase_writer, "get_client", return_value=db):
            yield db

    def test_page_requests_use_one_offset_and_limit_each(self):
        rows = [{"id": i} for i in range(3)]
        requests = []
        with self.postgrest({"sample": rows}, requests) as db:
            result = list(supabase_writer._read_pages(
                lambda: db.table("sample").select("id").order("id"), page_size=2,
            ))
        self.assertEqual(result, rows)
        self.assertEqual([offset for _, offset, _ in requests], [0, 2])

    def test_snapshot_targets_include_all_competitor_and_own_pages(self):
        tables = {
            "videos": [{"video_id": f"competitor-{i:04}"} for i in range(1001)],
            "own_channel_videos": [{"video_id": f"own-{i:04}"} for i in range(1001)],
        }
        requests = []
        with self.postgrest(tables, requests):
            rows = supabase_writer.get_all_videos_for_snapshot()
        self.assertEqual(len(rows), 2002)
        self.assertEqual(rows[1000], {"video_id": "competitor-1000", "source_type": "competitor"})
        self.assertEqual(rows[-1], {
            "video_id": "own-1000", "source_type": "own", "channel_title": config.OWN_CHANNEL,
        })
        self.assertEqual([(table, offset) for table, offset, _ in requests], [
            (table, offset) for table in tables for offset in (0, 500, 1000)
        ])

    def test_score_backfill_paginates_all_tables_and_uses_latest_valid_daily_row(self):
        videos = [{"video_id": f"v{i:04}", "title": str(i)} for i in range(1002)]
        valid = {"topic": "valid"}
        failed_daily = {"video_id": "v1000", "id": 2000, "gemini_raw": {"error": "quota"}}
        latest_valid = {"video_id": "v1000", "id": 0, "gemini_raw": valid}
        tables = {
            "video_scores": [{"video_id": f"scored-{i:04}"} for i in range(1000)] + [{"video_id": "v1001"}],
            "videos": videos,
            "initial_analysis": [{"video_id": v["video_id"], "id": i} for i, v in enumerate(videos)],
            "daily_analysis": [failed_daily] + [
                {"video_id": f"v{i:04}", "id": i + 1, "gemini_raw": valid} for i in range(999, -1, -1)
            ] + [latest_valid, {**latest_valid, "id": -1},
                 {"video_id": "only-failed", "id": -2, "gemini_raw": {"error": "quota"}}],
        }
        requests = []
        with self.postgrest(tables, requests):
            initial, daily = supabase_writer.get_score_backfill_rows()
        self.assertEqual(len(initial), 1001)
        self.assertEqual(initial[-1][0], videos[1000])
        self.assertEqual(len(daily), 1001)
        self.assertEqual(daily[-1], (videos[1000], latest_valid))
        for table in tables:
            self.assertIn((table, 1000), [(name, offset) for name, offset, _ in requests])
        self.assertTrue(all(order == "analysis_date.desc,id.desc"
                            for table, _, order in requests if table == "daily_analysis"))

    def test_comment_backfill_limit_counts_only_rows_needing_reanalysis(self):
        rows = [{"video_id": str(i), "deepseek_raw": {"emotion_temperature": {"comfort": 1}}}
                for i in range(500)]
        rows += [{"video_id": str(i), "deepseek_raw": {}} for i in range(500, 1001)]
        requests = []
        with self.postgrest({"initial_analysis": rows}, requests):
            pending = supabase_writer.get_videos_needing_comment_reanalysis(limit=1)
        self.assertEqual(pending, [{"video_id": "500", "title": "", "comment_count": 0}])
        self.assertEqual([offset for _, offset, _ in requests], [0, 500])

    def test_get_thumbnail_analyses_paginates_all_and_filtered_batches(self):
        rows = [{"video_id": f"v{i:04}", "couple_present": i % 2 == 0} for i in range(502)]
        requests = []
        with self.postgrest({"thumbnail_analysis": rows}, requests):
            all_thumbs = supabase_writer.get_thumbnail_analyses()
        self.assertEqual(len(all_thumbs), 502)
        self.assertEqual([offset for _, offset, _ in requests], [0, 500])

        filtered_requests = []
        target_ids = [f"v{i:04}" for i in range(105)]
        with self.postgrest({"thumbnail_analysis": rows[:50]}, filtered_requests):
            subset = supabase_writer.get_thumbnail_analyses(["", "v0001", "v0001", *target_ids])
        self.assertEqual(len(subset), 50)
        self.assertEqual(len(filtered_requests), 2)
        self.assertTrue(all(order == "video_id" for _, _, order in filtered_requests))


class ProcessingKeyFallbackTests(unittest.TestCase):
    @contextmanager
    def postgrest(self, respond, error_for_key=None):
        base_url = "https://example.invalid"
        requests = []

        def dispatch(request):
            table = request.url.path.rsplit("/", 1)[-1]
            params = dict(request.url.params)
            payload = json.loads(request.content) if request.content else None
            method = request.method
            requests.append((method, table, params, payload))
            uses_key = params.get("on_conflict") == "processing_key" or (
                "processing_key" in params.get("select", "").split(",")
            )
            if uses_key and error_for_key is not None:
                status, error = error_for_key
                return httpx.Response(status, json=error)
            return httpx.Response(200, json=respond(method, table, params, payload))

        with SyncPostgrestClient(base_url) as db, \
                httpx.Client(base_url=base_url, transport=httpx.MockTransport(dispatch)) as http, \
                patch.object(db.session, "request", side_effect=http.request), \
                patch.object(supabase_writer, "get_client", return_value=db):
            yield requests

    def test_daily_state_retries_without_processing_key(self):
        error = (400, {"code": "42703", "message": "column daily_analysis.processing_key does not exist"})
        valid = {"video_id": "known", "gemini_raw": {"topic": "valid"}, "deepseek_raw": {}}

        def respond(method, table, params, payload):
            if table == "video_scores":
                self.assertEqual(params["score_version"], f"eq.{config.SCORE_VERSION}")
                return [{"video_id": "known"}]
            self.assertEqual(params["select"], "video_id,analysis_date,gemini_raw,deepseek_raw")
            self.assertEqual(params["order"], "id.desc")
            if params["offset"] == "0":
                return [{"video_id": "known", "gemini_raw": {}}] * 500
            self.assertEqual(params["offset"], "500")
            return [valid]

        with self.postgrest(respond, error) as requests:
            self.assertEqual(supabase_writer.get_daily_state(["known"]), ({"known": valid}, {"known"}))
        self.assertEqual(len(requests), 4)
        self.assertIn("processing_key", requests[0][2]["select"].split(","))

    def test_daily_write_updates_latest_valid_id_then_inserts_only_new_videos(self):
        error = (400, {"code": "PGRST204", "message": "Could not find the 'processing_key' column"})
        analyses = [{"video_id": vid, "gemini": {"topic": "new"}} for vid in ("known", "new")]

        def respond(method, table, params, payload):
            self.assertEqual(table, "daily_analysis")
            if method == "GET":
                self.assertEqual(params["order"], "id.desc")
                self.assertEqual(params["video_id"], "in.(known,new)")
                return [
                    {"id": 9, "video_id": "known", "topic": "", "gemini_raw": {"topic": "x"}},
                    {"id": 8, "video_id": "known", "topic": "x", "gemini_raw": {"error": "failed"}},
                    {"id": 7, "video_id": "known", "topic": "x", "gemini_raw": {"topic": "x"}},
                    {"id": 6, "video_id": "known", "topic": "x", "gemini_raw": {"topic": "x"}},
                ]
            if method == "PATCH":
                self.assertEqual(params["id"], "eq.7")
                self.assertEqual(payload["video_id"], "known")
                self.assertNotIn("processing_key", payload)
                return [payload]
            self.assertEqual([row["video_id"] for row in payload], ["new"])
            self.assertNotIn("processing_key", payload[0])
            return payload

        with self.postgrest(respond, error) as requests:
            self.assertEqual(supabase_writer.save_daily_analysis(analyses), 2)
        self.assertEqual([request[0] for request in requests], ["POST", "GET", "PATCH", "POST"])

    def test_dated_writes_update_latest_id_or_insert(self):
        error = (400, {"code": "42703", "message": "column processing_key does not exist"})
        now = datetime(2026, 11, 26, tzinfo=config.KST)
        for table, date_column, invoke, expected in (
            ("weekly_reports", "report_date",
             lambda: supabase_writer.save_weekly_report({"week_summary": "summary"}, now.date()), True),
            ("event_keywords", "analysis_date",
             lambda: supabase_writer.save_trend_classified({"event_keywords": ["keyword"]}), 0),
        ):
            for exists in (True, False):
                with self.subTest(table=table, exists=exists):
                    def respond(method, name, params, payload):
                        self.assertEqual(table, name)
                        if method == "GET":
                            self.assertEqual(params, {
                                "select": "id", date_column: "eq.2026-11-26", "order": "id.desc", "limit": "1",
                            })
                            return [{"id": 7}] if exists else []
                        if exists:
                            self.assertEqual(params["id"], "eq.7")
                        self.assertEqual(payload[date_column], "2026-11-26")
                        self.assertNotIn("processing_key", payload)
                        return [payload]

                    with self.postgrest(respond, error) as requests, patch.object(supabase_writer, "datetime") as clock:
                        clock.now.return_value = now
                        self.assertEqual(invoke(), expected)
                    self.assertEqual([request[0] for request in requests],
                                     ["POST", "GET", "PATCH" if exists else "POST"])

    def test_trend_write_skips_existing_track_and_video_pairs_but_saves_keywords(self):
        error = (400, {"code": "PGRST204", "message": "Could not find the 'processing_key' column"})
        data = {
            "track_a": [{"video_id": "same"}],
            "track_b": [{"video_id": "same"}],
            "event_keywords": ["keyword"],
        }
        now = datetime(2026, 11, 26, tzinfo=config.KST)

        def respond(method, table, params, payload):
            if method == "GET":
                self.assertEqual(params["analysis_date"], "eq.2026-11-26")
                if table == "event_keywords":
                    return [{"id": 3}]
                self.assertEqual(params["order"], "id")
                if params["offset"] == "0":
                    return [{"track": "A", "video_id": str(i)} for i in range(500)]
                self.assertEqual(params["offset"], "500")
                return [{"track": "A", "video_id": "same"}]
            if table == "trend_classified":
                self.assertEqual([(row["track"], row["video_id"]) for row in payload], [("B", "same")])
                self.assertNotIn("processing_key", payload[0])
                return payload
            self.assertEqual(params["id"], "eq.3")
            self.assertEqual(payload, {"analysis_date": "2026-11-26", "keywords": ["keyword"]})
            return [payload]

        with self.postgrest(respond, error) as requests, patch.object(supabase_writer, "datetime") as clock:
            clock.now.return_value = now
            self.assertEqual(supabase_writer.save_trend_classified(data), 2)
        self.assertEqual([(method, table) for method, table, _, _ in requests], [
            ("POST", "trend_classified"), ("GET", "trend_classified"), ("GET", "trend_classified"),
            ("POST", "trend_classified"), ("POST", "event_keywords"), ("GET", "event_keywords"),
            ("PATCH", "event_keywords"),
        ])

    def test_other_db_errors_do_not_trigger_fallback(self):
        for error in (
            (500, {"message": "HTTP 500"}),
            (403, {"code": "42501", "message": "permission denied for column processing_key"}),
            (400, {"code": "PGRST204", "message": "Could not find the 'other_column' column"}),
        ):
            with self.subTest(error=error):
                respond = Mock(side_effect=AssertionError("unexpected fallback"))
                with self.postgrest(respond, error) as requests:
                    with self.assertRaisesRegex(Exception, error[1]["message"]):
                        supabase_writer.get_daily_state(["known"])
                    self.assertEqual(supabase_writer.save_daily_analysis([
                        {"video_id": "known", "gemini": {"topic": "new"}},
                    ]), 0)
                    self.assertFalse(supabase_writer.save_weekly_report({"week_summary": "summary"}))
                    with self.assertRaisesRegex(Exception, error[1]["message"]):
                        supabase_writer.save_trend_classified({"track_a": [{"video_id": "known"}]})
                    with self.assertRaisesRegex(Exception, error[1]["message"]):
                        supabase_writer.save_trend_classified({"event_keywords": ["keyword"]})
                respond.assert_not_called()
                self.assertEqual(len(requests), 5)

    def test_migrated_db_uses_processing_key_without_legacy_writes(self):
        def respond(method, table, params, payload):
            if method == "GET":
                if table == "daily_analysis":
                    self.assertIn("processing_key", params["select"].split(","))
                    return [{"video_id": "known", "processing_key": "known", "gemini_raw": {"topic": "x"}}]
                return []
            self.assertEqual(method, "POST")
            self.assertEqual(params["on_conflict"], "processing_key")
            rows = payload if isinstance(payload, list) else [payload]
            self.assertTrue(all(row["processing_key"] for row in rows))
            return rows

        with self.postgrest(respond) as requests:
            analyses, _ = supabase_writer.get_daily_state(["known"])
            self.assertEqual(analyses["known"]["processing_key"], "known")
            self.assertEqual(supabase_writer.save_daily_analysis([
                {"video_id": "known", "gemini": {"topic": "x"}},
            ]), 1)
            self.assertTrue(supabase_writer.save_weekly_report({"week_summary": "summary"}))
            self.assertEqual(supabase_writer.save_trend_classified({
                "track_a": [{"video_id": "known"}], "event_keywords": ["keyword"],
            }), 1)
        self.assertEqual(len(requests), 6)


class ErrorLoggingTests(unittest.TestCase):
    def test_initial_batch_and_history_extend_redact_errors_before_logging_or_alerting(self):
        error = "https://youtube.googleapis.com/test?part=snippet&key=SECRET_KEY&alt=json"
        db = Mock()
        db.get_oldest_video_date_by_channel.return_value = datetime.now(timezone.utc)
        with patch.object(main, "db", db), \
                patch.multiple(config, INITIAL_BATCHES={1: ["test"]}, TARGET_CHANNELS={"test": "channel"}), \
                patch.object(main, "collect_channel", side_effect=RuntimeError(error)), \
                patch.object(main.yt, "get_channel_videos", side_effect=RuntimeError(error)), \
                patch.object(main.tg, "send_error_alert") as alert, \
                patch.object(main.tg, "send_message"), \
                redirect_stdout(StringIO()) as stdout, redirect_stderr(StringIO()) as stderr:
            main.run_initial_batch(1)
            main.run_history_extend()

        self.assertNotIn("SECRET_KEY", stdout.getvalue() + stderr.getvalue())
        self.assertEqual(stdout.getvalue().count("key=[redacted]"), 2)
        self.assertEqual(alert.call_count, 2)
        for call in alert.call_args_list:
            self.assertEqual(call.args[0], error.replace("SECRET_KEY", "[redacted]"))


class ProviderTests(unittest.TestCase):
    def test_gemini_retries_transient_error_and_records_response_model(self):
        session = Mock()
        session.post.side_effect = [
            response(429, {"error": {"message": "quota"}}),
            response(200, {"modelVersion": "actual-model", "candidates": [{
                "finishReason": "STOP", "content": {"parts": [{
                    "text": json.dumps({"topic": "valid", "ppl_likely": "false"})
                }]}
            }]}),
        ]
        with patch.object(gemini_client, "_session", return_value=session), \
                patch.object(gemini_client.time, "sleep"):
            result = gemini_client.generate_json(
                "prompt", "requested-model", response_model=VideoAnalysis, provider="vertexai",
            )
        self.assertEqual(session.post.call_count, 2)
        self.assertEqual(result["_model_version"], "actual-model")
        self.assertIs(result["ppl_likely"], False)

    def test_gemini_preserves_http_error_and_skips_retry_for_missing_model(self):
        session = Mock()
        session.post.return_value = response(404, {"error": {"message": "model not found"}})
        with patch.object(gemini_client, "_session", return_value=session):
            result = gemini_client.generate_json("prompt", "missing-model", provider="vertexai")
        self.assertEqual(result["error"], "HTTP 404: model not found")
        session.post.assert_called_once()

    def test_ai_studio_uses_header_and_fallbacks_after_transient_and_missing_models(self):
        session = Mock()
        session.post.side_effect = [
            response(429, {"error": {"message": "quota"}}),
            response(429, {"error": {"message": "quota"}}),
            response(404, {"error": {"message": "model not found"}}),
            response(200, {"modelVersion": "actual-model", "candidates": [{
                "finishReason": "STOP", "content": {"parts": [
                    {"thought": True, "text": "not JSON"},
                    {"text": '{"topic": "valid"}'},
                ]},
            }]}),
        ]
        with patch.object(config, "GEMINI_PROVIDER", "ai_studio"), \
                patch.object(config, "GEMINI_API_KEY", "test-key"), \
                patch.object(config, "GEMINI_THINKING_LEVEL", "LOW"), \
                patch.object(gemini_client, "_ai_studio_session", return_value=session), \
                patch.object(gemini_client.time, "sleep") as sleep:
            result = gemini_client.generate_json(
                "prompt", "primary", attempts=2, fallback_models=["primary", "missing", "fallback"],
            )

        self.assertEqual(result, {
            "topic": "valid", "_provider": "ai_studio", "_model": "fallback", "_model_version": "actual-model",
        })
        self.assertEqual(session.post.call_count, 4)
        sleep.assert_called_once_with(2)
        for call, model in zip(session.post.call_args_list, ["primary", "primary", "missing", "fallback"]):
            self.assertEqual(
                call.args[0], f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
            )
            self.assertEqual(call.kwargs["headers"], {"x-goog-api-key": "test-key"})
            self.assertEqual(call.kwargs["json"]["generationConfig"], {
                "responseMimeType": "application/json", "thinkingConfig": {"thinkingLevel": "LOW"},
            })

    def test_gemini_text_returns_raw_array_and_reported_model(self):
        session = Mock()
        session.post.return_value = response(200, {"modelVersion": "actual-model", "candidates": [{
            "content": {"parts": [{"thought": True, "text": "thinking"}, {"text": '["value"]'}]},
        }]})
        with patch.object(config, "GEMINI_API_KEY", "test-key"), \
                patch.object(config, "GEMINI_THINKING_LEVEL", ""), \
                patch.object(gemini_client, "_ai_studio_session", return_value=session):
            self.assertEqual(
                gemini_client.generate_text("prompt", "model", provider="ai_studio"),
                ('["value"]', "actual-model"),
            )
        self.assertNotIn("responseMimeType", session.post.call_args.kwargs["json"]["generationConfig"])

    def test_ai_studio_missing_key_and_http_auth_error_stop_immediately(self):
        with patch.object(config, "GEMINI_API_KEY", ""), \
                patch.object(gemini_client, "_ai_studio_session") as session:
            self.assertEqual(
                gemini_client.generate_json("prompt", "model", fallback_models=["backup"], provider="ai_studio"),
                {"error": "GEMINI_API_KEY 미설정", "_provider": "ai_studio", "_model": "model"},
            )
            with self.assertRaisesRegex(RuntimeError, "GEMINI_API_KEY 미설정"):
                gemini_client.generate_text("prompt", "model", fallback_models=["backup"], provider="ai_studio")
            session.assert_not_called()

        session = Mock()
        session.post.return_value = response(403, {"error": {"message": "forbidden"}})
        with patch.object(config, "GEMINI_API_KEY", "test-key"), \
                patch.object(gemini_client, "_ai_studio_session", return_value=session):
            with self.assertRaisesRegex(RuntimeError, "HTTP 403"):
                gemini_client.generate_text("prompt", "model", fallback_models=["backup"], provider="ai_studio")
        session.post.assert_called_once()

    def test_safe_error_masks_unknown_query_key_and_database_url(self):
        url = "https://youtube.googleapis.com/test?part=snippet&key=SECRET123&alt=json"
        with patch.dict("os.environ", {"DATABASE_URL": "postgresql://test-db"}):
            self.assertEqual(
                gemini_client.safe_error(f"{url} postgresql://test-db"),
                "https://youtube.googleapis.com/test?part=snippet&key=[redacted]&alt=json [redacted]",
            )

    def test_deepseek_raw_stops_on_payment_error(self):
        import httpx
        from openai import APIStatusError

        request = httpx.Request("POST", "https://api.deepseek.com/chat/completions")
        client = Mock()
        create = client.chat.completions.create
        create.side_effect = APIStatusError(
            "payment required", response=httpx.Response(402, request=request), body=None,
        )
        manager = MagicMock()
        manager.__enter__.return_value = client
        with patch.object(config, "DEEPSEEK_API_KEY", "test-key"), \
                patch("openai.OpenAI", return_value=manager) as openai, \
                patch.object(main.ds.time, "sleep") as sleep:
            self.assertEqual(main.ds.call_deepseek_raw("prompt", retry=3), "")
        openai.assert_called_once_with(
            api_key="test-key", base_url="https://api.deepseek.com", max_retries=0, timeout=90,
        )
        create.assert_called_once()
        sleep.assert_not_called()

    def test_first_snapshot_does_not_report_growth(self):
        fresh = {**video("recent"), "view_count": 100, "like_count": 5}
        first = main._build_snapshot(fresh, fresh, None, "2026-11-26")
        self.assertEqual(first["view_growth"], 0)
        self.assertEqual(first["like_growth"], 0)

    def test_telegram_requires_message_id_and_retries_explicit_rate_limit(self):
        with patch.object(config, "TELEGRAM_BOT_TOKEN", "test-token"), \
                patch.object(config, "TELEGRAM_CHAT_ID", "test-chat"), \
                patch.object(telegram_notifier.time, "sleep"), \
                patch.object(telegram_notifier.requests, "post") as post:
            post.return_value = response(200, {"ok": True, "result": {}})
            self.assertFalse(telegram_notifier.send_message("test", parse_mode=None))
            post.side_effect = [
                response(429, {"ok": False, "parameters": {"retry_after": 1}}),
                response(200, {"ok": True, "result": {"message_id": 123}}),
            ]
            self.assertTrue(telegram_notifier.send_message("test", parse_mode=None))
            self.assertNotIn("parse_mode", post.call_args.kwargs["json"])

    def test_daily_quota_429_skips_exhausted_model_immediately_across_calls(self):
        self.addCleanup(gemini_client.reset_exhausted_models)
        session = Mock()
        session.post.side_effect = [
            response(429, {"error": {"message": "Generate content requests per day limit exceeded"}}),
            response(200, {"modelVersion": "lite-v1", "candidates": [{
                "finishReason": "STOP", "content": {"parts": [{"text": '{"topic": "ok1"}'}]},
            }]}),
            response(200, {"modelVersion": "lite-v1", "candidates": [{
                "finishReason": "STOP", "content": {"parts": [{"text": '{"topic": "ok2"}'}]},
            }]}),
        ]
        with patch.object(config, "GEMINI_PROVIDER", "ai_studio"), \
                patch.object(config, "GEMINI_API_KEY", "test-key"), \
                patch.object(gemini_client, "_ai_studio_session", return_value=session), \
                patch.object(gemini_client.time, "sleep") as sleep:
            r1 = gemini_client.generate_json("p1", "primary", attempts=3, fallback_models=["fallback"])
            r2 = gemini_client.generate_json("p2", "primary", attempts=3, fallback_models=["fallback"])

        self.assertEqual(r1["_model"], "fallback")
        self.assertEqual(r2["_model"], "fallback")
        sleep.assert_not_called()
        self.assertEqual(
            [call.args[0] for call in session.post.call_args_list],
            [
                "https://generativelanguage.googleapis.com/v1beta/models/primary:generateContent",
                "https://generativelanguage.googleapis.com/v1beta/models/fallback:generateContent",
                "https://generativelanguage.googleapis.com/v1beta/models/fallback:generateContent",
            ],
        )

    def test_rpm_429_still_retries_without_marking_daily_exhausted(self):
        self.addCleanup(gemini_client.reset_exhausted_models)
        session = Mock()
        session.post.side_effect = [
            response(429, {"error": {"message": "Requests per minute limit exceeded"}}),
            response(200, {"modelVersion": "primary-v1", "candidates": [{
                "finishReason": "STOP", "content": {"parts": [{"text": '{"topic": "ok"}'}]},
            }]}),
        ]
        with patch.object(config, "GEMINI_PROVIDER", "ai_studio"), \
                patch.object(config, "GEMINI_API_KEY", "test-key"), \
                patch.object(gemini_client, "_ai_studio_session", return_value=session), \
                patch.object(gemini_client.time, "sleep") as sleep:
            result = gemini_client.generate_json("p", "primary", attempts=2, fallback_models=["fallback"])

        self.assertEqual(result["_model"], "primary")
        sleep.assert_called_once_with(2)
        self.assertEqual(session.post.call_count, 2)
        self.assertEqual(gemini_client._EXHAUSTED_DAILY_MODELS, set())
