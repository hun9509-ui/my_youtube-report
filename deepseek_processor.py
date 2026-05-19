"""
텍스트 분석 모듈 (Gemini 또는 DeepSeek — config.TEXT_ANALYSIS_ENGINE으로 전환)
- 댓글 여론 분석
- 대박 영상 심층 분석
- 채널별 성공 공식 도출
- 주간 리포트 / 시장 변화 분석 생성
"""
import json
import re
import time
import config

_vertexai_initialized = False


def _ensure_vertexai():
    global _vertexai_initialized
    if not _vertexai_initialized:
        import vertexai
        vertexai.init(project=config.GOOGLE_CLOUD_PROJECT, location=config.GOOGLE_CLOUD_LOCATION)
        _vertexai_initialized = True


def _call_gemini_text(prompt: str, retry: int = 3) -> dict:
    """Gemini 3.1 Pro로 텍스트 분석 (JSON 응답)"""
    from vertexai.generative_models import GenerativeModel
    _ensure_vertexai()
    model = GenerativeModel(config.get_gemini_model('text'))

    full_prompt = "당신은 한국 유튜브 콘텐츠 분석 전문가입니다. 항상 JSON 형식으로만 응답하세요.\n\n" + prompt

    for attempt in range(retry):
        try:
            response = model.generate_content(full_prompt)
            text = response.text.strip()
            block = re.search(r'```(?:json)?\s*([\s\S]*?)```', text)
            if block:
                text = block.group(1).strip()
            obj = re.search(r'\{[\s\S]*\}', text)
            return json.loads(obj.group() if obj else text)
        except json.JSONDecodeError as e:
            if attempt < retry - 1:
                time.sleep(3)
                continue
            return {'error': f'JSON 파싱 실패: {e}'}
        except Exception as e:
            err = str(e)
            if any(x in err.lower() for x in ["quota", "rate", "429", "resource_exhausted"]):
                print(f"  ⚠️ Gemini rate limit (시도 {attempt+1}), 60초 대기...")
                time.sleep(60)
                continue
            if attempt < retry - 1:
                time.sleep(5)
                continue
            return {'error': err}
    return {'error': 'Max retries exceeded'}


def _call_deepseek_api(prompt: str, complex_task: bool = False, retry: int = 3) -> dict:
    """DeepSeek API 직접 호출"""
    from openai import OpenAI
    client = OpenAI(api_key=config.DEEPSEEK_API_KEY, base_url="https://api.deepseek.com")
    model = config.get_deepseek_model(complex_task=complex_task)

    for attempt in range(retry):
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": "당신은 한국 유튜브 콘텐츠 분석 전문가입니다. 항상 JSON 형식으로 응답하세요."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.3,
                response_format={"type": "json_object"}
            )
            return json.loads(response.choices[0].message.content)
        except Exception as e:
            if attempt < retry - 1:
                time.sleep(5)
                continue
            return {'error': str(e)}
    return {'error': 'Max retries exceeded'}


def call_deepseek(prompt, complex_task=False, retry=3):
    """텍스트 분석 진입점 — config.TEXT_ANALYSIS_ENGINE에 따라 Gemini 또는 DeepSeek 라우팅"""
    if getattr(config, 'TEXT_ANALYSIS_ENGINE', 'deepseek') == 'gemini':
        return _call_gemini_text(prompt, retry=retry)
    return _call_deepseek_api(prompt, complex_task=complex_task, retry=retry)


def analyze_comments(video_title, comments):
    """영상 댓글 여론 분석"""
    if not comments:
        return {'error': '댓글 없음'}

    top_comments = sorted(comments, key=lambda x: x.get('like_count', 0), reverse=True)[:200]
    comments_text = "\n".join([
        f"[👍{c['like_count']}] {c['text'][:200]}"
        for c in top_comments
    ])

    prompt = f"""
영상 제목: {video_title}

상위 댓글 {len(top_comments)}개:
{comments_text[:8000]}

이 댓글들을 분석해 다음 JSON으로 답변하세요:
{{
  "sentiment": {{"positive": 0, "negative": 0, "neutral": 0}},
  "emotion_temperature": {{
    "comfort": 0,
    "healing": 0,
    "nostalgia": 0,
    "trust": 0,
    "intimacy": 0,
    "aspiration": 0,
    "envy": 0,
    "fatigue": 0,
    "cringe": 0
  }},
  "dominant_emotion": "위 9개 중 가장 강하게 느껴지는 감정 1개",
  "main_keywords": ["자주 등장하는 키워드 5개"],
  "viewer_persona": "추정 시청자층 (연령/성별/관심사)",
  "new_viewer_signals": "알고리즘/추천으로 처음 온 시청자 댓글 패턴 (없으면 없음)",
  "algorithm_discovery_rate": 0,
  "praise_points": ["시청자가 좋아하는 점 3개"],
  "complaints": ["시청자 불만/요청사항 (없으면 빈 배열)"],
  "suggestions": ["콘텐츠 개선 인사이트"],
  "this_video_special": "댓글에서 읽히는 이 영상만의 특별한 이유",
  "revisit_intent": 0,
  "viral_signals": "공유·추천·감동 관련 댓글 패턴 (없으면 없음)",
  "summary": "전반적인 여론 한 줄 요약"
}}

- emotion_temperature 각 항목: 해당 감정을 드러내는 댓글 비율 추정값 (0~100 정수)
- algorithm_discovery_rate: "알고리즘 타고 왔어요", "추천 떠서", "처음 봤는데" 등 신규 유입 패턴 댓글 비율 (0~100 정수)
- revisit_intent: 재방문/재구독/공유 의사 비율 (0~100 정수)
JSON만 반환하세요.
"""
    return call_deepseek(prompt, complex_task=False)


def analyze_hit_video(video, gemini_result, comments_analysis):
    """대박 영상 전용 심층 분석 (채널 평균 × 2배 이상)"""
    avg_views = video.get('channel_avg_views', 0)
    view_count = video.get('view_count', 0)
    ratio = view_count / max(avg_views, 1)

    prompt = f"""
이 영상은 채널 평균({avg_views:,}회) 대비 {ratio:.1f}배의 조회수를 기록한 대박 영상입니다.
왜 이 영상이 특별히 터졌는지 철저히 분해해주세요.

[영상 정보]
제목: {video.get('title', '')}
채널: {video.get('channel_title', '')}
조회수: {view_count:,}회 (채널 평균 {avg_views:,}회의 {ratio:.1f}배)

[Gemini 분석]
주제: {gemini_result.get('topic', '')}
후킹 전략: {gemini_result.get('hook_strategy', '')}
콘텐츠 구조: {gemini_result.get('content_structure', '')}
성공 요인: {gemini_result.get('success_factors', '')}
이 영상만의 차별화: {gemini_result.get('unique_differentiator', '')}

[댓글 분석]
여론 요약: {comments_analysis.get('summary', '')}
이 영상만의 이유: {comments_analysis.get('this_video_special', '')}
바이럴 시그널: {comments_analysis.get('viral_signals', '')}

다음 JSON으로 답변:
{{
  "success_drivers": {{
    "title_hook": 0,
    "topic_choice": 0,
    "timing": 0,
    "channel_fandom": 0
  }},
  "primary_driver": "위 4가지 중 가장 핵심적인 성공 드라이버 (한 단어)",
  "seasonality": "시즌성/사회적 이슈와의 연결 여부 (있으면 구체적으로, 없으면 없음)",
  "what_clicked": "이 영상이 특별히 터진 핵심 이유 - 데이터와 댓글 기반으로 구체적으로",
  "hangoeun_version": "한고은(60대 살림/라이프, 담백·진정성)이 이 성공 요소를 활용한다면: 구체적 제목 예시 1개 + 구성 방향"
}}

success_drivers의 각 항목은 성공 기여도 점수 (0~10 정수).
"""
    return call_deepseek(prompt, complex_task=True)


def analyze_own_video_comments(video_title, comments, video_type='longform'):
    """자체 채널 댓글 분석 - 크리에이터 관점"""
    if not comments:
        return {'error': '댓글 없음'}

    top_comments = sorted(comments, key=lambda x: x.get('like_count', 0), reverse=True)[:200]
    comments_text = "\n".join([
        f"[👍{c['like_count']}] {c['text'][:200]}"
        for c in top_comments
    ])
    vtype = '숏츠' if video_type == 'shorts' else '롱폼'

    prompt = f"""
영상 제목: {video_title}
영상 유형: {vtype}

상위 댓글 {len(top_comments)}개:
{comments_text[:8000]}

크리에이터 관점에서 이 댓글들을 분석해 다음 JSON으로 답변하세요:
{{
  "sentiment": {{"positive": 0, "negative": 0, "neutral": 0}},
  "praise_points": ["시청자가 구체적으로 좋아한 점 3가지"],
  "complaints": ["불만 또는 개선 요청 (없으면 빈 배열)"],
  "next_video_requests": ["시청자가 다음에 보고 싶어하는 콘텐츠 3가지 - 구체적 표현 기반"],
  "new_viewer_signals": "처음 방문 시청자 댓글 패턴 (없으면 없음)",
  "fan_engagement": "고정 팬 반응 특징",
  "viral_signals": "공유·추천·감동 댓글 패턴 (없으면 없음)",
  "revisit_intent": 0,
  "ppl_reaction": "PPL/협찬 언급 반응 (없으면 없음)",
  "creator_feedback": "크리에이터에게 전달할 핵심 피드백 1-2줄",
  "summary": "전반적 여론 한 줄 요약"
}}

sentiment는 0~100 정수. revisit_intent는 재방문/재구독 의사 비율 (0~100 정수).
JSON만 반환하세요.
"""
    return call_deepseek(prompt, complex_task=False)


def derive_channel_success_formula(channel_name, videos_with_analysis):
    """채널의 성공 공식 도출"""
    if len(videos_with_analysis) < 5:
        return {'error': '분석할 영상이 부족함 (5개 미만)'}

    views = [v.get('view_count', 0) for v in videos_with_analysis]
    avg_views = sum(views) / len(views) if views else 0

    hits = [v for v in videos_with_analysis if v.get('view_count', 0) >= avg_views * 2]
    flops = [v for v in videos_with_analysis if v.get('view_count', 0) < avg_views * 0.5]

    hits_summary = "\n".join([
        f"- [{v.get('view_count', 0):,}회] {v.get('title', '')}"
        for v in hits[:15]
    ])
    flops_summary = "\n".join([
        f"- [{v.get('view_count', 0):,}회] {v.get('title', '')}"
        for v in flops[:10]
    ])

    prompt = f"""
채널: {channel_name}
평균 조회수: {avg_views:,.0f}회
분석 영상 수: {len(videos_with_analysis)}개

★ 대박 영상 (평균 2배 이상):
{hits_summary if hits else '없음'}

★ 저조 영상 (평균 절반 미만):
{flops_summary if flops else '없음'}

이 채널의 성공/실패 패턴을 분석해 JSON으로 답변:
{{
  "success_formula": "이 채널의 성공 공식 (구체적으로)",
  "winning_topics": ["잘 되는 주제 패턴 3가지"],
  "winning_title_patterns": ["성공한 제목 패턴 3가지"],
  "failure_patterns": ["실패하는 패턴 (있다면)"],
  "differentiator": "이 채널만의 차별점",
  "lessons_for_hangoeun": "한고은 채널(60대 살림/라이프)이 배울 점 3가지"
}}
"""
    return call_deepseek(prompt, complex_task=True)


def generate_weekly_report(all_channel_insights, top_videos_this_week):
    """주간 종합 리포트 생성"""
    insights_text = json.dumps(all_channel_insights, ensure_ascii=False, indent=2)[:5000]
    top_videos_text = "\n".join([
        f"- [{v.get('channel_title', '')}] {v.get('title', '')} ({v.get('view_count', 0):,}회)"
        for v in top_videos_this_week[:20]
    ])

    prompt = f"""
지난 주 경쟁 채널 분석 종합 리포트를 작성해주세요.

[채널별 인사이트]
{insights_text}

[지난 주 TOP 20 영상]
{top_videos_text}

다음 JSON 형식으로 답변:
{{
  "week_summary": "이번 주 핵심 트렌드 한 단락 요약",
  "top_performing_channels": ["가장 잘 나가는 채널 3개와 이유"],
  "rising_topics": ["떠오르는 주제 5개"],
  "format_trends": ["인기 콘텐츠 포맷 트렌드"],
  "ppl_observations": "이번 주 눈에 띄는 PPL/협찬 패턴",
  "actionable_insights_for_hangoeun": ["한고은 채널에 적용 가능한 구체적 액션 5개"],
  "next_video_suggestions": ["이번 주 트렌드 기반 다음 영상 방향성 제안 3개 (제목이 아닌 방향성)"]
}}
"""
    return call_deepseek(prompt, complex_task=True)


def generate_market_intelligence(snapshot_summary: dict, emotion_summary: dict, top_videos: list) -> dict:
    """
    시장 변화 분석 — weekly_market_state 생성용.
    snapshot_summary: 지난 7일 성장 패턴 집계
    emotion_summary: 지난 7일 감정 온도 집계
    top_videos: 이번 주 조회수 TOP 영상 목록
    """
    top_text = "\n".join([
        f"- [{v.get('channel_title','')}] {v.get('title','')} "
        f"({v.get('view_count',0):,}회, 성장패턴: {v.get('growth_pattern','?')})"
        for v in top_videos[:20]
    ])

    longtail_videos = [v for v in top_videos if v.get('growth_pattern') in ('LONGTAIL', 'EVERGREEN')]
    fast_decay = [v for v in top_videos if v.get('growth_pattern') == 'FAST_DECAY']

    longtail_text = "\n".join([f"- {v.get('title','')} ({v.get('channel_title','')})" for v in longtail_videos[:5]])
    fast_text = "\n".join([f"- {v.get('title','')} ({v.get('channel_title','')})" for v in fast_decay[:5]])

    emotion_text = json.dumps(emotion_summary, ensure_ascii=False)
    snapshot_text = json.dumps(snapshot_summary, ensure_ascii=False)

    prompt = f"""
당신은 셀럽 라이프스타일 유튜브 시장 분석가입니다.
한고은 채널(60대, 살림·라이프, 담백·진정성)의 PD에게 보낼 시장 변화 분석 보고서를 작성하세요.

[이번 주 성장 패턴 집계]
{snapshot_text[:1500]}

[감정 온도 트렌드]
{emotion_text[:1000]}

[TOP 영상]
{top_text}

[롱테일/에버그린 영상]
{longtail_text if longtail_text else "없음"}

[단기 바이럴 후 급락 영상]
{fast_text if fast_text else "없음"}

중요: 셀럽 유튜브는 촬영~업로드 2~6주 차이가 있으므로, 단기 바이럴보다 "한달 뒤에도 살아남는 콘텐츠" 관점으로 분석하세요.

다음 JSON으로 답변:
{{
  "dominant_emotion": "이번 주 시청자들이 가장 많이 느낀 감정 1개 (comfort/healing/nostalgia/trust/intimacy/aspiration/envy/fatigue/cringe 중)",
  "rising_topics": ["상승 중인 주제/포맷 5개 (구체적으로)"],
  "declining_topics": ["피로해지거나 하락 중인 주제/포맷 3개"],
  "oversaturated_formats": ["경쟁 채널들이 너무 많이 하고 있어 차별화가 어려운 포맷 3개"],
  "emerging_formats": ["아직 소수만 하지만 떠오르는 신규 포맷 2개"],
  "viewer_fatigue_signals": ["시청자 피로 신호 (댓글·조회수 패턴에서 읽히는 것)"],
  "comfort_content_score": 0,
  "hangoeun_opportunity_score": 0,
  "hangoeun_recommended_topics": ["한고은 채널에 가장 적합한 기회 주제 3개 (30일 뒤 업로드해도 살아남는 것 우선)"],
  "competitive_gap": ["경쟁 채널들이 안 하는데 수요가 있는 공백 주제 2개"],
  "market_summary": "이번 주 시장 한 줄 요약"
}}

- comfort_content_score: 위로/공감형 콘텐츠 수요 강도 (0~100)
- hangoeun_opportunity_score: 한고은 채널의 이번 주 기회 점수 (0~100)
JSON만 반환하세요.
"""
    return call_deepseek(prompt, complex_task=True)


def classify_growth_pattern(d1_views: int, d7_views: int, d30_views: int) -> str:
    """
    스냅샷 데이터로 성장 패턴 분류 (결정론적, API 없음).
    d1/d7/d30: 각 시점의 누적 조회수 (0이면 미수집)
    """
    if d1_views <= 0:
        return 'UNKNOWN'

    if d7_views > 0:
        d7_ratio = d7_views / d1_views  # D7/D1 비율
    else:
        return 'UNKNOWN'

    if d30_views > 0:
        d30_ratio = d30_views / d7_views if d7_views > 0 else 0
    else:
        d30_ratio = None

    # D7/D1 비율 기준 1차 분류
    if d7_ratio < 1.3:
        pattern = 'FAST_DECAY'    # 1주 내 대부분 소진
    elif d7_ratio < 2.0:
        pattern = 'STEADY'         # 꾸준한 선형 성장
    else:
        pattern = 'LONGTAIL'       # 1주 후에도 강한 성장

    # D30 데이터 있으면 정밀화
    if d30_ratio is not None:
        if d30_ratio >= 2.0:
            pattern = 'EVERGREEN'  # 30일 후에도 D7 대비 2배+ 성장
        elif d30_ratio >= 1.5 and pattern == 'LONGTAIL':
            pattern = 'EVERGREEN'

    return pattern


def summarize_daily_trends(trending_data, gemini_analysis):
    """일일 트렌드 요약 (텔레그램 발송용)"""
    prompt = f"""
오늘 한국 유튜브 트렌드 데이터입니다.

[Gemini 1차 분석]
{json.dumps(gemini_analysis, ensure_ascii=False)[:3000]}

[원본 트렌드 영상 TOP]
{json.dumps([{'title': v['title'], 'channel': v['channel_title'], 'views': v['view_count']} for v in trending_data[:20]], ensure_ascii=False)[:2000]}

이걸 한고은 채널 PD에게 보낼 텔레그램 메시지로 작성하세요.
JSON 형식:
{{
  "headline": "오늘의 핵심 트렌드 (한 줄)",
  "top_5_keywords": ["키워드와 간단 설명 5개"],
  "hangoeun_action": "한고은 채널에 즉시 적용 가능한 아이디어 1-2개",
  "watch_out": "주의 깊게 볼 만한 영상 1개와 이유"
}}
"""
    return call_deepseek(prompt, complex_task=False)
