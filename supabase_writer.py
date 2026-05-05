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
            'gemini_raw': gemini,
            'deepseek_raw': comments,
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
            'gemini_raw': gemini,
            'deepseek_raw': comments,
        })
    
    try:
        result = client.table('daily_analysis').insert(rows).execute()
        print(f"  💾 Supabase: daily_analysis {len(rows)}개 저장")
        return len(rows)
    except Exception as e:
        print(f"  ⚠️ Supabase daily_analysis 저장 실패: {e}")
        return 0


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
