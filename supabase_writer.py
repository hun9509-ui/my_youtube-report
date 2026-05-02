"""
Supabase 이중 저장 모듈
시트와 동일한 데이터를 PostgreSQL DB에도 저장
"""
from supabase import create_client, Client
from datetime import datetime
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
        })
    
    try:
        result = client.table('initial_analysis').insert(rows).execute()
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
