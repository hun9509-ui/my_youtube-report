"""
전략 스코어링 모듈 v1.2

기존 Gemini/DeepSeek 분석 결과를 입력으로 받아
순수 룰 기반 전략 점수를 산출한다.
API 호출 없음. 결정론적.
"""
import config


def _clamp(v, lo: int = 0, hi: int = 100) -> int:
    return max(lo, min(hi, int(round(v))))


def _contains_any(text: str, keywords: list) -> bool:
    t = str(text).lower()
    return any(k in t for k in keywords)


def score_video(video: dict, analysis: dict, source_table: str) -> dict:
    """
    video       : videos 테이블 기반 원본 영상 데이터
    analysis    : initial_analysis 또는 daily_analysis 기반 분석 데이터
    source_table: 'initial_analysis' | 'daily_analysis'
    """
    video_id = video.get('video_id') or analysis.get('video_id', '')

    # Supabase JSONB 또는 직접 dict 모두 처리
    gemini = analysis.get('gemini_raw') or analysis.get('gemini') or {}
    comments = analysis.get('deepseek_raw') or analysis.get('comments') or {}

    category        = str(gemini.get('category')        or analysis.get('category', '')).lower()
    topic           = str(gemini.get('topic')            or analysis.get('topic', '')).lower()
    title           = str(video.get('title')             or analysis.get('title', '')).lower()
    title_pattern   = str(gemini.get('title_pattern')    or analysis.get('title_pattern', '')).lower()
    content_struct  = str(gemini.get('content_structure') or analysis.get('content_structure', '')).lower()
    hook_strategy   = str(gemini.get('hook_strategy')    or analysis.get('hook_strategy', '')).lower()
    unique_diff     = str(gemini.get('unique_differentiator') or '').lower()
    applicability   = str(gemini.get('applicability')    or analysis.get('applicability', '')).lower()
    viral_signals   = str(comments.get('viral_signals')  or '').lower()

    # ppl_likely: bool 또는 문자열 둘 다 허용
    ppl_raw = gemini.get('ppl_likely') or analysis.get('ppl_likely', False)
    if isinstance(ppl_raw, str):
        ppl_likely = ppl_raw.lower() in ('true', 'yes', '예', '1')
    else:
        ppl_likely = bool(ppl_raw)

    combined = f"{title} {topic} {category}"
    trace = {}

    # ── 1. hangoeun_fit_score ──────────────────────────────────────
    fit_trace = []
    if '상' in applicability:
        fit_base = 80
        fit_trace.append("applicability=상 +80")
    elif '중' in applicability:
        fit_base = 55
        fit_trace.append("applicability=중 +55")
    else:
        fit_base = 20
        fit_trace.append("applicability=하/미확인 +20")

    fit_bonus = 0
    if _contains_any(category, ['요리', '라이프스타일', '일상']):
        fit_bonus += 10
        fit_trace.append(f"category={category} +10")
    if _contains_any(combined, ['살림', '주방', '정리', '집밥', '부부', '가족', '엄마', '갱년기', '건강']):
        fit_bonus += 10
        fit_trace.append("evergreen keyword +10")
    if _contains_any(combined, ['전문 메이크업', '정치', '게임', '자극', '논란']):
        fit_bonus -= 20
        fit_trace.append("negative keyword -20")

    hangoeun_fit_score = _clamp(fit_base + fit_bonus)
    trace['hangoeun_fit'] = fit_trace

    # ── 2. execution_score ────────────────────────────────────────
    exec_trace = []
    exec_map = {
        '토크형': 85, '정보전달형': 75, '브이로그형': 70,
        '리뷰형': 60, '스토리텔링형': 55,
    }
    exec_base = 65
    for k, v in exec_map.items():
        if k in content_struct:
            exec_base = v
            exec_trace.append(f"content_structure={k} {v}")
            break
    else:
        exec_trace.append(f"content_structure 미확인 기본값 {exec_base}")

    exec_bonus = 0
    if ppl_likely:
        exec_bonus -= 10
        exec_trace.append("ppl_likely -10")
    if _contains_any(combined, ['여행', '해외', '야외 촬영', '대형 게스트']):
        exec_bonus -= 15
        exec_trace.append("travel/outdoor -15")
    if _contains_any(combined, ['집', '주방', '일상', '정리', '요리']):
        exec_bonus += 10
        exec_trace.append("home/kitchen +10")

    execution_score = _clamp(exec_base + exec_bonus)
    trace['execution'] = exec_trace

    # ── 3. repeatability_score ───────────────────────────────────
    rep_trace = []
    rep = 40
    rep_trace.append("기본값 40")

    if '숫자형' in title_pattern:
        rep += 20
        rep_trace.append("숫자형 제목 +20")
    if _contains_any(content_struct, ['정보전달형', '리뷰형', '토크형']):
        rep += 25
        rep_trace.append(f"content_structure={content_struct} +25")
    if _contains_any(combined, ['루틴', '정리', '템', '레시피', '비법', '살림', '관리', '추천', '비교']):
        rep += 25
        rep_trace.append("series keyword +25")
    if _contains_any(combined, ['단발', '사건', '이슈', '근황', '고백', '최초공개', '충격']):
        rep -= 25
        rep_trace.append("one-off keyword -25")

    repeatability_score = _clamp(rep)
    trace['repeatability'] = rep_trace

    # ── 4. novelty_score ─────────────────────────────────────────
    nov_trace = []
    nov = 35
    nov_trace.append("기본값 35")

    if unique_diff and '없음' not in unique_diff and len(unique_diff) > 2:
        nov += 35
        nov_trace.append("unique_differentiator 존재 +35")
    if viral_signals and '없음' not in viral_signals and len(viral_signals) > 2:
        nov += 25
        nov_trace.append("viral_signals 존재 +25")
    if _contains_any(hook_strategy, ['반전', '최초', '의외', '진짜', '솔직', '공개', '비밀']):
        nov += 20
        nov_trace.append("hook keyword +20")
    if _contains_any(combined, ['일상', '브이로그', '먹방']) and not unique_diff:
        nov -= 10
        nov_trace.append("generic keyword (차별화 없음) -10")

    novelty_score = _clamp(nov)
    trace['novelty'] = nov_trace

    # ── 5. risk_score ────────────────────────────────────────────
    risk_trace = []
    risk = 10
    risk_trace.append("기본값 10")

    if ppl_likely:
        risk += 20
        risk_trace.append("ppl_likely +20")
    if _contains_any(combined, ['논란', '충격', '폭로', '갈등', '이혼', '불화', '정치', '사건', '사고', '루머']):
        risk += 40
        risk_trace.append("controversy keyword +40")
    if _contains_any(combined, ['의학', '투자', '법률', '처방', '진단']):
        risk += 25
        risk_trace.append("expert advice keyword +25")
    if _contains_any(combined, ['경악', '멘붕', '결국 눈물', '최악']):
        risk += 30
        risk_trace.append("brand risk keyword +30")

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

    # ── 9. upload_delay_risk ─────────────────────────────────────
    upload_delay_risk = {'FAST_TREND': 80, 'MID_TERM': 40, 'EVERGREEN': 5}[content_lifespan_type]

    # ── 10. evergreen_score ──────────────────────────────────────
    eg = {'EVERGREEN': 90, 'MID_TERM': 55, 'FAST_TREND': 25}[content_lifespan_type]
    if _contains_any(combined, ['요리', '살림', '건강', '가족']):
        eg = min(100, eg + 10)
    evergreen_score = _clamp(eg)

    # ── 11. priority_score ───────────────────────────────────────
    raw = (
        hangoeun_fit_score  * 0.30
        + repeatability_score * 0.18
        + evergreen_score     * 0.15
        + novelty_score       * 0.12
        + ppl_potential_score * 0.10
        + trend_lifespan_score * 0.08
        + execution_score     * 0.07
        - risk_score          * 0.15
        - upload_delay_risk   * 0.10
    )
    priority_score = _clamp(round(raw))

    # ── 12. recommended_action ───────────────────────────────────
    if priority_score >= 80:
        recommended_action = '바로 기획화'
    elif priority_score >= 65:
        recommended_action = '각색 후 기획'
    elif priority_score >= 50:
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
        'hangoeun_fit_score':   hangoeun_fit_score,
        'execution_score':      execution_score,
        'repeatability_score':  repeatability_score,
        'novelty_score':        novelty_score,
        'risk_score':           risk_score,
        'ppl_potential_score':  ppl_potential_score,
        'trend_lifespan_score': trend_lifespan_score,
        'upload_delay_risk':    upload_delay_risk,
        'evergreen_score':      evergreen_score,
        'content_lifespan_type': content_lifespan_type,
        'priority_score':       priority_score,
        'recommended_action':   recommended_action,
        'strategy_reason':      strategy_reason,
        'rule_trace':           trace,
    }


def score_batch(videos_map: dict, analyses: list, source_table: str) -> list:
    """
    여러 분석 결과 일괄 스코어링
    videos_map: {video_id: video_dict}
    """
    results = []
    for a in analyses:
        vid = a.get('video_id', '')
        video = videos_map.get(vid, {})
        try:
            results.append(score_video(video, a, source_table))
        except Exception as e:
            print(f"  ⚠️ 스코어링 실패 ({vid}): {e}")
    return results
