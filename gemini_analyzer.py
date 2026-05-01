"""
Gemini API를 이용한 영상 분석 모듈
영상 메타데이터 + 썸네일을 분석해 인사이트 추출
"""
import google.generativeai as genai
import json
import time
import config

MODEL_NAME = "gemini-2.5-flash-lite"


def _ensure_configured():
    """Gemini API 키 설정 (호출 시점에)"""
    if config.GEMINI_API_KEY:
        genai.configure(api_key=config.GEMINI_API_KEY)

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
  "title_pattern": "제목의 패턴 (질문형/단정형/숫자형/감정형/정보형 중)",
  "title_keywords": ["핵심 키워드 3개"],
  "hook_strategy": "시청자를 끌어들이는 후킹 전략",
  "ppl_likely": true/false,
  "ppl_signals": "PPL 가능성 근거 (있을 시)",
  "target_audience": "주 타겟 시청자층",
  "performance_level": "대박/평작/저조 중 (조회수와 채널 규모 고려)",
  "success_factors": "성공/실패 추정 요인 (1-2줄)",
  "applicability": "한고은 채널(60대 살림/라이프) 적용 가능성 (상/중/하)",
  "applicability_reason": "적용 가능성 이유"
}}

JSON만 반환하고 다른 설명은 하지 마세요.
"""


def analyze_video(video_data, retry=3):
    """단일 영상 분석"""
    _ensure_configured()
    model = genai.GenerativeModel(MODEL_NAME)
    
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
            text = response.text.strip()
            
            # JSON 추출
            if text.startswith("```"):
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
                text = text.strip()
            
            result = json.loads(text)
            result['video_id'] = video_data.get('video_id', '')
            return result
            
        except json.JSONDecodeError:
            if attempt < retry - 1:
                time.sleep(2)
                continue
            return {
                'video_id': video_data.get('video_id', ''),
                'error': 'JSON 파싱 실패',
                'raw_response': text[:200] if 'text' in dir() else ''
            }
        except Exception as e:
            if "quota" in str(e).lower() or "rate" in str(e).lower():
                time.sleep(60)  # Rate limit 시 1분 대기
                continue
            return {
                'video_id': video_data.get('video_id', ''),
                'error': str(e)
            }
    
    return {'video_id': video_data.get('video_id', ''), 'error': 'Max retries exceeded'}


def analyze_videos_batch(videos, delay=6):
    """여러 영상 배치 분석 (분당 10회 제한 고려)"""
    results = []
    for i, video in enumerate(videos):
        print(f"  분석 중 ({i+1}/{len(videos)}): {video.get('title', '')[:40]}")
        result = analyze_video(video)
        results.append(result)
        
        if i < len(videos) - 1:
            time.sleep(delay)  # Rate limit 방지
    
    return results


def detect_daily_trends(trending_videos):
    """일일 트렌드 영상에서 패턴 감지"""
    _ensure_configured()
    model = genai.GenerativeModel(MODEL_NAME)
    
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
        text = response.text.strip()
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
            text = text.strip()
        return json.loads(text)
    except Exception as e:
        return {'error': str(e)}
