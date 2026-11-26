import builtins
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

from googleapiclient.errors import HttpError
from httplib2 import Response

import b_track_gemini
import main as pipeline
import repair_analysis as repair
import strategy_scorer


def analysis(row_id, video_id, topic="", **fields):
    return {
        "id": row_id, "video_id": video_id, "topic": topic,
        "gemini_raw": {"topic": topic} if topic else {"error": "invalid"},
        **fields,
    }


def video(video_id, day=1, comments=0):
    return {
        "video_id": video_id, "title": video_id, "comment_count": comments,
        "published_at": f"2026-01-{day:02}T00:00:00+00:00",
    }


class FakeClient:
    """In-memory PostgREST mock supporting the real pagination and writer helpers."""
    def __init__(self, **tables):
        self.rows = deepcopy(tables)
        self.writes = []
        self.reads = []
        self.processing_key = True
        self.before_write = lambda *_: None

    def table(self, name):
        return FakeQuery(self, name)


class FakeQuery:
    def __init__(self, client, table):
        self.client = client
        self.table = table
        self.operation = "select"
        self.columns = "*"
        self.filters = {}
        self.order_by = None
        self.bounds = slice(None)
        self.payload = None
        self.conflict = None

    def select(self, columns):
        self.columns = columns
        return self

    def order(self, column):
        self.order_by = column
        return self

    def eq(self, column, value):
        return self.in_(column, [value])

    def in_(self, column, values):
        self.filters[column] = values
        return self

    def range(self, start, end):
        self.bounds = slice(start, end + 1)
        return self

    def limit(self, limit):
        return self.range(0, limit - 1)

    def delete(self):
        self.operation = "delete"
        return self

    def update(self, fields):
        self.operation, self.payload = "update", fields
        return self

    def insert(self, rows):
        self.operation, self.payload = "insert", rows
        return self

    def upsert(self, rows, on_conflict=None):
        self.operation, self.payload = "upsert", rows
        self.conflict = on_conflict
        return self

    def execute(self):
        rows = self.client.rows.setdefault(self.table, [])
        matched = [row for row in rows if all(row.get(key) in values for key, values in self.filters.items())]
        if self.operation == "select":
            self.client.reads.append((self.table, self.order_by, self.bounds.start))
            if self.columns == "processing_key" and not self.client.processing_key:
                raise RuntimeError("missing processing_key")
            if self.order_by:
                matched.sort(key=lambda row: row[self.order_by])
            return SimpleNamespace(data=deepcopy(matched[self.bounds]))

        change = (self.table, self.operation, self.filters, self.payload)
        self.client.before_write(*change)
        self.client.writes.append(deepcopy(change))
        if self.operation == "delete":
            rows[:] = [row for row in rows if row not in matched]
        elif self.operation == "update":
            for row in matched:
                row.update(deepcopy(self.payload))
        else:
            matched = self._insert_or_upsert(rows)
        return SimpleNamespace(data=deepcopy(matched))

    def _insert_or_upsert(self, rows):
        payload = self.payload if isinstance(self.payload, list) else [self.payload]
        saved = []
        for fields in deepcopy(payload):
            keys = (self.conflict or "video_id").split(",")
            prior = next((row for row in rows if all(row.get(key) == fields[key] for key in keys)), None)
            if self.operation == "upsert" and prior is not None:
                prior.update(fields)
                saved.append(prior)
            else:
                if self.table in {"daily_analysis", "video_scores"}:
                    fields["id"] = max((row["id"] for row in rows), default=0) + 1
                rows.append(fields)
                saved.append(fields)
        return saved


class RepairTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(TemporaryDirectory()))
        self.backups = self.root / "backups"
        self.output = self.enterContext(redirect_stdout(StringIO()))
        self.errors = self.enterContext(redirect_stderr(StringIO()))
        self.analyze = self.enterContext(patch.object(repair.gemini, "analyze_video"))
        self.analyze_own = self.enterContext(patch.object(repair.gemini, "analyze_own_video"))
        self.comments = self.enterContext(patch.object(repair.yt, "get_video_comments"))
        self.deepseek = self.enterContext(patch.object(repair.ds, "analyze_comments"))
        self.own_deepseek = self.enterContext(patch.object(repair.ds, "analyze_own_video_comments"))
        self.youtube = self.enterContext(patch.object(repair, "build"))
        self.enterContext(patch.object(repair.config, "YOUTUBE_API_KEY", ""))

    def run_cli(self, client, steps, *flags):
        report = self.root / f"report-{len(list(self.root.glob('report-*')))}.json"
        with patch.object(repair.db, "get_client", return_value=client):
            status = repair.main([
                "--steps", steps, "--delay", "0", "--backup-dir", str(self.backups),
                "--json-report", str(report), *flags,
            ])
        return status, json.loads(report.read_text())

    def test_default_dry_run_reads_later_pages_and_reports_candidates(self):
        initial = [analysis(i, f"v{i:04}", "valid") for i in range(1001)]
        client = FakeClient(
            initial_analysis=initial,
            videos=[video(row["video_id"]) for row in initial] + [video("unseen", 2)],
            daily_analysis=[analysis(1, "failed"), analysis(2, "v0000", "valid", deepseek_raw={"error": "retry"})],
        )
        status, report = self.run_cli(client, "all")
        self.assertEqual(status, 0)
        self.assertFalse(report["apply"])
        self.assertEqual(report["steps"]["daily-cleanup"]["delete_candidates"], 1)
        self.assertEqual(report["steps"]["daily-reanalyze"]["candidates"], 1)
        self.assertEqual(report["steps"]["daily-reanalyze"]["comment_errors"], 1)
        self.assertEqual(report["steps"]["scores-recompute"]["upsert_candidates"], 1001)
        self.assertIn(("initial_analysis", "id", 1000), client.reads)
        self.assertEqual(client.writes, [])
        self.assertFalse(self.backups.exists())
        for model in (self.analyze, self.analyze_own, self.comments, self.deepseek):
            model.assert_not_called()

    def test_later_stage_backup_failure_prevents_all_database_mutations(self):
        client = FakeClient(
            daily_analysis=[analysis(1, "failed")],
            weekly_reports=[{"id": 1, "report_date": "2026-01-01", "week_summary": ""}],
        )
        original_open = builtins.open

        def fail_weekly(path, *args, **kwargs):
            if Path(path).name == "weekly_cleanup_backup.json":
                raise OSError("disk full")
            return original_open(path, *args, **kwargs)

        with patch.object(repair, "open", side_effect=fail_weekly, create=True):
            status, report = self.run_cli(client, "daily-cleanup,weekly-cleanup", "--apply")

        self.assertEqual(status, 1)
        self.assertIn("disk full", report["error"])
        self.assertEqual(client.writes, [])
        self.assertTrue((self.backups / "daily_cleanup_backup.json").is_file())

    def test_daily_cleanup_preserves_latest_gemini_and_newest_valid_comments(self):
        donor = analysis(2, "dupe", "older", deepseek_raw={"summary": "preserved"}, comments_summary="indexed")
        survivor = analysis(3, "dupe", "latest", deepseek_raw={"error": "invalid"}, processing_key=None)
        failed = analysis(4, "dupe", processing_key="dupe")
        orphan = analysis(5, "failed", deepseek_raw={"praise_points": ["a"]})
        rows = [analysis(1, "dupe", deepseek_raw={"summary": "old comments"}), donor, survivor, failed, orphan]
        client = FakeClient(daily_analysis=rows)

        def check_backup(*_):
            originals = json.loads((self.backups / "daily_cleanup_backup.json").read_text())
            self.assertEqual(sorted(originals, key=lambda row: row["id"]), rows)

        client.before_write = check_backup
        status, report = self.run_cli(client, "daily-cleanup", "--apply")

        self.assertEqual(status, 0)
        self.assertEqual(report["steps"]["daily-cleanup"], {
            "survivors": 1, "delete_candidates": 4, "comment_merges": 1,
            "processing_key_backfills": 1, "comments_preserved": 1,
        })
        self.assertEqual([change[1] for change in client.writes], ["update", "delete", "update"])
        self.assertEqual(len(client.rows["daily_analysis"]), 1)
        row = client.rows["daily_analysis"][0]
        self.assertEqual(row["id"], 3)
        self.assertEqual(row["deepseek_raw"], donor["deepseek_raw"])
        self.assertEqual(row["comments_summary"], "indexed")
        self.assertEqual(row["processing_key"], "dupe")

    def test_daily_reanalysis_validates_gemini_and_reuses_preserved_comments(self):
        raw = {"summary": "existing comments", "_version": "1.0", "extra": "keep"}
        client = FakeClient(
            daily_analysis=[analysis(1, "preserved", deepseek_raw=raw, comments_summary="keep custom field")],
            videos=[video("preserved", 3, comments=12), video("empty", 2), video("error")],
        )
        self.analyze.side_effect = [{"topic": " usable "}, {"topic": " "}, {"error": "invalid model"}]
        status, report = self.run_cli(client, "daily-cleanup,daily-reanalyze", "--apply", "--with-comments")

        self.assertEqual(status, 1)
        stats = report["steps"]["daily-reanalyze"]
        self.assertEqual((stats["candidates"], stats["saved"], stats["failed"]), (3, 1, 2))
        self.assertEqual(self.analyze.call_args_list, [
            call(client.rows["videos"][0], model_mode="daily"),
            call(client.rows["videos"][1], model_mode="daily"),
            call(client.rows["videos"][2], model_mode="daily"),
        ])
        row, = client.rows["daily_analysis"]
        self.assertEqual(row["topic"], "usable")
        self.assertEqual(row["processing_key"], "preserved")
        self.assertEqual(row["deepseek_raw"], raw)
        self.assertEqual(row["comments_summary"], "keep custom field")
        self.comments.assert_not_called()
        self.deepseek.assert_not_called()

    def test_daily_comment_recovery_only_updates_comments_even_without_processing_key_on_row(self):
        original = analysis(1, "v1", "keep Gemini", deepseek_raw={"error": "retry"}, processing_key=None)
        client = FakeClient(daily_analysis=[original], videos=[video("v1", comments=10)])
        self.comments.return_value = [{"text": "a", "like_count": 0}]
        self.deepseek.return_value = {"summary": "comment repair"}

        status, report = self.run_cli(client, "daily-reanalyze", "--apply", "--with-comments")

        self.assertEqual(status, 0)
        self.assertEqual(report["steps"]["daily-reanalyze"]["comments_recovered"], 1)
        self.comments.assert_called_once_with("v1", max_comments=repair.config.TOP_COMMENTS_COUNT, raise_on_error=True)
        self.analyze.assert_not_called()
        row, = client.rows["daily_analysis"]
        self.assertEqual(row["gemini_raw"], original["gemini_raw"])
        self.assertIsNone(row["processing_key"])
        self.assertEqual(row["deepseek_raw"]["summary"], "comment repair")
        self.assertEqual(json.loads((self.backups / "daily_reanalyze_backup.json").read_text()), [original])

    def test_missing_migration_blocks_all_mutations_and_legacy_insert_is_opt_in(self):
        client = FakeClient(
            daily_analysis=[analysis(1, "already", "valid"), analysis(2, "corrupt")],
            videos=[video("already"), video("missing", 2)],
        )
        client.processing_key = False
        status, report = self.run_cli(client, "daily-cleanup,daily-reanalyze", "--apply")
        self.assertEqual(status, 1)
        self.assertIn("db_migrate.py", report["error"])
        self.assertEqual(client.writes, [])
        self.analyze.assert_not_called()

        self.analyze.return_value = {"topic": "filled"}
        status, report = self.run_cli(client, "daily-reanalyze", "--apply", "--allow-without-migration")
        self.assertEqual(status, 0)
        self.assertEqual(report["steps"]["daily-reanalyze"]["saved"], 1)
        (_, operation, _, payload), = client.writes
        self.assertEqual(operation, "insert")
        self.assertEqual(payload["video_id"], "missing")
        self.assertNotIn("processing_key", payload)
        self.analyze.assert_called_once_with(video("missing", 2), model_mode="daily")

    def test_legacy_insert_checks_for_valid_analysis_added_while_model_ran(self):
        client = FakeClient(videos=[video("race")])
        client.processing_key = False

        def model(*args, **kwargs):
            client.rows["daily_analysis"].append(analysis(1, "race", "existing"))
            return {"topic": "new result"}

        self.analyze.side_effect = model
        status, report = self.run_cli(client, "daily-reanalyze", "--apply", "--allow-without-migration")
        self.assertEqual(status, 0)
        self.assertEqual(report["steps"]["daily-reanalyze"]["already_analyzed"], 1)
        self.assertEqual(client.writes, [])

    def test_daily_limit_breaks_timestamp_ties_by_video_id_and_scores_new_analysis(self):
        older = video("aaa")
        preferred = video("first", 2)
        client = FakeClient(videos=[video("second", 2), older, preferred])
        self.analyze.return_value = {"topic": "new analysis"}

        status, report = self.run_cli(
            client, "scores-recompute,daily-reanalyze", "--apply", "--limit", "1",
        )

        self.assertEqual(status, 0)
        self.analyze.assert_called_once_with(preferred, model_mode="daily")
        self.assertEqual(report["steps"]["daily-reanalyze"]["saved"], 1)
        self.assertEqual(report["steps"]["scores-recompute"]["saved"], 1)
        score, = client.rows["video_scores"]
        self.assertEqual(score["video_id"], "first")
        self.assertEqual(score["source_table"], "daily_analysis")

    def test_scores_recompute_with_initial_precedence_and_optional_orphan_delete(self):
        version = repair.config.SCORE_VERSION
        corrupt = {"id": 1, "video_id": "initial", "score_version": version, "priority_score": -1}
        orphan = {"id": 2, "video_id": "orphan", "score_version": version, "priority_score": 9}
        old = {"id": 3, "video_id": "old_version", "score_version": "other"}
        client = FakeClient(
            video_scores=[corrupt, orphan, old],
            initial_analysis=[analysis(1, "initial", "priority")],
            daily_analysis=[analysis(1, "initial", "ignored"), analysis(2, "daily", "earlier"),
                            analysis(3, "daily", "latest", gemini_raw={"topic": "latest", "applicability": "상"})],
            videos=[video("initial"), video("daily"), video("orphan")],
        )
        status, report = self.run_cli(client, "scores-recompute", "--apply")
        self.assertEqual(status, 0)
        self.assertEqual(report["steps"]["scores-recompute"], {
            "saved": 2, "orphans_deleted": 0, "failed": 0, "upsert_candidates": 2, "orphan_scores": 1,
        })
        self.assertEqual(json.loads((self.backups / "scores_recompute_backup.json").read_text()), [corrupt])
        by_video = {row["video_id"]: row for row in client.rows["video_scores"]}
        self.assertEqual(by_video["initial"]["source_table"], "initial_analysis")
        self.assertEqual(by_video["daily"]["source_table"], "daily_analysis")
        self.assertGreater(by_video["daily"]["hangoeun_fit_score"], by_video["initial"]["hangoeun_fit_score"])
        self.assertIn("orphan", by_video)

        client.before_write = lambda *_: self.assertEqual(
            json.loads((self.backups / "orphan_scores_backup.json").read_text()), [orphan]
        )
        status, report = self.run_cli(client, "scores-recompute", "--apply", "--delete-orphan-scores")
        self.assertEqual(status, 0)
        self.assertEqual(report["steps"]["scores-recompute"]["orphans_deleted"], 1)
        self.assertEqual(client.writes[-1][:3], ("video_scores", "delete", {"id": [2]}))

    def test_own_reanalysis_keeps_original_comments_and_queries_video_id(self):
        raw = {"summary": "keep", "_version": "older", "extension": {"foo": 1}}
        failed = {"video_id": "own", "topic": "", "gemini_raw": {"error": "retry"}, "deepseek_raw": raw}
        client = FakeClient(own_channel_analysis=[failed], own_channel_videos=[video("own")])
        self.analyze_own.return_value = {"topic": "new Gemini"}
        client.before_write = lambda *_: self.assertEqual(
            json.loads((self.backups / "own_reanalyze_backup.json").read_text()), [failed]
        )
        with patch.object(repair.db, "save_own_analysis", wraps=repair.db.save_own_analysis) as save:
            status, report = self.run_cli(client, "own-reanalyze", "--apply", "--with-comments")

        self.assertEqual(status, 0)
        self.assertEqual(report["steps"]["own-reanalyze"]["saved"], 1)
        self.assertIn(("own_channel_analysis", "video_id", 0), client.reads)
        self.assertEqual(save.call_args.args[2], raw)
        self.assertEqual(client.rows["own_channel_analysis"][0]["deepseek_raw"], raw)
        self.assertEqual(client.rows["own_channel_analysis"][0]["topic"], "new Gemini")
        self.comments.assert_not_called()
        self.deepseek.assert_not_called()
        self.own_deepseek.assert_not_called()

    def test_weekly_cleanup_backs_up_blank_and_old_rows_before_chunked_deletion(self):
        rows = [{"id": i, "report_date": "2026-01-01", "week_summary": "previous"} for i in range(1, 102)]
        blank = {"id": 102, "report_date": "2026-01-02", "week_summary": "  "}
        client = FakeClient(weekly_reports=rows + [blank])
        client.before_write = lambda *_: self.assertEqual(
            json.loads((self.backups / "weekly_cleanup_backup.json").read_text()), rows[:-1] + [blank]
        )
        status, report = self.run_cli(client, "weekly-cleanup", "--apply")

        self.assertEqual(status, 0)
        self.assertEqual(report["steps"]["weekly-cleanup"]["delete_candidates"], 101)
        self.assertEqual([len(write[2]["id"]) for write in client.writes], [100, 1])
        self.assertEqual(client.rows["weekly_reports"], [rows[-1]])

    def test_dead_sources_require_positive_not_found_confirmation(self):
        sources = [
            {"id": i, "source_ref": f"c{i}", "active": True, "total_collected": count}
            for i, count in enumerate((0, 0, 0, 3, 7, 0), start=1)
        ]
        sources[-1]["active"] = False
        client = FakeClient(trend_sources=sources)

        def http_error(status, reason):
            content = json.dumps({"error": {"errors": [{"reason": reason}]}}).encode()
            return HttpError(Response({"status": str(status)}), content, uri="https://api/?key=test-key")

        youtube = self.youtube.return_value
        channels = youtube.channels.return_value
        channels.list.side_effect = lambda part, id: Mock(
            execute=Mock(return_value={"items": [] if id == "c5" else [
                {"contentDetails": {"relatedPlaylists": {"uploads": f"uploads-{id}"}}}
            ]})
        )
        youtube.playlistItems.return_value.list.return_value.execute.side_effect = [
            http_error(404, "playlistNotFound"), {"items": []}, http_error(429, "rateLimitExceeded"),
        ]
        client.before_write = lambda *_: self.assertEqual(
            json.loads((self.backups / "dead_sources_backup.json").read_text()), [sources[0], sources[4]]
        )
        with patch.object(repair.config, "YOUTUBE_API_KEY", "test-key"):
            status, report = self.run_cli(client, "dead-sources", "--apply", "--source-ids", "5,6")

        self.assertEqual(status, 0)
        stats = report["steps"]["dead-sources"]
        self.assertEqual(stats["candidate_ids"], [1, 2, 3, 5])
        self.assertEqual(stats["confirmed_dead_ids"], [1, 5])
        self.assertEqual(stats["probe_errors"], 1)
        self.assertEqual(stats["disabled"], 2)
        self.assertEqual([w[2]["id"] for w in client.writes], [[1], [5]])
        self.assertNotIn("test-key", self.errors.getvalue())

    def test_dead_sources_dry_run_without_key_lists_candidates_only(self):
        client = FakeClient(trend_sources=[
            {"id": 1, "source_ref": "c1", "active": True, "total_collected": None},
            {"id": 2, "source_ref": "c2", "active": True, "total_collected": 50},
        ])
        status, report = self.run_cli(client, "dead-sources", "--dry-run")
        self.assertEqual(status, 0)
        self.assertEqual(report["steps"]["dead-sources"]["candidate_ids"], [1])
        self.assertEqual(report["steps"]["dead-sources"]["confirmed_dead_ids"], [])
        self.youtube.assert_not_called()
        self.assertEqual(client.writes, [])

    def test_weekly_cleanup_keeps_older_valid_row_when_newer_same_date_is_blank(self):
        valid = {"id": 1, "report_date": "2026-05-10", "week_summary": "valid report"}
        blank_newer = {"id": 2, "report_date": "2026-05-10", "week_summary": "  "}
        client = FakeClient(weekly_reports=[valid, blank_newer])
        status, report = self.run_cli(client, "weekly-cleanup", "--apply")

        self.assertEqual(status, 0)
        self.assertEqual(report["steps"]["weekly-cleanup"]["delete_candidates"], 1)
        self.assertEqual(client.rows["weekly_reports"], [valid])
        self.assertEqual(
            json.loads((self.backups / "weekly_cleanup_backup.json").read_text()),
            [blank_newer],
        )

    def test_daily_cleanup_warns_when_deleting_preserved_comments_without_reanalyze(self):
        orphan = analysis(5, "failed-only", deepseek_raw={"summary": "keep me"})
        client = FakeClient(daily_analysis=[orphan])
        status, report = self.run_cli(client, "daily-cleanup")

        self.assertEqual(status, 0)
        self.assertEqual(report["steps"]["daily-cleanup"]["comments_preserved"], 1)
        self.assertTrue(report.get("warnings"))
        self.assertIn("daily-cleanup 단독 실행", self.errors.getvalue())
        self.assertEqual(client.writes, [])

    def test_dead_sources_treats_missing_items_key_as_probe_error(self):
        source = {"id": 1, "source_ref": "c1", "active": True, "total_collected": 0}
        client = FakeClient(trend_sources=[source])
        self.youtube.return_value.channels.return_value.list.return_value.execute.return_value = {}
        with patch.object(repair.config, "YOUTUBE_API_KEY", "test-key"):
            status, report = self.run_cli(client, "dead-sources", "--apply")

        self.assertEqual(status, 0)
        stats = report["steps"]["dead-sources"]
        self.assertEqual(stats["confirmed_dead_ids"], [])
        self.assertEqual(stats["probe_errors"], 1)
        self.assertEqual(stats["disabled"], 0)
        self.assertEqual(client.writes, [])
        self.assertTrue(client.rows["trend_sources"][0]["active"])

    def test_existing_json_report_path_aborts_before_any_apply_mutation(self):
        client = FakeClient(weekly_reports=[{"id": 1, "report_date": "2026-01-01", "week_summary": ""}])
        existing_report = self.root / "existing-report.json"
        existing_report.write_text("{\"keep\": true}\n", encoding="utf-8")

        with patch.object(repair.db, "get_client", return_value=client):
            status = repair.main([
                "--steps", "weekly-cleanup", "--apply", "--delay", "0",
                "--backup-dir", str(self.backups), "--json-report", str(existing_report),
            ])

        self.assertEqual(status, 1)
        self.assertEqual(existing_report.read_text(encoding="utf-8"), "{\"keep\": true}\n")
        self.assertEqual(client.writes, [])
        self.assertFalse(self.backups.exists())
        self.assertIn("error", json.loads(self.output.getvalue()))


class PhaseOneRegressionTest(unittest.TestCase):
    def test_score_batch_masks_exception(self):
        with (
            patch.object(strategy_scorer.config, "GEMINI_API_KEY", "private-model-key"),
            patch.object(strategy_scorer, "score_video", side_effect=RuntimeError("private-model-key")),
            redirect_stdout(StringIO()) as output,
        ):
            self.assertEqual(strategy_scorer.score_batch({"v": {}}, [{"video_id": "v"}], "daily_analysis"), [])
        self.assertIn("[redacted]", output.getvalue())
        self.assertNotIn("private-model-key", output.getvalue())

    def test_broad_scan_handles_null_non_lists_and_mixed_entities(self):
        db = Mock()
        db.table.return_value.insert.return_value.execute.return_value.data = [{"id": 1}]
        videos = [
            {"video_id": f"v{i}", "channel_id": "c", "channel_title": "channel",
             "title": "title", "description_short": "", "published_at": "2026-01-01"}
            for i in range(3)
        ]
        items = [
            {"video_id": "v0", "entities": None},
            {"video_id": "v1", "entities": "malformed"},
            {"video_id": "v2", "entities": [None, "bad", {"name": None}, {"name": 3, "entity_type": " "}]},
        ]
        with (
            patch.object(b_track_gemini, "get_db", return_value=db),
            patch.object(b_track_gemini, "_read_pages", return_value=videos),
            patch.object(b_track_gemini.ds, "call_deepseek_raw", return_value=json.dumps(items)),
            patch.object(b_track_gemini, "_upsert_entity", return_value=19) as entity,
            patch.object(b_track_gemini.time, "sleep"),
            redirect_stdout(StringIO()),
        ):
            self.assertEqual(b_track_gemini.run_broad_scan(), 3)
        entity.assert_called_once_with(db, "3", "item", "lifestyle")

    def test_read_only_modes_require_only_supabase_configuration(self):
        with (
            patch.multiple(
                pipeline.config, SUPABASE_URL="https://example.test", SUPABASE_KEY="test",
                YOUTUBE_API_KEY="", TELEGRAM_BOT_TOKEN="", TELEGRAM_CHAT_ID="",
                GEMINI_API_KEY="", DEEPSEEK_API_KEY="", GEMINI_PROVIDER="", TEXT_ANALYSIS_ENGINE="",
            ),
            redirect_stdout(StringIO()),
        ):
            for mode in ("repair-data", "pattern-report"):
                with self.subTest(mode=mode):
                    self.assertTrue(pipeline.validate_env(mode))
                    with patch.object(pipeline.config, "SUPABASE_KEY", ""):
                        self.assertFalse(pipeline.validate_env(mode))
