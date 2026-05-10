"""
딥시크 API를 이용한 텍스트 분석 모듈
- 댓글 여론 분석 (강화)
- 대박 영상 심층 분석 (신규)
- 채널별 성공 공식 도출
- 주간 리포트 생성
"""
from openai import OpenAI
import json
import time
import config


def get_client():
    return OpenAI(api_key=config.DEEPSEEK_API_KEY, base_url="https://api.deepseek.com")


def call_deepseek(prompt, complex_task=False, retry=3):
    client = get_client()
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


def analyze_comments(video_title, comments):
    """영상 댓글 여론 분석 (강화)"""
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
  "sentiment": {{"positive": 0~100, "negative": 0~100, "neutral": 0~100}},
  "main_keywords": ["자주 등장하는 키워드 5개"],
  "viewer_persona": "추정 시청자층 (연령/성별/관심사)",
  "praise_points": ["시청자가 좋아하는 점 3개"],
  "complaints": ["시청자 불만/요청사항 (없으면 빈 배열)"],
  "suggestions": ["콘텐츠 개선 인사이트"],
  "this_video_special": "댓글에서 읽히는 이 영상만의 특별한 이유 - 시청자들이 직접 언급한 표현 기반으로",
  "revisit_intent": 0,
  "viral_signals": "공유·추천·감동 관련 댓글 패턴 (없으면 없음)",
  "summary": "전반적인 여론 한 줄 요약"
}}

revisit_intent는 재방문/재구독/공유 의사를 드러내는 댓글 비율 추정값 (0~100 정수).
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
