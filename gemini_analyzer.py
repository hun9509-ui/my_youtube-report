"""
Vertex AI를 이용한 영상 분석 모듈 (Gemini 3.1 Flash Lite)
"""
import vertexai
from vertexai.generative_models import GenerativeModel
import json
import re
import time
import config

_initialized = False


def _ensure_configured():
    global _initialized
    if not _initialized:
        vertexai.init(
            project=config.GOOGLE_CLOUD_PROJECT,
            location=config.GOOGLE_CLOUD_LOCATION
        )
        _initialized = True


def _extract_json(text: str) -> dict:
    """응답에서 JSON 추출 (형식 무관 robust 처리)"""
    text = text.strip()
    block = re.search(r'```(?:json)?\s*([\s\S]*?)```', text)
    if block:
        text = block.group(1).strip()
    obj = re.search(r'\{[\s\S]*\}', text)
    if obj:
        return json.loads(obj.group())
    return json.loads(text)


ANALYSIS_PROMPT = """
당신은 유튜브 콘텐츠 분석 전문가입니다.
아래 영상 정보를 분석해 다음 항목을 JSON으로 정리해주세요.

영상 제목: {title}
채널명: {channel}
설명: {description}
태그: {tags}
조회수: {views}
좋아요: {likes}
댓글수: {comments}
영상 길이: {duration}

다음 항목으로 분석:
{{
  "topic": "영상의 핵심 주제 (1줄)",
  "category": "라이프스타일/뷰티/요리/육아/패션/여행/일상/기타 중 하나",
  "title_pattern": "질문형/단정형/숫자형/감정형/정보형 중 하나",
  "title_keywords": ["핵심 키워드 3개"],
  "title_emotion_tone": "따뜻함/놀람/친근함/권위감/유머/공감/기대감 중 하나",
  "hook_strategy": "시청자를 끌어들이는 후킹 전략 (구체적으로)",
  "content_structure": "브이로그형/정보전달형/스토리텔링형/리뷰형/토크형 중 하나",
  "ppl_likely": true,
  "ppl_signals": "PPL 가능성 근거 (없으면 빈 문자열)",
  "target_audience": "주 타겟 시청자층 (연령/성별/관심사 포함)",
  "performance_level": "대박/평작/저조 중 하나 (조회수와 채널 규모 고려)",
  "success_factors": "이 영상의 성공/실패 추정 요인 (구체적으로 2-3줄)",
  "unique_differentiator": "같은 채널 다른 영상 대비 이 영상만이 가진 특별한 점 (없으면 없음)",
  "applicability": "한고은 채널(60대 살림/라이프, 담백·진정성) 적용 가능성 (상/중/하)",
  "applicability_reason": "적용 가능성 이유 (1줄)",
  "hangoeun_scenario": "한고은 채널이라면 이 영상을 어떻게 만들지 - 제목 방향성, 구성, 핵심 메시지를 포함해 구체적으로 2-3줄"
}}

반드시 JSON만 반환하세요. 다른 설명은 절대 하지 마세요.
"""


def analyze_video(video_data, model_name=None, retry=3):
    """단일 영상 분석"""
    _ensure_configured()
    if model_name is None:
        model_name = config.get_gemini_model('daily')
    model = GenerativeModel(model_name)

    prompt = ANALYSIS_PROMPT.format(
        title=video_data.get('title', ''),
        channel=video_data.get('channel_title', ''),
        description=video_data.get('description', '')[:300],
        tags=video_data.get('tags', ''),
        views=video_data.get('view_count', 0),
        likes=video_data.get('like_count', 0),
        comments=video_data.get('comment_count', 0),
        duration=video_data.get('duration', '')
    )

    for attempt in range(retry):
        try:
            response = model.generate_content(prompt)
            result = _extract_json(response.text)
            result['video_id'] = video_data.get('video_id', '')
            return result

        except json.JSONDecodeError as e:
            raw = response.text[:300] if 'response' in dir() else ''
            print(f"  ⚠️ JSON 파싱 실패 (시도 {attempt+1}): {e} | 응답: {raw}")
            if attempt < retry - 1:
                time.sleep(2)
                continue
            return {
                'video_id': video_data.get('video_id', ''),
                'error': f'JSON 파싱 실패: {e}',
                'raw_response': raw
            }
        except Exception as e:
            err = str(e)
            if any(x in err.lower() for x in ["quota", "rate", "429", "resource_exhausted", "resourceexhausted"]):
                print(f"  ⚠️ Rate limit (시도 {attempt+1}), 60초 대기...")
                time.sleep(60)
                continue
            print(f"  ❌ Gemini 오류: {err}")
            return {'video_id': video_data.get('video_id', ''), 'error': err}

    return {'video_id': video_data.get('video_id', ''), 'error': 'Max retries exceeded'}


def analyze_videos_batch(videos, delay=5, model_mode='daily'):
    """여러 영상 배치 분석"""
    model_name = config.get_gemini_model(model_mode)
    print(f"  🤖 Gemini 모델: {model_name} (Vertex AI)")
    results = []
    errors = 0

    for i, video in enumerate(videos):
        print(f"  분석 중 ({i+1}/{len(videos)}): {video.get('title', '')[:45]}")
        result = analyze_video(video, model_name=model_name)
        if 'error' in result:
            errors += 1
            print(f"    ❌ 실패: {result['error']}")
        results.append(result)

        if i < len(videos) - 1:
            time.sleep(delay)

    print(f"  ✅ Gemini 완료: 성공 {len(results)-errors}개 / 실패 {errors}개")
    return results


OWN_CHANNEL_PROMPT = """
당신은 유튜브 채널 "고은언니 한고은" (60대 여성, 살림·라이프, 담백·진정성)의 콘텐츠 전략가입니다.
방금 이 채널에 올라온 영상을 크리에이터 관점으로 분석해주세요.

영상 제목: {title}
영상 유형: {video_type}
설명: {description}
태그: {tags}
영상 길이: {duration}
업로드 초기 조회수: {views}

다음 JSON으로 분석:
{{
  "topic": "핵심 주제 (1줄)",
  "content_structure": "브이로그형/정보전달형/스토리텔링형/리뷰형/토크형",
  "title_pattern": "질문형/단정형/숫자형/감정형/정보형",
  "title_emotion_tone": "따뜻함/놀람/친근함/권위감/유머/공감/기대감",
  "hook_strategy": "시청자를 끌어들이는 전략",
  "ppl_likely": true,
  "ppl_signals": "PPL 근거 (없으면 빈 문자열)",
  "target_audience": "이 영상의 주 타겟층",
  "predicted_performance": "높음/보통/낮음",
  "predicted_performance_reason": "예측 이유 (구체적으로)",
  "strengths": "이 영상 콘텐츠의 구체적 강점 2-3가지",
  "improvement_points": "다음에 더 잘할 수 있는 구체적 포인트 1-2가지",
  "thumbnail_suggestion": "썸네일 개선 제안 (없으면 없음)",
  "competitor_angle": "경쟁 채널들이 비슷한 주제를 다룰 때와 비교한 이 영상의 차별점 또는 개선 방향"
}}

JSON만 반환하세요.
"""


def analyze_own_video(video_data, retry=3):
    """자체 채널 영상 분석 (크리에이터 관점)"""
    _ensure_configured()
    model_name = config.get_gemini_model('daily')
    model = GenerativeModel(model_name)

    video_type = video_data.get('video_type', 'longform')
    prompt = OWN_CHANNEL_PROMPT.format(
        title=video_data.get('title', ''),
        video_type='숏츠' if video_type == 'shorts' else '롱폼',
        description=video_data.get('description', '')[:300],
        tags=video_data.get('tags', ''),
        duration=video_data.get('duration', ''),
        views=video_data.get('view_count', 0),
    )

    for attempt in range(retry):
        try:
            response = model.generate_content(prompt)
            result = _extract_json(response.text)
            result['video_id'] = video_data.get('video_id', '')
            return result
        except json.JSONDecodeError as e:
            if attempt < retry - 1:
                time.sleep(2)
                continue
            return {'video_id': video_data.get('video_id', ''), 'error': f'JSON 파싱 실패: {e}'}
        except Exception as e:
            err = str(e)
            if any(x in err.lower() for x in ["quota", "rate", "429", "resource_exhausted"]):
                time.sleep(60)
                continue
            return {'video_id': video_data.get('video_id', ''), 'error': err}

    return {'video_id': video_data.get('video_id', ''), 'error': 'Max retries exceeded'}


def detect_daily_trends(trending_videos):
    """일일 트렌드 영상에서 패턴 감지"""
    _ensure_configured()
    model = GenerativeModel(config.get_gemini_model('daily'))

    videos_summary = "\n".join([
        f"- [{v['view_count']:,}회] {v['title']} ({v['channel_title']})"
        for v in trending_videos[:30]
    ])

    prompt = f"""
오늘 한국 유튜브 인기 영상 TOP 30입니다.
이 데이터에서 트렌드 패턴을 분석해주세요.

{videos_summary}

다음 형식의 JSON으로 답변:
{{
  "top_trends": [
    {{"keyword": "트렌드 키워드", "reason": "왜 뜨고 있는지", "category": "카테고리"}},
    ... 5개
  ],
  "rising_topics": ["떠오르는 주제 3개"],
  "lifestyle_relevant": ["한고은 채널(60대 살림/라이프)에 적용 가능한 트렌드 3개"],
  "summary": "오늘의 트렌드 한 줄 요약"
}}

JSON만 반환하세요.
"""

    try:
        response = model.generate_content(prompt)
        return _extract_json(response.text)
    except Exception as e:
        return {'error': str(e)}
