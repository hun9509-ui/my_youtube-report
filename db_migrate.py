"""
DB 마이그레이션 스크립트
실행: python db_migrate.py
필요 환경변수: DATABASE_URL (Supabase Settings > Database > URI)
"""
import os
from contextlib import closing

import psycopg2
from gemini_client import safe_error

DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL 환경변수 미설정")

SQL = """
-- video_snapshots: 경쟁채널 + 자체채널 시계열 스냅샷
CREATE TABLE IF NOT EXISTS video_snapshots (
    id SERIAL PRIMARY KEY,
    video_id TEXT NOT NULL,
    channel_title TEXT,
    snapshot_date DATE NOT NULL,
    days_since_publish INTEGER,
    view_count BIGINT DEFAULT 0,
    like_count BIGINT DEFAULT 0,
    comment_count BIGINT DEFAULT 0,
    view_growth BIGINT DEFAULT 0,
    like_growth BIGINT DEFAULT 0,
    comment_growth BIGINT DEFAULT 0,
    view_growth_rate FLOAT DEFAULT 0,
    comment_growth_rate FLOAT DEFAULT 0,
    source_type TEXT DEFAULT 'competitor',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(video_id, snapshot_date)
);

-- thumbnail_analysis: 썸네일 Vision 분석 결과
CREATE TABLE IF NOT EXISTS thumbnail_analysis (
    video_id TEXT PRIMARY KEY,
    analyzed_at TIMESTAMPTZ DEFAULT NOW(),
    face_count INTEGER,
    main_emotion TEXT,
    food_present BOOLEAN,
    couple_present BOOLEAN,
    family_present BOOLEAN,
    home_visible BOOLEAN,
    luxury_signal BOOLEAN,
    text_overlay BOOLEAN,
    thumbnail_style TEXT,
    camera_distance TEXT,
    emotion_intensity INTEGER,
    ctr_prediction TEXT,
    ctr_reason TEXT,
    model_used TEXT,
    raw_analysis JSONB
);

CREATE INDEX IF NOT EXISTS idx_video_snapshots_video_id ON video_snapshots(video_id);
CREATE INDEX IF NOT EXISTS idx_video_snapshots_date ON video_snapshots(snapshot_date);
CREATE INDEX IF NOT EXISTS idx_video_snapshots_source ON video_snapshots(source_type, snapshot_date);

-- trend_entity_scores: B-track 엔티티별 반응/확산/추천 점수
CREATE TABLE IF NOT EXISTS trend_entity_scores (
    id BIGSERIAL PRIMARY KEY,
    entity_id BIGINT NOT NULL,
    entity_name TEXT NOT NULL,
    entity_type TEXT,
    category TEXT,
    scored_at DATE NOT NULL,

    mention_count_7d INT DEFAULT 0,
    mention_count_30d INT DEFAULT 0,
    mention_count_90d INT DEFAULT 0,
    distinct_channels_7d INT DEFAULT 0,
    distinct_channels_30d INT DEFAULT 0,
    channel_diversity_score NUMERIC DEFAULT 0,
    recurrence_score NUMERIC DEFAULT 0,

    avg_view_over_baseline NUMERIC DEFAULT 0,
    median_view_over_baseline NUMERIC DEFAULT 0,
    best_view_over_baseline NUMERIC DEFAULT 0,
    overperform_count INT DEFAULT 0,
    underperform_count INT DEFAULT 0,
    hit_ratio NUMERIC DEFAULT 0,
    total_views_30d BIGINT DEFAULT 0,
    engagement_score NUMERIC DEFAULT 0,

    velocity_7d NUMERIC DEFAULT 0,
    velocity_30d NUMERIC DEFAULT 0,
    acceleration_score NUMERIC DEFAULT 0,
    trend_stage TEXT,
    stage_reason TEXT,
    peak_risk NUMERIC DEFAULT 0,
    saturation_score NUMERIC DEFAULT 0,
    freshness_score NUMERIC DEFAULT 0,

    best_format TEXT,
    format_distribution JSONB,
    best_examples JSONB,

    commerce_signal NUMERIC DEFAULT 0,
    brand_heat_score NUMERIC DEFAULT 0,
    item_heat_score NUMERIC DEFAULT 0,
    shopping_intent_score NUMERIC DEFAULT 0,
    ppl_potential NUMERIC DEFAULT 0,

    channel_fit_score NUMERIC DEFAULT 0,
    timing_score NUMERIC DEFAULT 0,
    production_difficulty NUMERIC DEFAULT 0,
    originality_room NUMERIC DEFAULT 0,
    recommended_action TEXT,
    suggested_angle TEXT,

    confidence TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),

    UNIQUE(entity_id, scored_at)
);

CREATE INDEX IF NOT EXISTS idx_trend_entity_scores_date
ON trend_entity_scores(scored_at DESC);

CREATE INDEX IF NOT EXISTS idx_trend_entity_scores_action
ON trend_entity_scores(recommended_action, avg_view_over_baseline DESC);

CREATE INDEX IF NOT EXISTS idx_trend_entity_scores_stage
ON trend_entity_scores(trend_stage, scored_at DESC);

-- trend_answer_cards: 자연어 질문응답용 B-track 엔티티 요약 카드
CREATE TABLE IF NOT EXISTS trend_answer_cards (
    id BIGSERIAL PRIMARY KEY,
    entity_id BIGINT NOT NULL,
    entity_name TEXT NOT NULL,
    card_date DATE NOT NULL,
    question_type TEXT,

    answer_summary TEXT,
    evidence_bullets JSONB,
    caution_bullets JSONB,
    recommended_content_ideas JSONB,
    best_examples JSONB,
    confidence TEXT,

    created_at TIMESTAMPTZ DEFAULT NOW(),

    UNIQUE(entity_id, card_date, question_type)
);

CREATE INDEX IF NOT EXISTS idx_trend_answer_cards_date
ON trend_answer_cards(card_date DESC);

CREATE INDEX IF NOT EXISTS idx_trend_answer_cards_entity
ON trend_answer_cards(entity_name, card_date DESC);

-- 기존 분석 이력은 NULL로 유지한다. 새 자동 실행 결과에만 고유 키를 사용한다.
ALTER TABLE IF EXISTS public.daily_analysis ADD COLUMN IF NOT EXISTS processing_key TEXT UNIQUE;
ALTER TABLE IF EXISTS public.weekly_reports ADD COLUMN IF NOT EXISTS processing_key TEXT UNIQUE;
ALTER TABLE IF EXISTS public.trend_classified ADD COLUMN IF NOT EXISTS processing_key TEXT UNIQUE;
ALTER TABLE IF EXISTS public.event_keywords ADD COLUMN IF NOT EXISTS processing_key TEXT UNIQUE;
NOTIFY pgrst, 'reload schema';
"""

if __name__ == "__main__":
    try:
        with closing(psycopg2.connect(DATABASE_URL)) as conn, conn:
            with conn.cursor() as cursor:
                cursor.execute(SQL)
    except psycopg2.Error as exc:
        raise SystemExit(f"❌ 마이그레이션 실패: {safe_error(exc)}") from None
    print("✅ 마이그레이션 완료")
