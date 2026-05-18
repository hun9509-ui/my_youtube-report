"""
DB 마이그레이션 스크립트
실행: python db_migrate.py
필요 환경변수: DATABASE_URL (Supabase Settings > Database > URI)
"""
import os
import psycopg2

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
"""

conn = psycopg2.connect(DATABASE_URL)
cur = conn.cursor()
cur.execute(SQL)
conn.commit()
cur.close()
conn.close()
print("✅ 마이그레이션 완료")
