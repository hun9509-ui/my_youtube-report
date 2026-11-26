import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import httpx
from postgrest import SyncPostgrestClient

import pattern_learner as learner


NOW = datetime(2026, 11, 26, 12, tzinfo=timezone.utc)


def days_ago(days):
    return (NOW - timedelta(days=days)).isoformat()


def competitor(video_id, views, *, title=None, days=15, channel="셀럽"):
    return {
        "video_id": video_id, "channel_title": channel, "title": title or video_id,
        "published_at": days_ago(days), "view_count": views, "duration": "PT15M32S",
    }


def analysis(video_id, **fields):
    return {"video_id": video_id, "topic": "일반 주제", "gemini_raw": {"topic": "일반 주제"}, **fields}


def hit_fixture():
    videos = [competitor(f"v{i}", views) for i, views in enumerate([10, 20, 30, 80, 200])]
    videos += [competitor("other", 10000, channel="다른 채널")]
    videos[0]["published_at"] = "2026-11-22T16:00:00Z"  # 월요일 01:00 KST
    videos[1]["published_at"] = "2026-11-23T02:00:00Z"
    analyses = [
        analysis(
            f"v{i}", source_table="initial_analysis", analysis_date=days_ago(12),
            category="라이프" if i else "기타", title_pattern="고백",
            gemini_raw={"topic": "일반 주제", "title_emotion_tone": "따뜻함", "content_structure": "토크형"},
            ad_boost_suspected=i >= 3, deepseek_raw={"emotion_temperature": {"comfort": i}},
        )
        for i in range(5)
    ]
    analyses += [
        analysis("v2", source_table="daily_analysis", analysis_date=days_ago(10), id=4,
                 category="라이프", deepseek_raw={"error": "quota", "emotion_temperature": {"comfort": 99}},
                 title_pattern="고백"),
        analysis("v2", source_table="daily_analysis", analysis_date=days_ago(9), id=5,
                 gemini_raw={"error": "quota", "topic": "실패"}),
    ]
    thumbnails = [
        {"video_id": f"v{i}", "face_count": i, "thumbnail_style": "인물", "food_present": False}
        for i in range(5)
    ]
    scores = [
        {
            "video_id": f"v{i}", "score_version": "v1.2",
            "source_table": "daily_analysis" if i == 2 else "initial_analysis",
            "priority_score": 999 if i == 2 else i, "execution_score": i,
            "novelty_score": 5, "risk_score": 4 - i,
            "recommended_action": "바로 기획화" if i >= 3 else "아이디어 보관",
        }
        for i in range(5)
    ]
    scores += [
        {"video_id": "other", "source_table": "initial_analysis", "priority_score": 100},
        {"video_id": "v4", "score_version": "v1.1", "priority_score": 0},
    ]
    return videos, analyses, thumbnails, scores


class HelpersTest(unittest.TestCase):
    def test_spearman_perfect_tied_and_undefined(self):
        self.assertEqual(learner.spearman_correlation([1, 3, 2], [2, 6, 4]), 1.0)
        self.assertEqual(learner.spearman_correlation([1, 3, 2], [6, 2, 4]), -1.0)
        self.assertAlmostEqual(learner.spearman_correlation([1, 1, 2, 3], [1, 2, 2, 3]), 5 / 6)
        self.assertIsNone(learner.spearman_correlation([1, 2], [2, 3]))
        self.assertIsNone(learner.spearman_correlation([1, 1, 1], [2, 3, 4]))
        self.assertIsNone(learner.spearman_correlation([1, 2, 3], [3, 3, 3]))
        self.assertIsNone(learner.spearman_correlation([1, 2, 3], [1, 2]))

    def test_interpolated_percentiles_and_tiers(self):
        self.assertEqual([learner.percentile([10, 20, 30, 40], p) for p in (0.25, 0.5, 0.75)],
                         [17.5, 25, 32.5])
        self.assertIsNone(learner.percentile([], 0.5))
        self.assertEqual(learner.percentile([3], 0.75), 3)
        with self.assertRaises(ValueError):
            learner.percentile([3], 25)
        self.assertEqual([learner.confidence_tier(n) for n in (0, 6, 7, 14, 15)],
                         ["low", "low", "medium", "medium", "high"])

    def test_duration_and_utc_parsing(self):
        for duration, seconds in [("PT15M32S", 932), ("PT1H2M3S", 3723), ("PT45S", 45),
                                  ("PT0S", 0), ("P1DT2H", 93600), ("P1DT", None),
                                  ("PT", None), ("bad", None), (None, None)]:
            with self.subTest(duration=duration):
                self.assertEqual(learner.parse_iso_duration_seconds(duration), seconds)
        for timestamp in ("2026-11-26T12:00:00Z", "2026-11-26T12:00:00+00:00",
                          "2026-11-26T12:00:00", "2026-11-26T21:00:00+09:00"):
            self.assertEqual(learner.parse_iso_datetime(timestamp), NOW)
        self.assertIsNone(learner.parse_iso_datetime("broken"))


class LeadLagTest(unittest.TestCase):
    def test_spike_buckets_trendsetters_radar_and_token_boundaries(self):
        names = ["시카밤", "로즈솝", "on", "추천", "  가  ", "１２３", "잊힌향"]
        entities = [{"id": i, "name": name, "normalized_name": name} for i, name in enumerate(names)]
        mentions, trend_videos = [], []
        for i, name in enumerate(names):
            ages = (40, 38, 37) if i in (0, 6) else (21, 20, 18)
            for j, age in enumerate(ages):
                channel, video_id = f"c{j}", f"{name}-{j}"
                mentions.append({"entity_id": i, "channel_id": channel, "video_id": video_id,
                                 "published_at": days_ago(age)})
                trend_videos.append({"video_id": video_id, "view_over_baseline": j + 1})
        mentions.append({"entity_id": 0, "channel_id": "c0", "video_id": "extra",
                         "published_at": days_ago(39)})
        mentions.append(dict(mentions[0]))  # Same entity and upload is counted once.
        competitors = [
            competitor("hit", 400, title="시카 밤 후기", days=25),  # spike +12
            competitor("miss", 100, days=16),  # spike +21, matched by keyword
            competitor("late", 100, title="시카밤", days=2),  # spike +35
            competitor("coincident", 100, title="시카밤", days=42),
            competitor("early", 100, title="시카밤", days=46),
            competitor("noise", 100, title="cushion 추천 123"),
        ]
        analyses = [
            analysis("miss", title_keywords=["시카밤"], analysis_date=days_ago(15)),
            analysis("hit", topic="로즈솝", gemini_raw={"error": "failed", "topic": "로즈솝"}),
        ]
        sources = [{"id": i, "source_ref": f"c{i}", "source_name": f"채널 {i}"} for i in range(3)]
        result = learner.analyze_btrack_to_celeb_lead_lag(
            entities, mentions, trend_videos, sources, competitors, analyses,
        )
        self.assertEqual(result["relation_counts"], {"btrack_led": 3, "coincident": 1, "celeb_led": 1})
        self.assertEqual({row["video_id"]: row["lag_days"] for row in result["adoptions"]},
                         {"hit": 12, "miss": 21, "late": 35, "coincident": -5, "early": -9})
        self.assertEqual(result["lag_buckets"]["0-14d"]["hit_rate"], 1.0)
        self.assertEqual(result["lag_buckets"]["0-14d"]["median_vob"], 4.0)
        self.assertEqual(result["lag_buckets"]["15-30d"]["hit_rate"], 0.0)
        self.assertEqual(result["lag_buckets"]["31-60d"]["adoption_count"], 1)
        timeline = next(row for row in result["entity_timelines"] if row["entity"] == "시카밤")
        self.assertEqual(timeline["first_seen_at"], days_ago(40))
        self.assertEqual(timeline["spike_at"], days_ago(37))
        self.assertEqual((timeline["channel_count"], timeline["mention_count"]), (3, 4))
        self.assertEqual(timeline["median_vob"], 2)
        first = result["trendsetters"][0]
        self.assertEqual((first["source_name"], first["celeb_hit_lead_count"], first["lead_entity_count"]),
                         ("채널 0", 1, 1))
        self.assertEqual(first["median_lead_days"], 24)
        self.assertEqual({row["entity"] for row in result["unadopted_radar"]}, {"on", "로즈솝"})
        self.assertEqual({row["entity"] for row in result["entity_timelines"]}, {"on", "로즈솝", "시카밤", "잊힌향"})
        competitors.append(competitor("actual-on", 100, title="ON 쿠션"))
        matched = learner.analyze_btrack_to_celeb_lead_lag(
            entities, mentions, trend_videos, sources, competitors, analyses, now=NOW,
        )
        self.assertIn(("on", "actual-on"), [(row["entity"], row["video_id"]) for row in matched["adoptions"]])


class HitDriversTest(unittest.TestCase):
    def test_normalization_lift_comments_and_score_provenance(self):
        result = learner.analyze_hit_drivers_and_calibrate_scorer(*hit_fixture(), [], min_samples=2)
        self.assertEqual(result["channel_medians"], {"셀럽": 30, "다른 채널": 10000})
        self.assertEqual(result["view_over_channel_median"]["other"], 1)
        self.assertAlmostEqual(result["baseline_hit_rate"], 2 / 6)
        features = result["features"]
        self.assertEqual([row["value"] for row in features["category"]], ["라이프"])
        self.assertEqual([row["value"] for row in features["face_count_bucket"]], ["3+"])
        self.assertEqual(features["food_present"][0]["value"], False)
        self.assertEqual(features["duration_bucket"][0]["n"], 6)
        self.assertEqual({row["value"]: row["n"] for row in features["kst_weekday"]}, {"수": 4, "월": 2})
        self.assertTrue(all(row["n"] >= 2 for groups in result["combinations"].values() for row in groups))
        for key, value in (("category × title_pattern", ["라이프", "고백"]),
                           ("category × thumbnail_style", ["라이프", "인물"])):
            row, = result["combinations"][key]
            self.assertEqual(row["value"], value)
            self.assertEqual((row["n"], row["hit_count"], row["lift"]), (4, 2, 1.5))
        self.assertEqual(result["emotion_temperature_correlations"]["comfort"], {"r": 1, "n": 4})
        self.assertEqual(result["emotion_temperature_correlations"]["cringe"], {"r": None, "n": 0})
        self.assertEqual(result["ad_boost_correlation"]["n"], 4)
        backtest = result["scorer_backtest"]
        self.assertEqual(backtest["excluded_contaminated_daily_score_count"], 1)
        self.assertEqual(backtest["excluded_without_valid_analysis_count"], 1)
        self.assertEqual(backtest["correlations"]["priority_score"], {"r": 1, "n": 4})
        self.assertEqual(backtest["precision_at_top20"]["precision"], 1)
        self.assertEqual(backtest["precision_at_top20"]["lift"], 3)
        self.assertEqual(backtest["recommended_actions"]["바로 기획화"]["hit_rate"], 1)
        weights = backtest["weight_recommendations"]
        self.assertEqual(weights["risk_score"]["current_weight"], -0.15)
        self.assertIn("상향", weights["risk_score"]["recommendation"])
        self.assertIn("표본 부족", weights["execution_score"]["recommendation"])
        self.assertIsNone(weights["novelty_score"]["spearman_r"])

    def test_weak_or_negative_positive_weight_recommends_reduction(self):
        videos, analyses, thumbs, scores = hit_fixture()
        for score in scores:
            if "execution_score" in score:
                score["execution_score"] = 100 - score["execution_score"]
        report = learner.analyze_hit_drivers_and_calibrate_scorer(videos, analyses, thumbs, scores, [])
        recommendation = report["scorer_backtest"]["weight_recommendations"]["execution_score"]
        self.assertEqual(recommendation["spearman_r"], -1)
        self.assertIn("축소", recommendation["recommendation"])


class OwnTrajectoryTest(unittest.TestCase):
    def test_separate_formats_nearest_checkpoints_and_mature_only_bands(self):
        videos, hourly, weekly, analyses = [], [], [], []

        def add(video_id, kind, age, early, final):
            videos.append({"video_id": video_id, "title": video_id, "published_at": days_ago(age),
                           "video_type": kind, "view_count": final})
            hourly.extend({"video_id": video_id, "hours_since_upload": hours, "view_count": views}
                          for hours, views in early)
            weekly.extend([
                {"video_id": video_id, "week_number": 1, "view_count": final / 2},
                {"video_id": video_id, "week_number": 2, "view_count": final * 2},
            ])
            analyses.append(analysis(video_id, content_structure="토크형", title_pattern="고백",
                                     predicted_performance="높음"))

        for i, initial in enumerate((10, 20, 30), 1):
            add(f"long{i}", "longform", 14 + i,
                [(2.9, initial / 2), (4.6, 1), (6.2, initial), (12, initial * 1.5), (24, initial * 2)],
                initial * 10)
        add("new-long", "longform", 2, [(6.0, 1), (24, 10)], 1_000_000)
        add("recovered", "longform", 3, [(6, 1), (24, 40)], 1_000_000)
        add("new-at-6h", "longform", 1, [(6, 2)], 3_000_000)
        add("tie-p25", "longform", 1, [(6, 15)], 1_000_000)
        add("short1", "shorts", 14, [(6, 1000)], 2000)
        add("new-short", "shorts", 1, [(6, 900)], 10_000_000)
        result = learner.analyze_own_channel_early_trajectory(videos, hourly, weekly, analyses, now=NOW)
        longform, shorts = (result["by_video_type"][format] for format in ("longform", "shorts"))
        self.assertEqual((longform["mature_count"], longform["immature_count"]), (3, 4))
        self.assertEqual((shorts["mature_count"], shorts["immature_count"]), (1, 1))
        six = longform["checkpoints"]["6h"]
        self.assertEqual(set(longform["checkpoints"]), {"3h", "6h", "12h", "24h"})
        self.assertEqual(six["views"], {"n": 3, "p25": 15, "p50": 20, "p75": 25})
        self.assertEqual(six["week1_multiplier"], {"n": 3, "p25": 5, "p50": 5, "p75": 5})
        self.assertEqual(six["final_multiplier"], {"n": 3, "p25": 10, "p50": 10, "p75": 10})
        self.assertEqual(six["views_vs_final"], {"r": 1, "n": 3})
        self.assertEqual(longform["intervention_thresholds"], {"6h": 15, "24h": 30})
        self.assertEqual({row["video_id"]: row["checkpoint"] for row in longform["intervention_candidates"]},
                         {"new-long": "24h", "new-at-6h": "6h"})
        self.assertEqual(shorts["checkpoints"]["6h"]["views"]["p50"], 1000)
        self.assertEqual(shorts["checkpoints"]["6h"]["final_multiplier"]["p50"], 2)
        self.assertEqual(shorts["intervention_candidates"][0]["video_id"], "new-short")
        pattern, = longform["analysis_patterns"]["content_structure"]
        self.assertEqual(pattern["view_over_type_median"], {"n": 3, "p25": 0.75, "p50": 1, "p75": 1.25})


class ReportTest(unittest.TestCase):
    def test_audit_counts_latest_valid_unique_analyses_and_warns_about_old_data(self):
        initial = [analysis("initial"), analysis("also-daily"), analysis("out-of-scope")]
        daily = [
            analysis("also-daily", id=1, analysis_date=days_ago(2)),
            analysis("also-daily", id=2, analysis_date=days_ago(1)),
            analysis("daily-only", id=3, analysis_date=days_ago(5)),
            analysis("daily-only", id=4, analysis_date=days_ago(3), gemini_raw={"error": "quota"}),
            analysis("blank-topic", topic="  "),
        ]
        trend_videos = [
            {"published_at": days_ago(day), "view_over_baseline": 0.2 if day == 0 else 0.29}
            for day in range(200)
        ]
        trend_videos.insert(0, {"published_at": days_ago(201), "view_over_baseline": 5})
        report = learner.audit_data_quality({
            "videos": [{"video_id": video_id} for video_id in
                       ("initial", "also-daily", "daily-only", "blank-topic")],
            "initial_analysis": initial, "daily_analysis": daily,
            "video_scores": [{"video_id": "daily-only", "source_table": "daily_analysis"}],
            "video_snapshots": [{"video_id": "also-daily", "growth_pattern": ""}],
            "own_channel_analysis": [analysis("own"), analysis("own-failed", gemini_raw=None)],
            "trend_videos": trend_videos,
        })
        expected = {
            "competition_video_count": 4, "valid_competition_analysis_count": 3,
            "unanalyzed_competition_video_count": 1, "daily_analysis_count": 5,
            "failed_daily_analysis_count": 2, "duplicate_valid_daily_analysis_count": 1,
            "own_analysis_count": 2, "failed_own_analysis_count": 1,
            "recent_btrack_baseline_n": 200, "recent_btrack_median_vob": 0.29,
            "growth_pattern_video_count": 0, "contaminated_daily_score_count": 1,
        }
        self.assertEqual({key: report[key] for key in expected}, expected)
        self.assertEqual(len(report["caveats"]), 3)
        self.assertIn("stale baseline", report["caveats"][0])
        self.assertIn("growth-pattern", report["caveats"][1])
        self.assertIn("일일 분석", report["caveats"][2])

    def test_playbook_threshold_and_six_sections_through_cli(self):
        videos, analyses, thumbs, scores = hit_fixture()
        dataset = {
            "videos": videos,
            **{table: [row for row in analyses if row["source_table"] == table]
               for table in ("initial_analysis", "daily_analysis")},
            "thumbnail_analysis": thumbs, "video_scores": scores, "video_snapshots": [],
            "own_channel_videos": [], "own_channel_analysis": [], "own_channel_snapshots": [],
            "own_channel_hourly_snapshots": [],
        }
        with TemporaryDirectory() as directory, patch.object(
            learner, "load_dataset_from_supabase", return_value=dataset,
        ) as load:
            markdown = Path(directory) / "nested" / "report.md"
            structured = Path(directory) / "report.json"
            self.assertEqual(learner.main([
                "--out", str(markdown), "--json-out", str(structured),
                "--min-samples", "2", "--lookback-days", "60", "--skip-btrack",
            ]), 0)
            report = json.loads(structured.read_text(encoding="utf-8"))
            rendered = markdown.read_text(encoding="utf-8")
        self.assertEqual(load.call_args.kwargs["lookback_days"], 60)
        self.assertTrue(load.call_args.kwargs["skip_btrack"])
        self.assertEqual(report["lookback_days"], 60)
        self.assertIn("라이프", rendered)
        self.assertIn("Precision@Top20%", rendered)
        headers = [line for line in rendered.splitlines() if line.startswith("## ")]
        self.assertEqual(len(headers), 6)
        for index, section in enumerate(("데이터 품질", "B-track", "경쟁 채널",
                                        "strategy_scorer", "자체 채널", "검증된 학습"), 1):
            self.assertTrue(headers[index - 1].startswith(f"## {index}. {section}"))
        self.assertIn("### 롱폼", rendered)
        self.assertIn("### 쇼츠", rendered)
        self.assertIn("category", report["learned_playbook"]["hit_drivers"])
        at_five = learner.build_learned_playbook(report, min_samples=5)
        self.assertNotIn("category", at_five["hit_drivers"])
        self.assertFalse(at_five["combinations"])
        self.assertFalse(at_five["emotion_temperature"])
        self.assertFalse(at_five["scorer_weights"])


class SupabaseReadOnlyTest(unittest.TestCase):
    def test_paginates_every_table_through_postgrest_using_only_get(self):
        table_names = {
            "videos", "initial_analysis", "daily_analysis", "thumbnail_analysis", "video_scores",
            "video_snapshots", "own_channel_videos", "own_channel_analysis", "own_channel_snapshots",
            "own_channel_hourly_snapshots", "trend_sources", "trend_entities", "trend_videos",
            "entity_mentions",
        }
        requests = []
        records = [{"video_id": f"v{i:04}", "id": i} for i in range(1003)]

        def respond(request):
            requests.append(request)
            params = request.url.params
            self.assertEqual(len(params.get_list("offset")), 1)
            self.assertEqual(params.get_list("limit"), ["1000"])
            offset = int(params["offset"])
            return httpx.Response(200, json=records[offset:offset + 1000])

        base_url = "https://postgrest.test/rest/v1"
        with SyncPostgrestClient(base_url) as db, httpx.Client(
            base_url=base_url, transport=httpx.MockTransport(respond),
        ) as http, patch.object(db.session, "request", side_effect=http.request):
            data = learner.load_dataset_from_supabase(db, now=NOW)
            self.assertEqual(set(data), table_names)
            self.assertTrue(all(rows == records for rows in data.values()))
            self.assertEqual(len(requests), 2 * len(table_names))
            self.assertEqual({request.method for request in requests}, {"GET"})
            for table in table_names:
                pages = [request.url.params for request in requests
                         if request.url.path.rsplit("/", 1)[-1] == table]
                with self.subTest(table=table):
                    self.assertEqual([page["offset"] for page in pages], ["0", "1000"])
                    self.assertEqual(pages[0]["order"], pages[1]["order"])
                    self.assertIn(pages[0]["order"].split(",")[0], {"id", "video_id"})
                    self.assertNotIn("*", pages[0]["select"])
                    if table == "video_snapshots":
                        self.assertTrue(all(page["days_since_publish"] == "in.(1,7,30)" for page in pages))
                    if table in {"entity_mentions", "trend_videos"}:
                        self.assertTrue(all(page["published_at"] == "gte." + days_ago(210) for page in pages))
                        self.assertLessEqual(len(pages[0]["select"].split(",")), 5)
                    if table == "own_channel_analysis":
                        self.assertNotIn("id", pages[0]["select"].split(","))
                        self.assertNotIn("id", pages[0]["order"].split(","))
            requests.clear()
            data = learner.load_dataset_from_supabase(db, now=NOW, skip_btrack=True)
            for table in ("trend_sources", "trend_entities", "trend_videos", "entity_mentions"):
                self.assertEqual(data[table], [])
                self.assertFalse(any(request.url.path.endswith("/" + table) for request in requests))
            self.assertEqual({request.method for request in requests}, {"GET"})

    def test_unconfigured_client_raises(self):
        with patch.object(learner.supabase_writer, "get_client", return_value=None):
            with self.assertRaises(RuntimeError):
                learner.load_dataset_from_supabase()


if __name__ == "__main__":
    unittest.main()
