"""
딥시크 API를 이용한 텍스트 분석 모듈
- 댓글 여론 분석
- 채널별 성공 공식 도출
- 주간 리포트 생성
"""
from openai import OpenAI
import json
import time
import config


def get_client():
    """딥시크 클라이언트 (필요할 때만 생성)"""
    return OpenAI(
        api_key=config.DEEPSEEK_API_KEY,
        base_url="https://api.deepseek.com"
    )


def call_deepseek(prompt, complex_task=False, retry=3):
    """딥시크 API 호출"""
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
    """영상 댓글 200개 여론 분석"""
    if not comments:
        return {'error': '댓글 없음'}
    
    # 좋아요 순으로 정렬, 텍스트만 추출
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
  "complaints": ["시청자 불만/요청사항 (있을 시)"],
  "suggestions": ["콘텐츠 개선 인사이트"],
  "summary": "전반적인 여론 한 줄 요약"
}}
"""
    return call_deepseek(prompt, complex_task=False)


def derive_channel_success_formula(channel_name, videos_with_analysis):
    """채널의 성공 공식 자동 도출"""
    if len(videos_with_analysis) < 5:
        return {'error': '분석할 영상이 부족함 (5개 미만)'}
    
    # 평균 조회수 계산
    views = [v.get('view_count', 0) for v in videos_with_analysis]
    avg_views = sum(views) / len(views) if views else 0
    
    # 대박/평작/저조 분류
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
  "actionable_insights_for_hangoeun": [
    "한고은 채널에 적용 가능한 구체적 액션 5개"
  ],
  "next_video_suggestions": [
    "이번 주 트렌드 기반 다음 영상 방향성 제안 3개 (제목이 아닌 방향성)"
  ]
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
{json.dumps([{
    'title': v['title'],
    'channel': v['channel_title'],
    'views': v['view_count']
} for v in trending_data[:20]], ensure_ascii=False)[:2000]}

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
