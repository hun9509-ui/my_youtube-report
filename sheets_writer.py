"""
구글 시트 저장 모듈 (gspread 직접 쓰기)
"""
import os
import json
import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime, timedelta
import config


_sh = None


def _get_sheet():
    """스프레드시트 객체 (세션 내 재사용)"""
    global _sh
    if _sh is not None:
        return _sh
    creds_path = os.environ.get('GOOGLE_APPLICATION_CREDENTIALS')
    if not creds_path:
        print("  ⚠️ GOOGLE_APPLICATION_CREDENTIALS 미설정 — 시트 저장 불가")
        return None
    try:
        creds = Credentials.from_service_account_file(creds_path, scopes=[
            'https://www.googleapis.com/auth/spreadsheets',
            'https://www.googleapis.com/auth/drive',
        ])
        _sh = gspread.authorize(creds).open_by_key(config.GOOGLE_SHEET_ID)
        return _sh
    except Exception as e:
        print(f"  ⚠️ 스프레드시트 초기화 실패: {e}")
        return None


def _write_to_sheet(sheet_name: str, headers: list, rows: list, dedupe_col: int = None) -> int:
    """탭에 행 추가. dedupe_col 지정 시 해당 컬럼 기준 중복 제거"""
    if not rows:
        return 0
    sh = _get_sheet()
    if not sh:
        return 0
    try:
        try:
            ws = sh.worksheet(sheet_name)
        except gspread.exceptions.WorksheetNotFound:
            ws = sh.add_worksheet(title=sheet_name, rows=10000, cols=max(len(headers), 30))
            ws.append_row(headers, value_input_option='USER_ENTERED')
            existing = [headers]
        else:
            existing = ws.get_all_values()
            if not existing:
                ws.append_row(headers, value_input_option='USER_ENTERED')
                existing = [headers]

        if dedupe_col is not None:
            seen = {
                row[dedupe_col]
                for row in existing[1:]
                if len(row) > dedupe_col and row[dedupe_col]
            }
            new_rows = [r for r in rows if len(r) > dedupe_col and str(r[dedupe_col]) not in seen]
        else:
            new_rows = rows

        if not new_rows:
            print(f"  ⏩ {sheet_name}: 신규 없음")
            return 0

        ws.append_rows(new_rows, value_input_option='USER_ENTERED')
        print(f"  📊 {sheet_name}: {len(new_rows)}개 저장")
        return len(new_rows)
    except Exception as e:
        print(f"  ⚠️ {sheet_name} 저장 실패: {e}")
        return 0


def save_videos(videos):
    today = datetime.now().strftime('%Y-%m-%d')
    rows = [[
        today, v.get('channel_title', ''), v.get('video_id', ''),
        v.get('title', ''), v.get('published_at', ''),
        v.get('view_count', 0), v.get('like_count', 0), v.get('comment_count', 0),
        v.get('duration', ''), str(v.get('tags', '')),
        v.get('thumbnail_url', ''), v.get('video_url', ''),
        v.get('description', '')[:200],
    ] for v in videos]
    return _write_to_sheet(
        config.SHEET_VIDEOS,
        ['수집일', '채널명', '영상ID', '제목', '업로드일', '조회수', '좋아요',
         '댓글수', '영상길이', '태그', '썸네일URL', '영상URL', '설명'],
        rows, dedupe_col=2,
    )


def _build_analysis_row(a, extra_first_cols):
    gemini = a.get('gemini', {}) or {}
    comments = a.get('comments', {}) or {}
    sentiment = comments.get('sentiment', {}) if isinstance(comments, dict) else {}
    return extra_first_cols + [
        a.get('video_id', ''),
        gemini.get('topic', ''),
        gemini.get('category', ''),
        gemini.get('title_pattern', ''),
        ', '.join(gemini.get('title_keywords', [])) if isinstance(gemini.get('title_keywords'), list) else '',
        gemini.get('title_emotion_tone', ''),
        gemini.get('hook_strategy', ''),
        gemini.get('content_structure', ''),
        str(gemini.get('ppl_likely', '')),
        gemini.get('ppl_signals', ''),
        gemini.get('target_audience', ''),
        gemini.get('performance_level', ''),
        gemini.get('success_factors', ''),
        gemini.get('unique_differentiator', ''),
        gemini.get('applicability', ''),
        gemini.get('applicability_reason', ''),
        gemini.get('hangoeun_scenario', ''),
        sentiment.get('positive', ''),
        sentiment.get('negative', ''),
        ', '.join(comments.get('main_keywords', [])) if isinstance(comments.get('main_keywords'), list) else '',
        comments.get('viewer_persona', '') if isinstance(comments, dict) else '',
        ', '.join(comments.get('praise_points', [])) if isinstance(comments.get('praise_points'), list) else '',
        ', '.join(comments.get('complaints', [])) if isinstance(comments.get('complaints'), list) else '',
        comments.get('this_video_special', '') if isinstance(comments, dict) else '',
        comments.get('revisit_intent', '') if isinstance(comments, dict) else '',
        comments.get('viral_signals', '') if isinstance(comments, dict) else '',
        comments.get('summary', '') if isinstance(comments, dict) else '',
    ]


_ANALYSIS_HEADERS_BASE = [
    '영상ID', '주제', '카테고리', '제목패턴', '핵심키워드', '제목감정톤',
    '후킹전략', '콘텐츠구조', 'PPL여부', 'PPL근거', '타겟층', '성과수준', '성공요인',
    '차별화포인트', '한고은적용가능성', '적용근거', '한고은시나리오',
    '여론_긍정', '여론_부정', '여론_핵심키워드', '시청자페르소나',
    '칭찬포인트', '불만사항', '이영상만의이유', '재방문의사%', '바이럴시그널', '댓글요약',
]


def save_initial_analysis(analyses, batch_num):
    today = datetime.now().strftime('%Y-%m-%d')
    rows = []
    for a in analyses:
        row = _build_analysis_row(a, [today, batch_num])
        row.append('대박' if a.get('is_hit') else '')
        rows.append(row)
    return _write_to_sheet(
        config.SHEET_INITIAL,
        ['분석일', '배치번호'] + _ANALYSIS_HEADERS_BASE + ['대박여부'],
        rows,
    )


def save_daily_analysis(analyses):
    today = datetime.now().strftime('%Y-%m-%d')
    rows = [_build_analysis_row(a, [today]) for a in analyses]
    return _write_to_sheet(
        config.SHEET_DAILY,
        ['분석일'] + _ANALYSIS_HEADERS_BASE,
        rows,
    )


def save_analysis(analyses):
    return save_daily_analysis(analyses)


def save_channel_insights(channel_insights):
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
            ', '.join(insights.get('lessons_for_hangoeun', [])) if isinstance(insights.get('lessons_for_hangoeun'), list) else '',
        ])
    return _write_to_sheet(
        config.SHEET_CHANNEL_INSIGHTS,
        ['분석일', '채널명', '성공공식', '잘되는주제', '성공제목패턴', '실패패턴', '차별점', '한고은적용포인트'],
        rows,
    )


def save_daily_trends(trends_data):
    today = datetime.now().strftime('%Y-%m-%d')
    row = [
        today, trends_data.get('headline', ''),
        json.dumps(trends_data.get('top_5_keywords', []), ensure_ascii=False),
        json.dumps(trends_data.get('rising_topics', []), ensure_ascii=False),
        trends_data.get('hangoeun_action', ''), trends_data.get('watch_out', ''),
    ]
    return _write_to_sheet(
        config.SHEET_TRENDS,
        ['날짜', '핵심트렌드', '키워드', '떠오르는주제', '한고은적용아이디어', '주의영상'],
        [row],
    )


def save_weekly_report(report):
    today = datetime.now().strftime('%Y-%m-%d')
    row = [
        today, report.get('week_summary', ''),
        json.dumps(report.get('top_performing_channels', []), ensure_ascii=False),
        json.dumps(report.get('rising_topics', []), ensure_ascii=False),
        json.dumps(report.get('format_trends', []), ensure_ascii=False),
        report.get('ppl_observations', ''),
        json.dumps(report.get('actionable_insights_for_hangoeun', []), ensure_ascii=False),
        json.dumps(report.get('next_video_suggestions', []), ensure_ascii=False),
    ]
    return _write_to_sheet(
        config.SHEET_WEEKLY,
        ['날짜', '주간요약', '잘나가는채널', '떠오르는주제', '포맷트렌드', 'PPL관찰', '한고은액션', '다음영상방향성'],
        [row],
    )


def get_videos_from_sheet(days_back=7):
    """최근 N일 분석 영상 조회 (Supabase 직접)"""
    import supabase_writer as db
    cutoff = (datetime.now() - timedelta(days=days_back)).date().isoformat()
    client = db.get_client()
    if not client:
        return []
    try:
        da = client.table('daily_analysis').select('video_id').gte('analysis_date', cutoff).execute()
        ia = client.table('initial_analysis').select('video_id').gte('analysis_date', cutoff).execute()
        video_ids = list({r['video_id'] for r in da.data + ia.data})
        if not video_ids:
            return []
        vids = client.table('videos')\
            .select('video_id,channel_title,title,view_count')\
            .in_('video_id', video_ids).execute()
        return [{'채널명': v['channel_title'], '제목': v['title'], '조회수': v['view_count']} for v in vids.data]
    except Exception as e:
        print(f"  ⚠️ 최근 영상 조회 실패: {e}")
        return []


def save_own_analysis(video: dict, gemini: dict, comments: dict) -> int:
    today = datetime.now().strftime('%Y-%m-%d')
    g = gemini or {}
    c = comments or {}
    sentiment = c.get('sentiment', {}) if isinstance(c, dict) else {}
    row = [
        today,
        '숏츠' if video.get('video_type') == 'shorts' else '롱폼',
        video.get('video_id', ''),
        video.get('title', ''),
        video.get('published_at', '')[:10],
        video.get('view_count', 0),
        video.get('like_count', 0),
        video.get('comment_count', 0),
        video.get('duration', ''),
        g.get('topic', ''),
        g.get('content_structure', ''),
        g.get('title_pattern', ''),
        g.get('title_emotion_tone', ''),
        g.get('hook_strategy', ''),
        str(g.get('ppl_likely', '')),
        g.get('ppl_signals', ''),
        g.get('target_audience', ''),
        g.get('predicted_performance', ''),
        g.get('predicted_performance_reason', ''),
        g.get('strengths', ''),
        g.get('improvement_points', ''),
        g.get('thumbnail_suggestion', ''),
        g.get('competitor_angle', ''),
        sentiment.get('positive', ''),
        sentiment.get('negative', ''),
        ', '.join(c.get('praise_points', [])) if isinstance(c.get('praise_points'), list) else '',
        ', '.join(c.get('complaints', [])) if isinstance(c.get('complaints'), list) else '',
        ', '.join(c.get('next_video_requests', [])) if isinstance(c.get('next_video_requests'), list) else '',
        c.get('new_viewer_signals', ''),
        c.get('fan_engagement', ''),
        c.get('viral_signals', ''),
        str(c.get('revisit_intent', '')),
        c.get('ppl_reaction', ''),
        c.get('creator_feedback', ''),
        c.get('summary', ''),
        video.get('video_url', ''),
    ]
    return _write_to_sheet(
        config.SHEET_OWN_ANALYSIS,
        [
            '분석일', '유형', '영상ID', '제목', '업로드일', '조회수', '좋아요', '댓글수', '영상길이',
            '주제', '콘텐츠구조', '제목패턴', '제목감정톤', '후킹전략', 'PPL여부', 'PPL근거',
            '타겟층', '성과예측', '예측이유', '콘텐츠강점', '개선포인트', '썸네일제안', '경쟁사비교',
            '여론_긍정', '여론_부정', '칭찬포인트', '불만사항', '다음영상요청',
            '신규시청자신호', '팬반응', '바이럴시그널', '재방문의사%', 'PPL반응', '크리에이터피드백',
            '댓글요약', 'URL',
        ],
        [row], dedupe_col=2,
    )


def save_own_tracking(tracking_results: list) -> int:
    today = datetime.now().strftime('%Y-%m-%d')
    rows = []
    for r in tracking_results:
        growth = r.get('view_growth', 0)
        rows.append([
            today,
            r.get('week_number', ''),
            '숏츠' if r.get('video_type') == 'shorts' else '롱폼',
            r.get('title', ''),
            r.get('published_at', '')[:10] if r.get('published_at') else '',
            r.get('view_count', 0),
            f"+{growth:,}" if growth >= 0 else f"{growth:,}",
        ])
    return _write_to_sheet(
        config.SHEET_OWN_TRACKING,
        ['추적일', '주차', '유형', '제목', '업로드일', '누적조회수', '주간증가'],
        rows,
    )


def save_strategy_scores(scores: list, videos_map: dict = None) -> int:
    today = datetime.now().strftime('%Y-%m-%d')
    vm = videos_map or {}
    rows = []
    for s in scores:
        vid = vm.get(s.get('video_id', ''), {})
        rows.append([
            today,
            s.get('score_version', ''),
            s.get('source_table', ''),
            vid.get('channel_title', ''),
            vid.get('title', ''),
            s.get('video_id', ''),
            vid.get('view_count', 0),
            str(vid.get('published_at', ''))[:10],
            s.get('hangoeun_fit_score', 0),
            s.get('execution_score', 0),
            s.get('repeatability_score', 0),
            s.get('novelty_score', 0),
            s.get('risk_score', 0),
            s.get('ppl_potential_score', 0),
            s.get('trend_lifespan_score', 0),
            s.get('upload_delay_risk', 0),
            s.get('evergreen_score', 0),
            s.get('content_lifespan_type', ''),
            s.get('priority_score', 0),
            s.get('recommended_action', ''),
            s.get('strategy_reason', ''),
            vid.get('video_url', ''),
        ])
    return _write_to_sheet(
        config.SHEET_SCORES,
        [
            '점수일', '버전', '출처', '채널명', '제목', '영상ID', '조회수', '업로드일',
            '한고은적합도', '실행용이성', '반복가능성', '참신성', '리스크', 'PPL잠재력',
            '트렌드수명', '지연리스크', '에버그린성', '수명타입',
            '우선순위점수', '추천액션', '전략이유', 'URL',
        ],
        rows, dedupe_col=5,
    )


def save_trend_classified(classified: dict) -> int:
    today = datetime.now().strftime('%Y-%m-%d')
    rows = []

    def _to_row(v, track):
        return [
            today, track, v.get('video_id', ''),
            v.get('title', ''), v.get('channel_title', ''),
            v.get('view_count', 0), v.get('like_count', 0),
            v.get('comment_count', 0), v.get('published_at', ''),
            v.get('track_score_a', 0), v.get('track_score_b', 0),
            ', '.join(v.get('matched_keywords', [])) if isinstance(v.get('matched_keywords'), list) else '',
            ', '.join(v.get('irregular_reasons', [])) if isinstance(v.get('irregular_reasons'), list) else '',
            f"https://www.youtube.com/watch?v={v.get('video_id', '')}",
        ]

    for v in classified.get('track_a', []):
        rows.append(_to_row(v, 'A'))
    for v in classified.get('track_b', []):
        rows.append(_to_row(v, 'B'))
    for v in classified.get('irregular', []):
        rows.append(_to_row(v, 'IRREGULAR'))

    saved = _write_to_sheet(
        config.SHEET_TRENDS,
        ['날짜', '트랙', '영상ID', '제목', '채널명',
         '조회수', '좋아요', '댓글수', '업로드일',
         '트랙A점수', '트랙B점수', '매칭키워드', '이레귤러사유', 'URL'],
        rows,
    )

    event_kws = classified.get('event_keywords', [])
    if event_kws:
        _write_to_sheet(
            config.SHEET_EVENT_KEYWORDS,
            ['날짜', '사건키워드'],
            [[today, ', '.join(event_kws)]],
        )

    return saved
