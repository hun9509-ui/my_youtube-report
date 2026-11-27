import unittest

from strategy_scorer import PRIORITY_WEIGHTS, score_batch, score_video


SOURCE = "initial_analysis"


class StrategyScorerTest(unittest.TestCase):
    def setUp(self):
        self.videos = {
            "high": {"video_id": "high", "title": "부부의 패션 토크"},
            "low": {"video_id": "low", "title": "집밥 요리 레시피 3가지"},
        }
        self.analyses = [
            {
                "video_id": "high",
                "gemini_raw": {
                    "category": "패션",
                    "applicability": "상",
                    "content_structure": "토크형",
                    "title_pattern": "단정형",
                    "title_emotion_tone": "놀람",
                    "ppl_likely": False,
                },
                "deepseek_raw": {"emotion_temperature": {"envy": "25", "aspiration": 15}},
                "ad_boost_suspected": False,
            },
            {
                "video_id": "low",
                "gemini_raw": {
                    "category": "요리",
                    "applicability": "하",
                    "content_structure": "정보전달형",
                    "title_pattern": "숫자형",
                },
                "title_emotion_tone": "공감",
                "deepseek_raw": {
                    "emotion_temperature": {"fatigue": "22"},
                    "ad_boost_suspected": True,
                },
            },
        ]
        self.thumbnails = {
            "high": {"couple_present": True, "face_count": 2, "main_emotion": "놀람",
                     "home_visible": False, "luxury_signal": True},
            "low": {"face_count": 1, "food_present": True},
        }

    def test_high_and_low_lift_signals_split_recommended_actions(self):
        high, low = score_batch(self.videos, self.analyses, SOURCE, thumbnails_map=self.thumbnails)
        self.assertEqual(high["recommended_action"], "바로 기획화")
        self.assertEqual(low["recommended_action"], "우선순위 낮음")
        self.assertEqual(
            (high["hangoeun_fit_score"], high["novelty_score"], high["execution_score"],
             high["repeatability_score"], high["risk_score"]),
            (92, 100, 100, 83, 10),
        )
        self.assertEqual(
            (low["hangoeun_fit_score"], low["novelty_score"], low["execution_score"],
             low["repeatability_score"], low["risk_score"]),
            (25, 0, 64, 45, 60),
        )
        self.assertIn("thumbnail.couple_present +12", high["rule_trace"]["novelty"])
        self.assertIn("thumbnail.food_present -12", low["rule_trace"]["novelty"])
        self.assertIn("ad_boost_suspected +35", low["rule_trace"]["risk"])

    def test_optional_thumbnail_and_legacy_calls(self):
        high_without = score_video(self.videos["high"], self.analyses[0], SOURCE)
        self.assertEqual(high_without, score_video(self.videos["high"], self.analyses[0], SOURCE, thumbnail=None))
        legacy_batch = score_batch(self.videos, self.analyses, SOURCE)
        self.assertEqual(legacy_batch[0], high_without)
        high_with, low_without = score_batch(
            self.videos, self.analyses, SOURCE, thumbnails_map={"high": self.thumbnails["high"]},
        )
        self.assertEqual(high_with, score_video(
            self.videos["high"], self.analyses[0], SOURCE, thumbnail=self.thumbnails["high"],
        ))
        self.assertEqual(high_without["novelty_score"], 89)
        self.assertGreater(high_with["priority_score"], high_without["priority_score"])
        self.assertEqual(low_without, legacy_batch[1])

    def test_weighted_priority_bounded_subscores_and_trace(self):
        self.assertAlmostEqual(sum(weight for weight in PRIORITY_WEIGHTS.values() if weight > 0), 1.0)
        for score in score_batch(self.videos, self.analyses, SOURCE, self.thumbnails):
            with self.subTest(video_id=score["video_id"]):
                for field in (*PRIORITY_WEIGHTS, "priority_score"):
                    self.assertLessEqual(0, score[field], field)
                    self.assertLessEqual(score[field], 100, field)
                raw = sum(score[field] * weight for field, weight in PRIORITY_WEIGHTS.items())
                self.assertEqual(score["priority_score"], max(0, min(100, round(raw))))
                self.assertTrue(score["rule_trace"])
                self.assertTrue(all(score["rule_trace"].values()))

    def test_malformed_comment_and_thumbnail_values_are_ignored_individually(self):
        score = score_video({}, {
            "ad_boost_suspected": True,
            "deepseek_raw": {"emotion_temperature": {
                "envy": "invalid", "aspiration": "20", "fatigue": "NaN", "cringe": "15",
            }},
        }, SOURCE, thumbnail={"face_count": [], "couple_present": "true", "home_visible": 0})
        self.assertEqual(score["novelty_score"], 30)
        self.assertEqual(score["risk_score"], 45)
        baseline = score_video({}, {}, SOURCE)
        self.assertEqual(score_video({}, {"comments": {"emotion_temperature": [1]}}, SOURCE), baseline)


if __name__ == "__main__":
    unittest.main()
