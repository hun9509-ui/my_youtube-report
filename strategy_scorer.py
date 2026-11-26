"""
전략 스코어링 모듈 v2.0

기존 Gemini/DeepSeek 분석 결과를 입력으로 받아
순수 룰 기반 전략 점수를 산출한다.
API 호출 없음. 결정론적.
"""
import math

import config
from gemini_client import safe_error


PRIORITY_WEIGHTS = {
    "novelty_score": 0.34,
    "hangoeun_fit_score": 0.24,
    "execution_score": 0.14,
    "repeatability_score": 0.12,
    "evergreen_score": 0.06,
    "trend_lifespan_score": 0.05,
    "ppl_potential_score": 0.05,
    "risk_score": -0.14,
    "upload_delay_risk": -0.04,
}


def _clamp(v, lo: int = 0, hi: int = 100) -> int:
    return max(lo, min(hi, int(round(v))))


def _contains_any(text: str, keywords: list) -> bool:
    t = str(text).lower()
    return any(k in t for k in keywords)


def _float_or_zero(value) -> float:
    if isinstance(value, bool):
        return 0.0
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return 0.0
    return number if math.isfinite(number) else 0.0


def score_video(video: dict, analysis: dict, source_table: str, thumbnail: dict | None = None) -> dict:
    """
    video       : videos 테이블 기반 원본 영상 데이터
    analysis    : initial_analysis 또는 daily_analysis 기반 분석 데이터
    source_table: 'initial_analysis' | 'daily_analysis'
    thumbnail   : 선택적 thumbnail_analysis 행
    """
    video_id = video.get('video_id') or analysis.get('video_id', '')

    # Supabase JSONB 또는 직접 dict 모두 처리
    gemini = analysis.get('gemini_raw') or analysis.get('gemini') or {}
    comments = analysis.get('deepseek_raw') or analysis.get('comments') or {}

    category        = str(gemini.get('category')        or analysis.get('category', '')).lower()
    topic           = str(gemini.get('topic')            or analysis.get('topic', '')).lower()
    title           = str(video.get('title')             or analysis.get('title', '')).lower()
    title_pattern   = str(gemini.get('title_pattern')    or analysis.get('title_pattern', '')).lower()
    title_emotion   = str(gemini.get('title_emotion_tone') or analysis.get('title_emotion_tone', '')).lower()
    content_struct  = str(gemini.get('content_structure') or analysis.get('content_structure', '')).lower()
    hook_strategy   = str(gemini.get('hook_strategy')    or analysis.get('hook_strategy', '')).lower()
    unique_diff     = str(gemini.get('unique_differentiator') or '').strip().lower()
    applicability   = str(gemini.get('applicability')    or analysis.get('applicability', '')).strip()
    viral_signals   = str(comments.get('viral_signals')  or '').strip().lower()

    # ppl_likely: bool 또는 문자열 둘 다 허용
    ppl_raw = gemini.get('ppl_likely')
    if ppl_raw is None or ppl_raw == '':
        ppl_raw = analysis.get('ppl_likely', False)
    if isinstance(ppl_raw, str):
        ppl_likely = ppl_raw.lower() in ('true', 'yes', '예', '1')
    else:
        ppl_likely = bool(ppl_raw)

    combined = f"{title} {topic} {category}"
    emotion_temperature = comments.get('emotion_temperature') or {}
    if not isinstance(emotion_temperature, dict):
        emotion_temperature = {}
    desire = (_float_or_zero(emotion_temperature.get('envy'))
              + _float_or_zero(emotion_temperature.get('aspiration'))) / 2
    fatigue = max(_float_or_zero(emotion_temperature.get('fatigue')),
                  _float_or_zero(emotion_temperature.get('cringe')))
    ad_boost_suspected = bool(analysis.get('ad_boost_suspected') or comments.get('ad_boost_suspected'))
    trace = {}

    # ── 1. hangoeun_fit_score ──────────────────────────────────────
    fit = {'상': 65, '중': 60}.get(applicability, 35)
    fit_trace = [f"applicability={applicability or '미확인'} {fit}"]
    for label, points in (
        ('뷰티', 15), ('패션', 15), ('일상', 10), ('기타', 10),
        ('라이프스타일', 5), ('육아', 5), ('요리', -10), ('여행', -12),
    ):
        if label in category:
            fit += points
            fit_trace.append(f"category={label} {points:+d}")
            break
    if _contains_any(f"{combined} {content_struct}", ['부부', '남편', '가족', '게스트', '친구', '수다', '토크']):
        fit += 12
        fit_trace.append("persona/talk keyword +12")
    if _contains_any(combined, ['전문 메이크업', '정치', '게임', '논란']):
        fit -= 25
        fit_trace.append("negative keyword -25")

    hangoeun_fit_score = _clamp(fit)
    trace['hangoeun_fit'] = fit_trace

    # ── 2. execution_score ────────────────────────────────────────
    exec_trace = []
    exec_map = {
        '토크형': 88, '스토리텔링형': 72, '브이로그형': 65,
        '리뷰형': 62, '정보전달형': 58,
    }
    exec_base = 65
    for k, v in exec_map.items():
        if k in content_struct:
            exec_base = v
            exec_trace.append(f"content_structure={k} {v}")
            break
    else:
        exec_trace.append(f"content_structure 미확인 기본값 {exec_base}")

    exec_bonus = -10 if ppl_likely else 6
    exec_trace.append(f"ppl_likely={ppl_likely} {exec_bonus:+d}")
    if _contains_any(combined, ['여행', '해외', '야외 촬영']):
        exec_bonus -= 12
        exec_trace.append("travel/outdoor -12")
    if _contains_any(combined, ['부부', '토크', '게스트']):
        exec_bonus += 8
        exec_trace.append("couple/talk/guest +8")

    execution_score = _clamp(exec_base + exec_bonus)
    trace['execution'] = exec_trace

    # ── 3. repeatability_score ───────────────────────────────────
    rep = 45
    rep_trace = ["기본값 45"]
    if _contains_any(content_struct, ['토크형', '스토리텔링형', '리뷰형']):
        rep += 20
        rep_trace.append(f"content_structure={content_struct} +20")
    elif '브이로그형' in content_struct:
        rep += 5
        rep_trace.append("content_structure=브이로그형 +5")
    if _contains_any(combined, ['토크', '수다', '게스트', '부부', '패션', '뷰티',
                                '추천', '템', '관리', '스타일', '코디']):
        rep += 18
        rep_trace.append("series keyword +18")

    repeatability_score = _clamp(rep)
    trace['repeatability'] = rep_trace

    # ── 4. novelty_score ─────────────────────────────────────────
    nov = 35
    nov_trace = ["기본값 35"]

    for field, value, rules in (
        ('content_structure', content_struct, (
            ('토크형', 20), ('스토리텔링형', 10), ('브이로그형', -4), ('정보전달형', -8),
        )),
        ('title_pattern', title_pattern, (('단정형', 12), ('감정형', 5), ('숫자형', -8))),
        ('title_emotion_tone', title_emotion, (('놀람', 12), ('친근함', 4), ('유머', 4), ('공감', -10))),
    ):
        for label, points in rules:
            if label in value:
                nov += points
                nov_trace.append(f"{field}={label} {points:+d}")
                break

    if unique_diff and '없음' not in unique_diff and len(unique_diff) > 2:
        nov += 12
        nov_trace.append("unique_differentiator 존재 +12")
    if viral_signals and '없음' not in viral_signals and len(viral_signals) > 2:
        nov += 10
        nov_trace.append("viral_signals 존재 +10")
    if _contains_any(hook_strategy, ['반전', '최초', '의외', '진짜', '솔직', '공개', '비밀', '충격', '고백']):
        nov += 8
        nov_trace.append("hook keyword +8")
    if desire >= 20:
        nov += 10
        nov_trace.append("envy/aspiration >= 20 +10")
    elif desire >= 10:
        nov += 5
        nov_trace.append("envy/aspiration >= 10 +5")
    if fatigue >= 15:
        nov -= 10
        nov_trace.append("fatigue/cringe >= 15 -10")

    if isinstance(thumbnail, dict):
        face_count = _float_or_zero(thumbnail.get('face_count'))
        for label, matched, points in (
            ('couple_present', thumbnail.get('couple_present') is True, 12),
            ('face_count=2', face_count == 2, 10),
            ('face_count>=3', face_count >= 3, 4),
            ('face_count=1', face_count == 1, -6),
            ('main_emotion=놀람', '놀람' in str(thumbnail.get('main_emotion') or ''), 8),
            ('luxury_signal', thumbnail.get('luxury_signal') is True, 6),
            ('home_visible=False', thumbnail.get('home_visible') is False, 6),
            ('food_present', thumbnail.get('food_present') is True, -12),
        ):
            if matched:
                nov += points
                nov_trace.append(f"thumbnail.{label} {points:+d}")

    novelty_score = _clamp(nov)
    trace['novelty'] = nov_trace

    # ── 5. risk_score ────────────────────────────────────────────
    risk = 10
    risk_trace = ["기본값 10"]

    if ad_boost_suspected:
        risk += 35
        risk_trace.append("ad_boost_suspected +35")
    if ppl_likely:
        risk += 12
        risk_trace.append("ppl_likely +12")
    if fatigue >= 20:
        risk += 15
        risk_trace.append("fatigue/cringe >= 20 +15")
    if _contains_any(combined, ['논란', '폭로', '정치', '사건', '사고', '루머',
                                '의학', '투자', '법률', '처방', '진단']):
        risk += 35
        risk_trace.append("brand risk keyword +35")

    risk_score = _clamp(risk)
    trace['risk'] = risk_trace

    # ── 6. ppl_potential_score ───────────────────────────────────
    ppl_trace = []
    ppl = 20
    ppl_trace.append("기본값 20")

    if ppl_likely:
        ppl += 40
        ppl_trace.append("ppl_likely +40")
    if _contains_any(category, ['요리', '패션', '뷰티', '라이프스타일']):
        ppl += 25
        ppl_trace.append(f"category={category} +25")
    if _contains_any(combined, ['템', '추천', '리뷰', '주방', '관리', '건강', '제품', '착용', '사용']):
        ppl += 25
        ppl_trace.append("ppl keyword +25")
    if risk_score >= 60:
        ppl -= 20
        ppl_trace.append(f"high risk({risk_score}) penalty -20")

    ppl_potential_score = _clamp(ppl)
    trace['ppl_potential'] = ppl_trace

    # ── 7. content_lifespan_type ─────────────────────────────────
    fast_kws     = ['사건', '논란', '근황', '챌린지', '유행', '밈', '이슈', '최초공개']
    evergreen_kws = ['살림', '요리', '레시피', '정리', '건강', '가족', '부부', '주방', '루틴', '관리', '집밥']

    if _contains_any(combined, fast_kws):
        content_lifespan_type = 'FAST_TREND'
        trace['content_lifespan_type'] = [f"FAST_TREND keyword matched in: {combined[:60]}"]
    elif _contains_any(combined, evergreen_kws):
        content_lifespan_type = 'EVERGREEN'
        trace['content_lifespan_type'] = [f"EVERGREEN keyword matched in: {combined[:60]}"]
    else:
        content_lifespan_type = 'MID_TERM'
        trace['content_lifespan_type'] = ["strong keyword 없음 → MID_TERM"]

    # ── 8. trend_lifespan_score ──────────────────────────────────
    trend_lifespan_score = {'FAST_TREND': 30, 'MID_TERM': 60, 'EVERGREEN': 90}[content_lifespan_type]
    trace['trend_lifespan'] = [f"{content_lifespan_type} {trend_lifespan_score}"]

    # ── 9. upload_delay_risk ─────────────────────────────────────
    upload_delay_risk = {'FAST_TREND': 80, 'MID_TERM': 40, 'EVERGREEN': 5}[content_lifespan_type]
    trace['upload_delay_risk'] = [f"{content_lifespan_type} {upload_delay_risk}"]

    # ── 10. evergreen_score ──────────────────────────────────────
    eg = {'EVERGREEN': 90, 'MID_TERM': 55, 'FAST_TREND': 25}[content_lifespan_type]
    trace['evergreen'] = [f"{content_lifespan_type} {eg}"]
    if _contains_any(combined, ['요리', '살림', '건강', '가족']):
        eg += 10
        trace['evergreen'].append("evergreen keyword +10")
    evergreen_score = _clamp(eg)

    # ── 11. priority_score ───────────────────────────────────────
    subscores = {
        'hangoeun_fit_score':   hangoeun_fit_score,
        'execution_score':      execution_score,
        'repeatability_score':  repeatability_score,
        'novelty_score':        novelty_score,
        'risk_score':           risk_score,
        'ppl_potential_score':  ppl_potential_score,
        'trend_lifespan_score': trend_lifespan_score,
        'upload_delay_risk':    upload_delay_risk,
        'evergreen_score':      evergreen_score,
    }
    raw = sum(subscores[k] * w for k, w in PRIORITY_WEIGHTS.items())
    priority_score = _clamp(raw)

    # ── 12. recommended_action ───────────────────────────────────
    if priority_score >= 75:
        recommended_action = '바로 기획화'
    elif priority_score >= 60:
        recommended_action = '각색 후 기획'
    elif priority_score >= 48:
        recommended_action = '아이디어 보관'
    else:
        recommended_action = '우선순위 낮음'

    # ── 13. strategy_reason ──────────────────────────────────────
    parts = []
    if hangoeun_fit_score >= 70:
        parts.append("한고은 채널 적합도가 높고")
    elif hangoeun_fit_score <= 30:
        parts.append("한고은 채널 적합도가 낮고")

    if content_lifespan_type == 'EVERGREEN':
        parts.append("에버그린 성격이 강해 장기 기획 후보입니다")
    elif upload_delay_risk >= 70:
        parts.append("업로드 지연 리스크가 높아 빠른 실행이 필요합니다")

    if risk_score >= 60:
        parts.append("브랜드 리스크가 높아 각색이 필요합니다")
    elif repeatability_score >= 70:
        parts.append("반복·시리즈화 가능성이 높습니다")

    if parts:
        strategy_reason = ". ".join(parts) + "."
    else:
        strategy_reason = f"우선순위 점수 {priority_score}점 기준 {recommended_action} 판정."

    return {
        'video_id':             video_id,
        'score_version':        config.SCORE_VERSION,
        'source_table':         source_table,
        **subscores,
        'content_lifespan_type': content_lifespan_type,
        'priority_score':       priority_score,
        'recommended_action':   recommended_action,
        'strategy_reason':      strategy_reason,
        'rule_trace':           trace,
    }


def score_batch(
    videos_map: dict, analyses: list, source_table: str, thumbnails_map: dict | None = None,
) -> list:
    """
    여러 분석 결과 일괄 스코어링
    videos_map: {video_id: video_dict}
    thumbnails_map: 선택적 {video_id: thumbnail_dict}
    """
    results = []
    thumbnails_map = thumbnails_map or {}
    for a in analyses:
        vid = a.get('video_id', '')
        video = videos_map.get(vid, {})
        try:
            results.append(score_video(video, a, source_table, thumbnail=thumbnails_map.get(vid)))
        except Exception as e:
            print(f"  ⚠️ 스코어링 실패 ({vid}): {safe_error(e)}")
    return results
