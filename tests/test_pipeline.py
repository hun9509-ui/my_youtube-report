import json
import sys
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

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


class OwnChannelTests(unittest.TestCase):
    def setUp(self):
        self.video = {**video("own-video", days=1), "comment_count": 1}
        self.gemini = Mock(return_value={"topic": "valid"})
        self.db = Mock()
        self.db.is_own_video_analyzed.return_value = False
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

    def test_b_track_run_stops_before_clustering_when_scan_raises(self):
        worker = Mock()
        worker.run_broad_scan.side_effect = RuntimeError("scan failed")
        with patch.dict(sys.modules, {"b_track_collector": worker, "b_track_gemini": worker}):
            self.assertIs(main.run_b_track("b-track-run", []), False)
        worker.run_collect.assert_called_once_with()
        worker.run_broad_scan.assert_called_once_with()
        worker.run_cluster.assert_not_called()


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
