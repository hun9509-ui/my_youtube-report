import json
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock, patch

import main
import config
import gemini_client
import telegram_notifier
from analysis_schema import VideoAnalysis


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
            result = gemini_client.generate_json("prompt", "requested-model", response_model=VideoAnalysis)
        self.assertEqual(session.post.call_count, 2)
        self.assertEqual(result["_model_version"], "actual-model")
        self.assertIs(result["ppl_likely"], False)

    def test_gemini_preserves_http_error_and_skips_retry_for_missing_model(self):
        session = Mock()
        session.post.return_value = response(404, {"error": {"message": "model not found"}})
        with patch.object(gemini_client, "_session", return_value=session):
            result = gemini_client.generate_json("prompt", "missing-model")
        self.assertEqual(result["error"], "HTTP 404: model not found")
        session.post.assert_called_once()

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
