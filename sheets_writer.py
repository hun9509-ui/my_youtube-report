"""
구글 시트에 데이터를 저장하는 모듈
"""
import gspread
from google.oauth2.service_account import Credentials
import json
from datetime import datetime
import config

SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/drive'
]


def get_sheet_client():
    """구글 시트 클라이언트 생성"""
    creds_dict = json.loads(config.GOOGLE_SERVICE_ACCOUNT_JSON)
    creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
    return gspread.authorize(creds)


def get_or_create_worksheet(spreadsheet, sheet_name, headers):
    """시트 가져오거나 새로 생성"""
    try:
        ws = spreadsheet.worksheet(sheet_name)
    except gspread.WorksheetNotFound:
        ws = spreadsheet.add_worksheet(title=sheet_name, rows=1000, cols=len(headers))
        ws.append_row(headers)
    return ws


def save_videos(videos):
    """영상 마스터 데이터 저장"""
    client = get_sheet_client()
    sheet = client.open_by_key(config.GOOGLE_SHEET_ID)
    
    headers = [
        '수집일', '채널명', '영상ID', '제목', '업로드일', '조회수', '좋아요', 
        '댓글수', '영상길이', '태그', '썸네일URL', '영상URL', '설명'
    ]
    ws = get_or_create_worksheet(sheet, config.SHEET_VIDEOS, headers)
    
    # 기존 영상ID 조회 (중복 방지)
    existing_ids = set()
    try:
        all_values = ws.get_all_values()
        if len(all_values) > 1:
            existing_ids = {row[2] for row in all_values[1:] if len(row) > 2}
    except Exception:
        pass
    
    # 새 영상만 저장
    new_rows = []
    today = datetime.now().strftime('%Y-%m-%d')
    
    for v in videos:
        if v['video_id'] in existing_ids:
            continue
        new_rows.append([
            today,
            v.get('channel_title', ''),
            v['video_id'],
            v.get('title', ''),
            v.get('published_at', ''),
            v.get('view_count', 0),
            v.get('like_count', 0),
            v.get('comment_count', 0),
            v.get('duration', ''),
            v.get('tags', ''),
            v.get('thumbnail_url', ''),
            v.get('video_url', ''),
            v.get('description', '')[:200]
        ])
    
    if new_rows:
        ws.append_rows(new_rows, value_input_option='USER_ENTERED')
    
    return len(new_rows)


def save_analysis(analyses):
    """AI 분석 결과 저장"""
    client = get_sheet_client()
    sheet = client.open_by_key(config.GOOGLE_SHEET_ID)
    
    headers = [
        '분석일', '영상ID', '주제', '카테고리', '제목패턴', '핵심키워드',
        '후킹전략', 'PPL여부', 'PPL근거', '타겟층', '성과수준', '성공요인',
        '한고은적용가능성', '적용근거',
        '여론_긍정', '여론_부정', '여론_핵심키워드', '시청자페르소나',
        '칭찬포인트', '불만사항', '댓글요약'
    ]
    ws = get_or_create_worksheet(sheet, config.SHEET_ANALYSIS, headers)
    
    today = datetime.now().strftime('%Y-%m-%d')
    new_rows = []
    
    for a in analyses:
        gemini = a.get('gemini', {})
        comments = a.get('comments', {})
        sentiment = comments.get('sentiment', {}) if isinstance(comments, dict) else {}
        
        new_rows.append([
            today,
            a.get('video_id', ''),
            gemini.get('topic', ''),
            gemini.get('category', ''),
            gemini.get('title_pattern', ''),
            ', '.join(gemini.get('title_keywords', [])) if isinstance(gemini.get('title_keywords'), list) else '',
            gemini.get('hook_strategy', ''),
            str(gemini.get('ppl_likely', '')),
            gemini.get('ppl_signals', ''),
            gemini.get('target_audience', ''),
            gemini.get('performance_level', ''),
            gemini.get('success_factors', ''),
            gemini.get('applicability', ''),
            gemini.get('applicability_reason', ''),
            sentiment.get('positive', ''),
            sentiment.get('negative', ''),
            ', '.join(comments.get('main_keywords', [])) if isinstance(comments.get('main_keywords'), list) else '',
            comments.get('viewer_persona', '') if isinstance(comments, dict) else '',
            ', '.join(comments.get('praise_points', [])) if isinstance(comments.get('praise_points'), list) else '',
            ', '.join(comments.get('complaints', [])) if isinstance(comments.get('complaints'), list) else '',
            comments.get('summary', '') if isinstance(comments, dict) else ''
        ])
    
    if new_rows:
        ws.append_rows(new_rows, value_input_option='USER_ENTERED')
    
    return len(new_rows)


def save_channel_insights(channel_insights):
    """채널별 성공 공식 저장"""
    client = get_sheet_client()
    sheet = client.open_by_key(config.GOOGLE_SHEET_ID)
    
    headers = [
        '분석일', '채널명', '성공공식', '잘되는주제', '성공제목패턴',
        '실패패턴', '차별점', '한고은적용포인트'
    ]
    ws = get_or_create_worksheet(sheet, config.SHEET_CHANNEL_INSIGHTS, headers)
    
    today = datetime.now().strftime('%Y-%m-%d')
    new_rows = []
    
    for channel, insights in channel_insights.items():
        if 'error' in insights:
            continue
        new_rows.append([
            today,
            channel,
            insights.get('success_formula', ''),
            ', '.join(insights.get('winning_topics', [])) if isinstance(insights.get('winning_topics'), list) else '',
            ', '.join(insights.get('winning_title_patterns', [])) if isinstance(insights.get('winning_title_patterns'), list) else '',
            ', '.join(insights.get('failure_patterns', [])) if isinstance(insights.get('failure_patterns'), list) else '',
            insights.get('differentiator', ''),
            ', '.join(insights.get('lessons_for_hangoeun', [])) if isinstance(insights.get('lessons_for_hangoeun'), list) else ''
        ])
    
    if new_rows:
        ws.append_rows(new_rows, value_input_option='USER_ENTERED')
    
    return len(new_rows)


def save_daily_trends(trends_data):
    """일일 트렌드 저장"""
    client = get_sheet_client()
    sheet = client.open_by_key(config.GOOGLE_SHEET_ID)
    
    headers = [
        '날짜', '핵심트렌드', '키워드', '떠오르는주제', '한고은적용아이디어', '주의영상'
    ]
    ws = get_or_create_worksheet(sheet, config.SHEET_TRENDS, headers)
    
    today = datetime.now().strftime('%Y-%m-%d')
    
    new_row = [
        today,
        trends_data.get('headline', ''),
        json.dumps(trends_data.get('top_5_keywords', []), ensure_ascii=False),
        json.dumps(trends_data.get('rising_topics', []), ensure_ascii=False),
        trends_data.get('hangoeun_action', ''),
        trends_data.get('watch_out', '')
    ]
    
    ws.append_row(new_row, value_input_option='USER_ENTERED')
    return 1


def save_weekly_report(report):
    """주간 리포트 저장"""
    client = get_sheet_client()
    sheet = client.open_by_key(config.GOOGLE_SHEET_ID)
    
    headers = [
        '날짜', '주간요약', '잘나가는채널', '떠오르는주제', '포맷트렌드',
        'PPL관찰', '한고은액션', '다음영상방향성'
    ]
    ws = get_or_create_worksheet(sheet, config.SHEET_WEEKLY, headers)
    
    today = datetime.now().strftime('%Y-%m-%d')
    
    new_row = [
        today,
        report.get('week_summary', ''),
        json.dumps(report.get('top_performing_channels', []), ensure_ascii=False),
        json.dumps(report.get('rising_topics', []), ensure_ascii=False),
        json.dumps(report.get('format_trends', []), ensure_ascii=False),
        report.get('ppl_observations', ''),
        json.dumps(report.get('actionable_insights_for_hangoeun', []), ensure_ascii=False),
        json.dumps(report.get('next_video_suggestions', []), ensure_ascii=False)
    ]
    
    ws.append_row(new_row, value_input_option='USER_ENTERED')
    return 1


def get_videos_from_sheet(days_back=7):
    """시트에서 최근 N일 영상 조회 (주간 리포트용)"""
    client = get_sheet_client()
    sheet = client.open_by_key(config.GOOGLE_SHEET_ID)
    
    try:
        ws = sheet.worksheet(config.SHEET_VIDEOS)
        all_values = ws.get_all_records()
        
        from datetime import timedelta
        cutoff = (datetime.now() - timedelta(days=days_back)).strftime('%Y-%m-%d')
        
        return [v for v in all_values if v.get('수집일', '') >= cutoff]
    except Exception as e:
        print(f"시트 조회 실패: {e}")
        return []
