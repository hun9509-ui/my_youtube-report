"""
구글 시트에 데이터를 저장하는 모듈 (Apps Script 웹훅 방식)
서비스 계정 JSON 불필요 - 조직 정책 무관
"""
import requests
import json
from datetime import datetime, timedelta
import config


def call_webhook(action, data):
    """Apps Script 웹훅 호출"""
    if not config.GOOGLE_WEBHOOK_URL:
        print("⚠️ GOOGLE_WEBHOOK_URL이 설정되지 않음")
        return None
    
    payload = {"action": action, "data": data}
    
    try:
        response = requests.post(config.GOOGLE_WEBHOOK_URL, json=payload, timeout=120)
        if response.status_code == 200:
            return response.json()
        else:
            print(f"웹훅 오류 {response.status_code}: {response.text[:200]}")
            return None
    except Exception as e:
        print(f"웹훅 호출 실패: {e}")
        return None


def save_videos(videos):
    """영상 마스터 데이터 저장"""
    today = datetime.now().strftime('%Y-%m-%d')
    rows = []
    for v in videos:
        rows.append([
            today, v.get('channel_title', ''), v.get('video_id', ''),
            v.get('title', ''), v.get('published_at', ''),
            v.get('view_count', 0), v.get('like_count', 0), v.get('comment_count', 0),
            v.get('duration', ''), v.get('tags', ''),
            v.get('thumbnail_url', ''), v.get('video_url', ''),
            v.get('description', '')[:200]
        ])
    
    result = call_webhook('save_videos', {
        'sheet_name': config.SHEET_VIDEOS,
        'rows': rows,
        'headers': ['수집일', '채널명', '영상ID', '제목', '업로드일', '조회수', '좋아요',
                    '댓글수', '영상길이', '태그', '썸네일URL', '영상URL', '설명'],
        'dedupe_column': 2
    })
    return result.get('saved_count', 0) if result else 0


def save_analysis(analyses):
    """AI 분석 결과 저장"""
    today = datetime.now().strftime('%Y-%m-%d')
    rows = []
    for a in analyses:
        gemini = a.get('gemini', {})
        comments = a.get('comments', {})
        sentiment = comments.get('sentiment', {}) if isinstance(comments, dict) else {}
        
        rows.append([
            today, a.get('video_id', ''),
            gemini.get('topic', ''), gemini.get('category', ''),
            gemini.get('title_pattern', ''),
            ', '.join(gemini.get('title_keywords', [])) if isinstance(gemini.get('title_keywords'), list) else '',
            gemini.get('hook_strategy', ''),
            str(gemini.get('ppl_likely', '')), gemini.get('ppl_signals', ''),
            gemini.get('target_audience', ''), gemini.get('performance_level', ''),
            gemini.get('success_factors', ''),
            gemini.get('applicability', ''), gemini.get('applicability_reason', ''),
            sentiment.get('positive', ''), sentiment.get('negative', ''),
            ', '.join(comments.get('main_keywords', [])) if isinstance(comments.get('main_keywords'), list) else '',
            comments.get('viewer_persona', '') if isinstance(comments, dict) else '',
            ', '.join(comments.get('praise_points', [])) if isinstance(comments.get('praise_points'), list) else '',
            ', '.join(comments.get('complaints', [])) if isinstance(comments.get('complaints'), list) else '',
            comments.get('summary', '') if isinstance(comments, dict) else ''
        ])
    
    result = call_webhook('save_analysis', {
        'sheet_name': config.SHEET_ANALYSIS,
        'rows': rows,
        'headers': ['분석일', '영상ID', '주제', '카테고리', '제목패턴', '핵심키워드',
                    '후킹전략', 'PPL여부', 'PPL근거', '타겟층', '성과수준', '성공요인',
                    '한고은적용가능성', '적용근거',
                    '여론_긍정', '여론_부정', '여론_핵심키워드', '시청자페르소나',
                    '칭찬포인트', '불만사항', '댓글요약']
    })
    return result.get('saved_count', 0) if result else 0


def save_channel_insights(channel_insights):
    """채널별 성공 공식 저장"""
    today = datetime.now().strftime('%Y-%m-%d')
    rows = []
    for channel, insights in channel_insights.items():
        if 'error' in insights:
            continue
        rows.append([
            today, channel, insights.get('success_formula', ''),
            ', '.join(insights.get('winning_topics', [])) if isinstance(insights.get('winning_topics'), list) else '',
            ', '.join(insights.get('winning_title_patterns', [])) if isinstance(insights.get('winning_title_patterns'), list) else '',
            ', '.join(insights.get('failure_patterns', [])) if isinstance(insights.get('failure_patterns'), list) else '',
            insights.get('differentiator', ''),
            ', '.join(insights.get('lessons_for_hangoeun', [])) if isinstance(insights.get('lessons_for_hangoeun'), list) else ''
        ])
    
    result = call_webhook('save_insights', {
        'sheet_name': config.SHEET_CHANNEL_INSIGHTS,
        'rows': rows,
        'headers': ['분석일', '채널명', '성공공식', '잘되는주제', '성공제목패턴',
                    '실패패턴', '차별점', '한고은적용포인트']
    })
    return result.get('saved_count', 0) if result else 0


def save_daily_trends(trends_data):
    """일일 트렌드 저장"""
    today = datetime.now().strftime('%Y-%m-%d')
    row = [
        today, trends_data.get('headline', ''),
        json.dumps(trends_data.get('top_5_keywords', []), ensure_ascii=False),
        json.dumps(trends_data.get('rising_topics', []), ensure_ascii=False),
        trends_data.get('hangoeun_action', ''), trends_data.get('watch_out', '')
    ]
    result = call_webhook('save_trends', {
        'sheet_name': config.SHEET_TRENDS,
        'rows': [row],
        'headers': ['날짜', '핵심트렌드', '키워드', '떠오르는주제', '한고은적용아이디어', '주의영상']
    })
    return 1 if result else 0


def save_weekly_report(report):
    """주간 리포트 저장"""
    today = datetime.now().strftime('%Y-%m-%d')
    row = [
        today, report.get('week_summary', ''),
        json.dumps(report.get('top_performing_channels', []), ensure_ascii=False),
        json.dumps(report.get('rising_topics', []), ensure_ascii=False),
        json.dumps(report.get('format_trends', []), ensure_ascii=False),
        report.get('ppl_observations', ''),
        json.dumps(report.get('actionable_insights_for_hangoeun', []), ensure_ascii=False),
        json.dumps(report.get('next_video_suggestions', []), ensure_ascii=False)
    ]
    result = call_webhook('save_weekly', {
        'sheet_name': config.SHEET_WEEKLY,
        'rows': [row],
        'headers': ['날짜', '주간요약', '잘나가는채널', '떠오르는주제', '포맷트렌드',
                    'PPL관찰', '한고은액션', '다음영상방향성']
    })
    return 1 if result else 0


def get_videos_from_sheet(days_back=7):
    """시트에서 최근 N일 영상 조회 (주간 리포트용)"""
    cutoff = (datetime.now() - timedelta(days=days_back)).strftime('%Y-%m-%d')
    result = call_webhook('get_recent_videos', {
        'sheet_name': config.SHEET_VIDEOS,
        'cutoff_date': cutoff
    })
    if result and 'videos' in result:
        return result['videos']
    return []
