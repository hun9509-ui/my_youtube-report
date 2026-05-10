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


def get_top_priority_videos(limit: int = 5, days_back: int = 7) -> list:
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
        # 트랙별 TOP 영상 (조회수 순)
        track_a = client.table('trend_classified') \
            .select('*') \
            .eq('track', 'A') \
            .gte('analysis_date', cutoff) \
            .order('view_count', desc=True) \
            .limit(10) \
            .execute()
 
        track_b = client.table('trend_classified') \
            .select('*') \
            .eq('track', 'B') \
            .gte('analysis_date', cutoff) \
            .order('view_count', desc=True) \
            .limit(10) \
            .execute()
 
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
            'track_a': track_a.data,
            'track_b': track_b.data,
            'event_keywords': top_keywords,
            'period_days': days_back,
        }
    except Exception as e:
        print(f"  ⚠️ 주간 트렌드 조회 실패: {e}")
        return {}
