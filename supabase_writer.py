"""
Supabase 이중 저장 모듈
시트와 동일한 데이터를 PostgreSQL DB에도 저장
"""
from supabase import create_client, Client
from datetime import datetime, timedelta
import config


_client: Client = None

def get_client() -> Client:
    """Supabase 클라이언트 (지연 초기화)"""
    global _client
    if _client is None:
        if not config.SUPABASE_URL or not config.SUPABASE_KEY:
            print("⚠️ SUPABASE_URL/KEY 미설정")
            return None
        _client = create_client(config.SUPABASE_URL, config.SUPABASE_KEY)
    return _client


def save_videos(videos):
    """영상 마스터 저장 (중복 시 업데이트)"""
    client = get_client()
    if not client:
        return 0
    
    rows = []
    for v in videos:
        rows.append({
            'video_id': v.get('video_id', ''),
            'channel_title': v.get('channel_title', ''),
            'title': v.get('title', ''),
            'description': v.get('description', '')[:1000],
            'published_at': v.get('published_at'),
            'view_count': v.get('view_count', 0),
            'like_count': v.get('like_count', 0),
            'comment_count': v.get('comment_count', 0),
            'duration': v.get('duration', ''),
            'tags': str(v.get('tags', '')),
            'thumbnail_url': v.get('thumbnail_url', ''),
            'video_url': v.get('video_url', ''),
        })
    
    try:
        result = client.table('videos').upsert(rows).execute()
        print(f"  💾 Supabase: videos {len(rows)}개 저장")
        return len(rows)
    except Exception as e:
        print(f"  ⚠️ Supabase videos 저장 실패: {e}")
        return 0


def delete_initial_batch(batch_num):
    """배치 재실행 전 기존 데이터 삭제"""
    client = get_client()
    if not client:
        return
    try:
        client.table('initial_analysis').delete().eq('batch_num', batch_num).execute()
        print(f"  🗑️ Supabase: 배치#{batch_num} 기존 데이터 삭제 완료")
    except Exception as e:
        print(f"  ⚠️ 배치 데이터 삭제 실패: {e}")


def save_hit_analysis(video_id, hit_analysis):
    """대박 영상 심층 분석 결과 업데이트"""
    client = get_client()
    if not client:
        return
    try:
        client.table('initial_analysis')\
            .update({'hit_analysis': hit_analysis, 'is_hit': True})\
            .eq('video_id', video_id)\
            .execute()
        print(f"  ⭐ Supabase: hit_analysis 저장 ({video_id[:8]}...)")
    except Exception as e:
        print(f"  ⚠️ hit_analysis 저장 실패: {e}")


def save_initial_analysis(analyses, batch_num):
    """초기 분석 결과 저장"""
    client = get_client()
    if not client:
        return 0

    today = datetime.now().date().isoformat()
    rows = []

    for a in analyses:
        gemini = a.get('gemini', {}) or {}
        comments = a.get('comments', {}) or {}
        sentiment = comments.get('sentiment', {}) if isinstance(comments, dict) else {}

        rows.append({
            'analysis_date': today,
            'batch_num': batch_num,
            'video_id': a.get('video_id', ''),
            'topic': gemini.get('topic', ''),
            'category': gemini.get('category', ''),
            'title_pattern': gemini.get('title_pattern', ''),
            'title_keywords': gemini.get('title_keywords', []),
            'hook_strategy': gemini.get('hook_strategy', ''),
            'ppl_likely': gemini.get('ppl_likely', False),
            'ppl_signals': gemini.get('ppl_signals', ''),
            'target_audience': gemini.get('target_audience', ''),
            'performance_level': gemini.get('performance_level', ''),
            'success_factors': gemini.get('success_factors', ''),
            'applicability': gemini.get('applicability', ''),
            'applicability_reason': gemini.get('applicability_reason', ''),
            'sentiment': sentiment,
            'main_keywords': comments.get('main_keywords', []),
            'viewer_persona': comments.get('viewer_persona', ''),
            'praise_points': comments.get('praise_points', []),
            'complaints': comments.get('complaints', []),
            'suggestions': comments.get('suggestions', []),
            'comments_summary': comments.get('summary', ''),
            'gemini_raw': {**gemini, '_version': config.ANALYSIS_VERSION},
            'deepseek_raw': {**comments, '_version': config.ANALYSIS_VERSION},
            'is_hit': a.get('is_hit', False),
            'channel_avg_views': a.get('channel_avg_views', 0),
        })

    try:
        client.table('initial_analysis').insert(rows).execute()
        print(f"  💾 Supabase: initial_analysis {len(rows)}개 저장")
        return len(rows)
    except Exception as e:
        print(f"  ⚠️ Supabase initial_analysis 저장 실패: {e}")
        return 0


def save_daily_analysis(analyses):
    """일일 분석 결과 저장"""
    client = get_client()
    if not client:
        return 0
    
    today = datetime.now().date().isoformat()
    rows = []
    
    for a in analyses:
        gemini = a.get('gemini', {}) or {}
        comments = a.get('comments', {}) or {}
        sentiment = comments.get('sentiment', {}) if isinstance(comments, dict) else {}
        
        rows.append({
            'analysis_date': today,
            'video_id': a.get('video_id', ''),
            'topic': gemini.get('topic', ''),
            'category': gemini.get('category', ''),
            'title_pattern': gemini.get('title_pattern', ''),
            'title_keywords': gemini.get('title_keywords', []),
            'hook_strategy': gemini.get('hook_strategy', ''),
            'ppl_likely': gemini.get('ppl_likely', False),
            'ppl_signals': gemini.get('ppl_signals', ''),
            'target_audience': gemini.get('target_audience', ''),
            'performance_level': gemini.get('performance_level', ''),
            'success_factors': gemini.get('success_factors', ''),
            'applicability': gemini.get('applicability', ''),
            'applicability_reason': gemini.get('applicability_reason', ''),
            'sentiment': sentiment,
            'main_keywords': comments.get('main_keywords', []),
            'viewer_persona': comments.get('viewer_persona', ''),
            'praise_points': comments.get('praise_points', []),
            'complaints': comments.get('complaints', []),
            'suggestions': comments.get('suggestions', []),
            'comments_summary': comments.get('summary', ''),
            'gemini_raw': {**gemini, '_version': config.ANALYSIS_VERSION},
            'deepseek_raw': {**comments, '_version': config.ANALYSIS_VERSION},
        })

    try:
        result = client.table('daily_analysis').insert(rows).execute()
        print(f"  💾 Supabase: daily_analysis {len(rows)}개 저장")
        return len(rows)
    except Exception as e:
        print(f"  ⚠️ Supabase daily_analysis 저장 실패: {e}")
        return 0


def save_weekly_report(report: dict) -> bool:
    """주간 리포트 저장"""
    client = get_client()
    if not client:
        return False
    today = datetime.now().date().isoformat()
    try:
        client.table('weekly_reports').insert({
            'report_date': today,
            'week_summary': report.get('week_summary', ''),
            'top_performing_channels': report.get('top_performing_channels', []),
            'rising_topics': report.get('rising_topics', []),
            'format_trends': report.get('format_trends', []),
            'ppl_observations': report.get('ppl_observations', ''),
            'actionable_insights_for_hangoeun': report.get('actionable_insights_for_hangoeun', []),
            'next_video_suggestions': report.get('next_video_suggestions', []),
            'raw_data': report,
        }).execute()
        print(f"  💾 Supabase: weekly_reports 저장")
        return True
    except Exception as e:
        print(f"  ⚠️ weekly_reports 저장 실패: {e}")
        return False


def save_channel_insights(channel_insights):
    """채널 인사이트 저장"""
    client = get_client()
    if not client:
        return 0
    
    today = datetime.now().date().isoformat()
    rows = []
    
    for channel, insights in channel_insights.items():
        if 'error' in insights:
            continue
        rows.append({
            'analysis_date': today,
            'channel_name': channel,
            'success_formula': insights.get('success_formula', ''),
            'winning_topics': insights.get('winning_topics', []),
            'winning_title_patterns': insights.get('winning_title_patterns', []),
            'failure_patterns': insights.get('failure_patterns', []),
            'differentiator': insights.get('differentiator', ''),
            'lessons_for_hangoeun': insights.get('lessons_for_hangoeun', []),
            'raw_data': insights,
        })
    
    if not rows:
        return 0
    
    try:
        result = client.table('channel_insights').insert(rows).execute()
        print(f"  💾 Supabase: channel_insights {len(rows)}개 저장")
        return len(rows)
    except Exception as e:
        print(f"  ⚠️ Supabase channel_insights 저장 실패: {e}")
        return 0

def save_trend_classified(classified: dict) -> int:
    """트렌드 분류 결과를 Supabase에 저장"""
    client = get_client()
    if not client:
        return 0
 
    today = datetime.now().date().isoformat()
    rows = []
 
    def _to_row(v, track):
        return {
            'analysis_date': today,
            'track': track,
            'video_id': v.get('video_id', ''),
            'title': v.get('title', ''),
            'channel_title': v.get('channel_title', ''),
            'view_count': v.get('view_count', 0),
            'like_count': v.get('like_count', 0),
            'comment_count': v.get('comment_count', 0),
            'published_at': v.get('published_at'),
            'track_score_a': v.get('track_score_a', 0),
            'track_score_b': v.get('track_score_b', 0),
            'matched_keywords': v.get('matched_keywords', []),
            'irregular_reasons': v.get('irregular_reasons', []),
            'irregular_metrics': v.get('irregular_metrics', {}),
        }
 
    for v in classified.get('track_a', []):
        rows.append(_to_row(v, 'A'))
    for v in classified.get('track_b', []):
        rows.append(_to_row(v, 'B'))
    for v in classified.get('irregular', []):
        rows.append(_to_row(v, 'IRREGULAR'))
 
    if not rows:
        return 0
 
    try:
        client.table('trend_classified').insert(rows).execute()
 
        # 사건 키워드 별도 저장
        event_kws = classified.get('event_keywords', [])
        if event_kws:
            client.table('event_keywords').insert({
                'analysis_date': today,
                'keywords': event_kws,
            }).execute()
 
        print(f"  💾 Supabase: trend_classified {len(rows)}개 저장")
        return len(rows)
    except Exception as e:
        print(f"  ⚠️ Supabase trend_classified 저장 실패: {e}")
        return 0
 
 
# ───────────────────────────────────────────────
# 자체 채널 분석
# ───────────────────────────────────────────────

def is_own_video_analyzed(video_id: str) -> bool:
    """분석까지 완료된 자체 채널 영상인지 확인 (own_channel_analysis 기준)"""
    client = get_client()
    if not client:
        return False
    try:
        result = client.table('own_channel_analysis').select('video_id').eq('video_id', video_id).execute()
        return len(result.data) > 0
    except Exception:
        return False


def save_own_video(video: dict) -> bool:
    """자체 채널 영상 저장 (upsert)"""
    client = get_client()
    if not client:
        return False
    try:
        client.table('own_channel_videos').upsert({
            'video_id': video.get('video_id'),
            'title': video.get('title', ''),
            'published_at': video.get('published_at'),
            'video_type': video.get('video_type', 'longform'),
            'view_count': video.get('view_count', 0),
            'like_count': video.get('like_count', 0),
            'comment_count': video.get('comment_count', 0),
            'duration': video.get('duration', ''),
            'duration_seconds': video.get('duration_seconds', 0),
            'thumbnail_url': video.get('thumbnail_url', ''),
            'video_url': video.get('video_url', ''),
        }).execute()
        return True
    except Exception as e:
        print(f"  ⚠️ own_channel_videos 저장 실패: {e}")
        return False


def save_own_analysis(video_id: str, gemini_result: dict, comments_result: dict):
    """자체 채널 분석 결과 저장"""
    client = get_client()
    if not client:
        return
    today = datetime.now().date().isoformat()
    gemini = gemini_result or {}
    comments = comments_result or {}
    sentiment = comments.get('sentiment', {}) if isinstance(comments, dict) else {}
    try:
        client.table('own_channel_analysis').upsert({
            'video_id': video_id,
            'analysis_date': today,
            'topic': gemini.get('topic', ''),
            'content_structure': gemini.get('content_structure', ''),
            'title_pattern': gemini.get('title_pattern', ''),
            'title_emotion_tone': gemini.get('title_emotion_tone', ''),
            'hook_strategy': gemini.get('hook_strategy', ''),
            'ppl_likely': gemini.get('ppl_likely', False),
            'ppl_signals': gemini.get('ppl_signals', ''),
            'target_audience': gemini.get('target_audience', ''),
            'predicted_performance': gemini.get('predicted_performance', ''),
            'predicted_performance_reason': gemini.get('predicted_performance_reason', ''),
            'strengths': gemini.get('strengths', ''),
            'improvement_points': gemini.get('improvement_points', ''),
            'thumbnail_suggestion': gemini.get('thumbnail_suggestion', ''),
            'competitor_angle': gemini.get('competitor_angle', ''),
            'sentiment': sentiment,
            'praise_points': comments.get('praise_points', []),
            'complaints': comments.get('complaints', []),
            'next_video_requests': comments.get('next_video_requests', []),
            'new_viewer_signals': comments.get('new_viewer_signals', ''),
            'fan_engagement': comments.get('fan_engagement', ''),
            'viral_signals': comments.get('viral_signals', ''),
            'revisit_intent': comments.get('revisit_intent', 0),
            'ppl_reaction': comments.get('ppl_reaction', ''),
            'creator_feedback': comments.get('creator_feedback', ''),
            'comments_summary': comments.get('summary', ''),
            'gemini_raw': {**gemini, '_version': config.ANALYSIS_VERSION},
            'deepseek_raw': {**comments, '_version': config.ANALYSIS_VERSION},
        }).execute()
        print(f"  💾 Supabase: own_channel_analysis 저장 ({video_id[:8]}...)")
    except Exception as e:
        print(f"  ⚠️ own_channel_analysis 저장 실패: {e}")


def get_all_own_analyses() -> list:
    """자체 채널 분석 결과 전체 조회 (영상 정보 포함)"""
    client = get_client()
    if not client:
        return []
    try:
        result = client.table('own_channel_analysis')\
            .select('*, own_channel_videos(*)')\
            .execute()
        return result.data
    except Exception as e:
        print(f"  ⚠️ own_channel_analysis 조회 실패: {e}")
        return []


def get_tracking_videos() -> list:
    """추적 활성화 중인 자체 채널 영상 목록"""
    client = get_client()
    if not client:
        return []
    try:
        result = client.table('own_channel_videos').select('*').eq('tracking_active', True).execute()
        return result.data
    except Exception as e:
        print(f"  ⚠️ 추적 영상 조회 실패: {e}")
        return []


def count_snapshots(video_id: str) -> int:
    """영상의 스냅샷 횟수"""
    client = get_client()
    if not client:
        return 0
    try:
        result = client.table('own_channel_snapshots').select('id', count='exact').eq('video_id', video_id).execute()
        return result.count or 0
    except Exception as e:
        print(f"  ⚠️ 스냅샷 카운트 실패: {e}")
        return 0


def get_last_snapshot(video_id: str) -> dict | None:
    """마지막 스냅샷 조회"""
    client = get_client()
    if not client:
        return None
    try:
        result = client.table('own_channel_snapshots').select('*').eq('video_id', video_id)\
            .order('week_number', desc=True).limit(1).execute()
        return result.data[0] if result.data else None
    except Exception as e:
        print(f"  ⚠️ 스냅샷 조회 실패: {e}")
        return None


def get_snapshot_by_week(video_id: str, week_number: int) -> dict | None:
    """특정 주차 스냅샷 조회 (중복 저장 방지용)"""
    client = get_client()
    if not client:
        return None
    try:
        result = client.table('own_channel_snapshots').select('id')\
            .eq('video_id', video_id).eq('week_number', week_number).execute()
        return result.data[0] if result.data else None
    except Exception as e:
        print(f"  ⚠️ 주차 스냅샷 조회 실패: {e}")
        return None


def save_own_snapshot(video_id: str, week_number: int, current_stats: dict,
                      prev_view_count: int, comment_analysis: dict = None):
    """주별 스냅샷 저장"""
    client = get_client()
    if not client:
        return
    today = datetime.now().date().isoformat()
    view_growth = current_stats.get('view_count', 0) - prev_view_count
    try:
        client.table('own_channel_snapshots').insert({
            'video_id': video_id,
            'snapshot_date': today,
            'week_number': week_number,
            'view_count': current_stats.get('view_count', 0),
            'like_count': current_stats.get('like_count', 0),
            'comment_count': current_stats.get('comment_count', 0),
            'view_growth': view_growth,
            'comment_analysis': comment_analysis,
        }).execute()
        print(f"  📸 스냅샷 week#{week_number}: {video_id[:8]}... {current_stats.get('view_count',0):,}회 (+{view_growth:,})")
    except Exception as e:
        print(f"  ⚠️ 스냅샷 저장 실패: {e}")


def update_own_video_stats(video_id: str, stats: dict):
    """추적 시 최신 통계 업데이트"""
    client = get_client()
    if not client:
        return
    try:
        client.table('own_channel_videos').update({
            'view_count': stats.get('view_count', 0),
            'like_count': stats.get('like_count', 0),
            'comment_count': stats.get('comment_count', 0),
            'last_tracked_at': datetime.now().isoformat(),
        }).eq('video_id', video_id).execute()
    except Exception as e:
        print(f"  ⚠️ 통계 업데이트 실패: {e}")


def deactivate_tracking(video_id: str):
    """5주 추적 완료 후 비활성화"""
    client = get_client()
    if not client:
        return
    try:
        client.table('own_channel_videos').update({'tracking_active': False})\
            .eq('video_id', video_id).execute()
        print(f"  ✅ 추적 종료: {video_id[:8]}... (5주 완료)")
    except Exception as e:
        print(f"  ⚠️ 추적 비활성화 실패: {e}")


# ───────────────────────────────────────────────
# 전략 스코어링
# ───────────────────────────────────────────────

def save_video_scores(scores: list) -> int:
    """전략 스코어 저장 (video_id + score_version 기준 upsert)"""
    client = get_client()
    if not client:
        return 0

    today = datetime.now().date().isoformat()
    rows = []
    for s in scores:
        rows.append({
            'video_id':             s.get('video_id', ''),
            'scored_at':            today,
            'score_version':        s.get('score_version', config.SCORE_VERSION),
            'source_table':         s.get('source_table', ''),
            'hangoeun_fit_score':   s.get('hangoeun_fit_score'),
            'execution_score':      s.get('execution_score'),
            'repeatability_score':  s.get('repeatability_score'),
            'novelty_score':        s.get('novelty_score'),
            'risk_score':           s.get('risk_score'),
            'ppl_potential_score':  s.get('ppl_potential_score'),
            'trend_lifespan_score': s.get('trend_lifespan_score'),
            'upload_delay_risk':    s.get('upload_delay_risk'),
            'evergreen_score':      s.get('evergreen_score'),
            'content_lifespan_type': s.get('content_lifespan_type'),
            'priority_score':       s.get('priority_score'),
            'recommended_action':   s.get('recommended_action'),
            'strategy_reason':      s.get('strategy_reason'),
            'rule_trace':           s.get('rule_trace'),
        })

    if not rows:
        return 0

    try:
        client.table('video_scores').upsert(
            rows, on_conflict='video_id,score_version'
        ).execute()
        print(f"  💾 Supabase: video_scores {len(rows)}개 저장")
        return len(rows)
    except Exception as e:
        print(f"  ⚠️ video_scores 저장 실패: {e}")
        return 0


def get_score_backfill_rows() -> tuple:
    """
    스코어 백필용 데이터 조회
    Returns: (initial_rows, daily_rows)
      각각 [(video_dict, analysis_dict), ...] 형태
    이미 현재 score_version으로 점수화된 video_id는 제외
    """
    client = get_client()
    if not client:
        return [], []

    try:
        # 이미 스코어링 완료된 video_id 집합
        scored = client.table('video_scores').select('video_id')\
            .eq('score_version', config.SCORE_VERSION).execute()
        scored_ids = {r['video_id'] for r in scored.data}

        # videos 전체 맵 (video_id → dict)
        vids = client.table('videos').select('*').execute()
        videos_map = {v['video_id']: v for v in vids.data}

        # initial_analysis
        ia = client.table('initial_analysis').select('*').execute()
        initial_rows = [
            (videos_map.get(r.get('video_id', ''), {}), r)
            for r in ia.data
            if r.get('video_id') not in scored_ids
        ]

        # daily_analysis (video_id 중복 제거 — 최신 1건만)
        da = client.table('daily_analysis').select('*')\
            .order('analysis_date', desc=True).execute()
        seen = set()
        daily_rows = []
        for r in da.data:
            vid = r.get('video_id', '')
            if vid in scored_ids or vid in seen:
                continue
            seen.add(vid)
            daily_rows.append((videos_map.get(vid, {}), r))

        print(f"  📋 백필 대상: initial {len(initial_rows)}개 / daily {len(daily_rows)}개")
        return initial_rows, daily_rows

    except Exception as e:
        print(f"  ⚠️ 백필 데이터 조회 실패: {e}")
        return [], []


def get_top_priority_videos(limit: int = 5) -> list:
    """priority_score 상위 N개 조회 (videos 조인)"""
    client = get_client()
    if not client:
        return []

    try:
        result = client.table('video_scores').select('*')\
            .eq('score_version', config.SCORE_VERSION)\
            .order('priority_score', desc=True)\
            .limit(limit).execute()

        if not result.data:
            return []

        video_ids = [r['video_id'] for r in result.data]
        vid_res = client.table('videos')\
            .select('video_id,title,channel_title,video_url,view_count,published_at')\
            .in_('video_id', video_ids).execute()
        vid_map = {v['video_id']: v for v in vid_res.data}

        merged = []
        for s in result.data:
            vid = vid_map.get(s['video_id'], {})
            merged.append({**s, **vid})
        return merged

    except Exception as e:
        print(f"  ⚠️ top priority 조회 실패: {e}")
        return []


def get_weekly_trend_summary(days_back: int = 7) -> dict:
    """지난 N일간 트렌드 데이터 종합 (월요일 알림용)"""
    client = get_client()
    if not client:
        return {}
 
    cutoff = (datetime.now() - timedelta(days=days_back)).date().isoformat()
 
    try:
        # 트랙별 TOP 영상 — 많이 가져와서 video_id 기준 중복 제거 후 TOP 10
        track_a_raw = client.table('trend_classified') \
            .select('*') \
            .eq('track', 'A') \
            .gte('analysis_date', cutoff) \
            .order('view_count', desc=True) \
            .limit(100) \
            .execute()

        track_b_raw = client.table('trend_classified') \
            .select('*') \
            .eq('track', 'B') \
            .gte('analysis_date', cutoff) \
            .order('view_count', desc=True) \
            .limit(100) \
            .execute()

        def dedup_by_video_id(rows, top_n=10):
            seen = {}
            for row in rows:
                vid = row.get('video_id')
                if vid not in seen or row.get('view_count', 0) > seen[vid].get('view_count', 0):
                    seen[vid] = row
            return sorted(seen.values(), key=lambda x: x.get('view_count', 0), reverse=True)[:top_n]

        track_a_data = dedup_by_video_id(track_a_raw.data)
        track_b_data = dedup_by_video_id(track_b_raw.data)
 
        # 이번 주 사건 키워드 빈도 합산
        kw_data = client.table('event_keywords') \
            .select('keywords') \
            .gte('analysis_date', cutoff) \
            .execute()
 
        from collections import Counter
        kw_counter = Counter()
        for row in kw_data.data:
            for k in row.get('keywords', []):
                kw_counter[k] += 1
 
        top_keywords = [k for k, _ in kw_counter.most_common(10)]
 
        return {
            'track_a': track_a_data,
            'track_b': track_b_data,
            'event_keywords': top_keywords,
            'period_days': days_back,
        }
    except Exception as e:
        print(f"  ⚠️ 주간 트렌드 조회 실패: {e}")
        return {}


def get_all_tracked_video_ids() -> list:
    """경쟁채널 전체 video_id 목록 (통계 갱신용)"""
    client = get_client()
    if not client:
        return []
    try:
        result = client.table('videos').select('video_id').execute()
        return [r['video_id'] for r in result.data]
    except Exception as e:
        print(f"  ⚠️ video_id 목록 조회 실패: {e}")
        return []


def get_recent_videos_from_db(days_back: int = 7) -> list:
    """최근 N일간 업로드된 경쟁채널 영상 조회 (갱신된 stats 포함)"""
    client = get_client()
    if not client:
        return []
    cutoff = (datetime.now() - timedelta(days=days_back)).isoformat()
    try:
        result = client.table('videos')\
            .select('video_id,channel_title,title,view_count,like_count,comment_count,published_at,video_url')\
            .gte('published_at', cutoff)\
            .order('view_count', desc=True)\
            .execute()
        return result.data
    except Exception as e:
        print(f"  ⚠️ 최근 영상 조회 실패: {e}")
        return []


# ───────────────────────────────────────────────
# video_snapshots: 시계열 스냅샷
# ───────────────────────────────────────────────

def get_last_video_snapshot(video_id: str) -> dict | None:
    """직전 스냅샷 조회 (growth 계산용)"""
    client = get_client()
    if not client:
        return None
    try:
        result = client.table('video_snapshots')\
            .select('view_count,like_count,comment_count,snapshot_date')\
            .eq('video_id', video_id)\
            .order('snapshot_date', desc=True)\
            .limit(1).execute()
        return result.data[0] if result.data else None
    except Exception:
        return None


def save_video_snapshots(snapshots: list) -> int:
    """video_snapshots 일괄 저장 (video_id + snapshot_date upsert)"""
    client = get_client()
    if not client or not snapshots:
        return 0
    try:
        client.table('video_snapshots').upsert(
            snapshots, on_conflict='video_id,snapshot_date'
        ).execute()
        return len(snapshots)
    except Exception as e:
        print(f"  ⚠️ video_snapshots 저장 실패: {e}")
        return 0


def get_all_videos_for_snapshot() -> list:
    """스냅샷 대상 전체 영상 (경쟁채널 + 자체채널)"""
    client = get_client()
    if not client:
        return []
    try:
        comp = client.table('videos')\
            .select('video_id,channel_title,published_at')\
            .execute().data
        for v in comp:
            v['source_type'] = 'competitor'

        own = client.table('own_channel_videos')\
            .select('video_id,published_at')\
            .execute().data
        for v in own:
            v['source_type'] = 'own'
            v['channel_title'] = config.OWN_CHANNEL

        return comp + own
    except Exception as e:
        print(f"  ⚠️ 스냅샷 대상 조회 실패: {e}")
        return []


# ───────────────────────────────────────────────
# thumbnail_analysis: 썸네일 Vision 분석
# ───────────────────────────────────────────────

def save_thumbnail_analysis(video_id: str, analysis: dict) -> bool:
    """썸네일 분석 결과 저장"""
    client = get_client()
    if not client:
        return False
    try:
        client.table('thumbnail_analysis').upsert({
            'video_id': video_id,
            'analyzed_at': datetime.now().isoformat(),
            'face_count': analysis.get('face_count'),
            'main_emotion': analysis.get('main_emotion', ''),
            'food_present': analysis.get('food_present', False),
            'couple_present': analysis.get('couple_present', False),
            'family_present': analysis.get('family_present', False),
            'home_visible': analysis.get('home_visible', False),
            'luxury_signal': analysis.get('luxury_signal', False),
            'text_overlay': analysis.get('text_overlay', False),
            'thumbnail_style': analysis.get('thumbnail_style', ''),
            'camera_distance': analysis.get('camera_distance', ''),
            'emotion_intensity': analysis.get('emotion_intensity'),
            'ctr_prediction': analysis.get('ctr_prediction', ''),
            'ctr_reason': analysis.get('ctr_reason', ''),
            'model_used': analysis.get('model_used', ''),
            'raw_analysis': analysis,
        }).execute()
        return True
    except Exception as e:
        print(f"  ⚠️ thumbnail_analysis 저장 실패 ({video_id[:8]}...): {e}")
        return False


def get_videos_for_thumbnail_analysis(mode: str = 'full_initial', limit: int = 500) -> list:
    """
    썸네일 분석 대상 영상 조회 (미분석만)
    mode='full_initial' : initial_analysis 전체 영상 (기본 — 전수 backfill용)
    mode='priority'     : HIT + IRREGULAR + priority_score≥70 (일상 운영용)
    """
    client = get_client()
    if not client:
        return []
    try:
        # 이미 분석된 video_id
        done = {r['video_id'] for r in
                client.table('thumbnail_analysis').select('video_id').execute().data}

        if mode == 'full_initial':
            # initial_analysis 전체 video_id
            rows = client.table('initial_analysis').select('video_id').execute().data
            candidates = {r['video_id'] for r in rows}
        else:
            candidates = set()
            # HIT 영상
            hit = client.table('initial_analysis').select('video_id')\
                .eq('is_hit', True).execute().data
            candidates.update(r['video_id'] for r in hit)
            # IRREGULAR
            irreg = client.table('trend_classified').select('video_id')\
                .eq('track', 'IRREGULAR').execute().data
            candidates.update(r['video_id'] for r in irreg)
            # priority 상위
            top = client.table('video_scores').select('video_id')\
                .gte('priority_score', 70)\
                .order('priority_score', desc=True).limit(100).execute().data
            candidates.update(r['video_id'] for r in top)

        targets = list(candidates - done)[:limit]
        if not targets:
            return []

        # 50개씩 나눠서 videos 테이블 조회 (PostgREST in 절 한도 대응)
        result = []
        for i in range(0, len(targets), 50):
            chunk = targets[i:i+50]
            rows = client.table('videos')\
                .select('video_id,title,channel_title,thumbnail_url')\
                .in_('video_id', chunk).execute().data
            result.extend(rows)
        return result
    except Exception as e:
        print(f"  ⚠️ 썸네일 분석 대상 조회 실패: {e}")
        return []


# ───────────────────────────────────────────────
# weekly_market_state: 시장 변화 분석
# ───────────────────────────────────────────────

def save_weekly_market_state(state: dict) -> bool:
    """주간 시장 분석 저장 (report_date upsert)"""
    client = get_client()
    if not client:
        return False
    today = datetime.now().date().isoformat()
    try:
        client.table('weekly_market_state').upsert({
            'report_date': today,
            'dominant_emotion': state.get('dominant_emotion', ''),
            'rising_topics': state.get('rising_topics', []),
            'declining_topics': state.get('declining_topics', []),
            'oversaturated_formats': state.get('oversaturated_formats', []),
            'emerging_formats': state.get('emerging_formats', []),
            'viewer_fatigue_signals': state.get('viewer_fatigue_signals', []),
            'comfort_content_score': state.get('comfort_content_score', 0),
            'hangoeun_opportunity_score': state.get('hangoeun_opportunity_score', 0),
            'hangoeun_recommended_topics': state.get('hangoeun_recommended_topics', []),
            'competitive_gap': state.get('competitive_gap', []),
            'market_summary': state.get('market_summary', ''),
            'raw_analysis': state,
        }, on_conflict='report_date').execute()
        print(f"  💾 Supabase: weekly_market_state 저장 ({today})")
        return True
    except Exception as e:
        print(f"  ⚠️ weekly_market_state 저장 실패: {e}")
        return False


def get_snapshot_growth_summary(days_back: int = 7) -> dict:
    """
    지난 N일 스냅샷에서 성장 패턴 집계.
    반환: {pattern: count, avg_growth_rate, top_growing: [...]}
    """
    client = get_client()
    if not client:
        return {}
    cutoff = (datetime.now() - timedelta(days=days_back)).date().isoformat()
    try:
        rows = client.table('video_snapshots')\
            .select('video_id,channel_title,view_count,view_growth,view_growth_rate,growth_pattern,source_type')\
            .gte('snapshot_date', cutoff)\
            .order('view_growth', desc=True)\
            .execute().data

        from collections import Counter
        pattern_counter = Counter(r.get('growth_pattern') for r in rows if r.get('growth_pattern'))
        avg_growth = sum(r.get('view_growth_rate', 0) or 0 for r in rows) / max(len(rows), 1)

        top_growing = sorted(
            [r for r in rows if r.get('view_growth', 0) > 0],
            key=lambda x: x.get('view_growth', 0), reverse=True
        )[:20]

        return {
            'pattern_counts': dict(pattern_counter),
            'avg_growth_rate': round(avg_growth, 4),
            'total_snapshots': len(rows),
            'top_growing': top_growing,
        }
    except Exception as e:
        print(f"  ⚠️ 스냅샷 성장 집계 실패: {e}")
        return {}


def get_emotion_summary(days_back: int = 7) -> dict:
    """
    지난 N일 댓글 분석에서 감정 온도 집계.
    initial_analysis + daily_analysis의 deepseek_raw.emotion_temperature 평균
    """
    client = get_client()
    if not client:
        return {}
    cutoff = (datetime.now() - timedelta(days=days_back)).date().isoformat()

    emotions = ['comfort', 'healing', 'nostalgia', 'trust', 'intimacy',
                'aspiration', 'envy', 'fatigue', 'cringe']
    totals = {e: 0 for e in emotions}
    count = 0

    try:
        for table in ['daily_analysis', 'initial_analysis']:
            rows = client.table(table)\
                .select('deepseek_raw')\
                .gte('analysis_date', cutoff)\
                .execute().data
            for r in rows:
                raw = r.get('deepseek_raw') or {}
                et = raw.get('emotion_temperature') or {}
                if et:
                    for e in emotions:
                        totals[e] += et.get(e, 0) or 0
                    count += 1

        if count == 0:
            return {}
        return {e: round(totals[e] / count, 1) for e in emotions}
    except Exception as e:
        print(f"  ⚠️ 감정 온도 집계 실패: {e}")
        return {}


def get_unanalyzed_videos(limit: int = None) -> list:
    """
    videos 테이블에 있지만 initial_analysis가 없는 영상 목록 반환.
    full-backfill 대상 조회용.
    """
    client = get_client()
    if not client:
        return []
    try:
        all_vids = client.table('videos')\
            .select('video_id,channel_title,title,view_count,like_count,comment_count,published_at,thumbnail_url,video_url,duration,tags,description')\
            .execute().data
        analyzed = {r['video_id'] for r in
                    client.table('initial_analysis').select('video_id').execute().data}
        result = [v for v in all_vids if v['video_id'] not in analyzed]
        if limit:
            result = result[:limit]
        print(f"  📋 미분석 영상: {len(result)}개 (전체 {len(all_vids)}개 중)")
        return result
    except Exception as e:
        print(f"  ⚠️ 미분석 영상 조회 실패: {e}")
        return []


def get_videos_needing_comment_reanalysis(limit: int = None) -> list:
    """
    emotion_temperature가 없는 initial_analysis 영상 목록 반환.
    [(video_id, title, comment_count), ...] 형태의 dict 리스트
    """
    client = get_client()
    if not client:
        return []
    try:
        q = client.table('initial_analysis')\
            .select('video_id, deepseek_raw, videos(title, comment_count)')
        if limit:
            q = q.limit(limit)
        result = q.execute()

        rows = []
        for r in result.data:
            ds_raw = r.get('deepseek_raw') or {}
            if not ds_raw.get('emotion_temperature'):
                video_meta = r.get('videos') or {}
                rows.append({
                    'video_id': r['video_id'],
                    'title': video_meta.get('title', ''),
                    'comment_count': video_meta.get('comment_count', 0),
                })
        return rows
    except Exception as e:
        print(f"  ⚠️ comment reanalysis 대상 조회 실패: {e}")
        return []


def update_comment_analysis(video_id: str, comments_result: dict) -> bool:
    """initial_analysis의 deepseek_raw를 새 댓글 분석 결과로 갱신 (기존 필드 보존)"""
    client = get_client()
    if not client:
        return False
    try:
        res = client.table('initial_analysis').select('deepseek_raw')\
            .eq('video_id', video_id).limit(1).execute()
        existing = (res.data[0].get('deepseek_raw') or {}) if res.data else {}
        merged = {**existing, **comments_result, '_version': config.ANALYSIS_VERSION}
        client.table('initial_analysis').update({'deepseek_raw': merged})\
            .eq('video_id', video_id).execute()
        return True
    except Exception as e:
        print(f"  ⚠️ update_comment_analysis 실패 ({video_id[:8]}...): {e}")
        return False


def update_growth_patterns(video_ids: list = None) -> int:
    """
    video_snapshots에서 D1/D7/D30 스냅샷을 비교해 growth_pattern 업데이트.
    video_ids=None이면 전체 대상.
    """
    client = get_client()
    if not client:
        return 0

    import deepseek_processor as ds

    try:
        # 스냅샷이 2개 이상인 video_id 목록
        q = client.table('video_snapshots').select('video_id').execute()
        all_ids = list({r['video_id'] for r in q.data})
        if video_ids:
            all_ids = [v for v in all_ids if v in video_ids]

        updated = 0
        for vid in all_ids:
            snaps = client.table('video_snapshots')\
                .select('snapshot_date,view_count,days_since_publish')\
                .eq('video_id', vid)\
                .order('snapshot_date').execute().data

            if len(snaps) < 2:
                continue

            # days_since_publish 기준으로 D1/D7/D30 근사값 추출
            def closest(target_days):
                valid = [s for s in snaps if s.get('days_since_publish') is not None]
                if not valid:
                    return None
                return min(valid, key=lambda s: abs((s.get('days_since_publish') or 0) - target_days))

            d1 = closest(1)
            d7 = closest(7)
            d30 = closest(30)

            d1_v = d1['view_count'] if d1 else 0
            d7_v = d7['view_count'] if d7 else 0
            d30_v = d30['view_count'] if d30 else 0

            pattern = ds.classify_growth_pattern(d1_v, d7_v, d30_v)
            if pattern == 'UNKNOWN':
                continue

            d7_ratio = round(d7_v / d1_v, 4) if d1_v > 0 and d7_v > 0 else None
            d30_ratio = round(d30_v / d7_v, 4) if d7_v > 0 and d30_v > 0 else None

            client.table('video_snapshots')\
                .update({
                    'growth_pattern': pattern,
                    'd7_view_ratio': d7_ratio,
                    'd30_view_ratio': d30_ratio,
                })\
                .eq('video_id', vid).execute()
            updated += 1

        return updated
    except Exception as e:
        print(f"  ⚠️ growth_pattern 업데이트 실패: {e}")
        return 0
