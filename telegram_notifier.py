"""
텔레그램 봇으로 알림 전송하는 모듈
"""
import re
import time
from html import escape, unescape

import requests
import config
from gemini_client import safe_error


def send_message(text, parse_mode="HTML"):
    """Bot API가 발송 ID를 반환한 경우만 성공으로 기록한다."""
    if not config.TELEGRAM_BOT_TOKEN or not config.TELEGRAM_CHAT_ID:
        print("⚠️ 텔레그램 설정 누락")
        return False
    url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage"

    if len(text) > 4000:
        if parse_mode == "HTML":
            text = unescape(re.sub(r"</?b>", "", text))
            parse_mode = None
        text = text[:4000] + "...\n(내용이 길어 일부 생략됨)"

    payload = {
        "chat_id": config.TELEGRAM_CHAT_ID,
        "text": text,
        "link_preview_options": {"is_disabled": True},
    }
    if parse_mode:
        payload["parse_mode"] = parse_mode
    for attempt in range(2):
        try:
            response = requests.post(url, json=payload, timeout=30)
            body = response.json()
        except (requests.RequestException, ValueError) as exc:
            print(f"⚠️ 텔레그램 발송 확인 실패: {safe_error(exc)}")
            return False
        if not isinstance(body, dict):
            print(f"⚠️ 텔레그램 응답 형식 오류 (HTTP {response.status_code})")
            return False
        result = body.get("result")
        message_id = result.get("message_id") if isinstance(result, dict) else None
        if response.ok and body.get("ok") is True and isinstance(message_id, int):
            print(f"  📨 텔레그램 발송 확인 (message_id={message_id})")
            return True
        description = str(body.get("description") or "응답에 발송 ID 누락")
        if attempt == 0 and response.status_code == 429:
            delay = (body.get("parameters") or {}).get("retry_after")
            if isinstance(delay, int) and 0 <= delay <= 30:
                time.sleep(delay)
                continue
        if (attempt == 0 and response.status_code == 400 and parse_mode == "HTML"
                and "can't parse entities" in description.lower()):
            payload.pop("parse_mode")
            payload["text"] = unescape(re.sub(r"</?b>", "", text))
            continue
        print(f"⚠️ 텔레그램 발송 실패 (HTTP {response.status_code}): {safe_error(description)}")
        return False


def send_daily_trend_alert(trend_summary):
    """일일 트렌드 알림 (매일 오전 10시)"""
    if 'error' in trend_summary:
        return send_message(f"⚠️ 오늘 트렌드 분석 실패: {trend_summary['error']}")
    
    keywords = trend_summary.get('top_5_keywords', [])
    keywords_text = ""
    for i, kw in enumerate(keywords, 1):
        if isinstance(kw, dict):
            keywords_text += f"{i}. <b>{kw.get('keyword', '')}</b> - {kw.get('reason', '')}\n"
        else:
            keywords_text += f"{i}. {kw}\n"
    
    message = f"""
🌅 <b>오늘의 유튜브 트렌드</b>
━━━━━━━━━━━━━━━━━━

📌 <b>핵심 트렌드</b>
{trend_summary.get('headline', '')}

🔥 <b>TOP 5 키워드</b>
{keywords_text}

💡 <b>한고은 채널 적용 아이디어</b>
{trend_summary.get('hangoeun_action', '')}

👀 <b>주목할 영상</b>
{trend_summary.get('watch_out', '')}

━━━━━━━━━━━━━━━━━━
📊 자세한 내용은 구글 시트에서 확인
"""
    return send_message(message.strip())


def send_ad_boost_summary(summary: list):
    """채널별 광고 부스팅 의심 현황 알림"""
    if not summary:
        return send_message("📢 광고 분석: 데이터 없음")

    msg = "📢 <b>채널별 광고 부스팅 의심 현황</b>\n━━━━━━━━━━━━━━━━━━\n\n"
    for s in summary:
        rate = s.get('ad_rate_pct', 0)
        icon = "🔴" if rate >= 50 else "🟡" if rate >= 20 else "🟢"
        msg += (
            f"{icon} <b>{s.get('channel_title', '')}</b>\n"
            f"   광고 의심 {s.get('ad_suspected_count', 0)}/{s.get('total_videos', 0)}개 "
            f"({rate}%) | 평균 점수 {s.get('avg_ad_score', 0)}\n"
        )
    msg += "\n🟢 20% 미만: 유기 성장  🟡 20~50%: 부분 광고  🔴 50%+: 광고 의존"
    return send_message(msg)


def send_weekly_report_alert(report, top_priority=None, market_state=None, market_missing=False):
    """주간 리포트 알림 (매주 월요일 오전 10시)"""
    if 'error' in report:
        return send_message(f"⚠️ 주간 리포트 생성 실패: {report['error']}")
    
    insights = report.get('actionable_insights_for_hangoeun', [])
    insights_text = "\n".join(f"  ▸ {escape(str(ins))}" for ins in insights[:5])
    
    suggestions = report.get('next_video_suggestions', [])
    suggestions_text = "\n".join(f"  ▸ {escape(str(s))}" for s in suggestions[:3])
    
    rising = report.get('rising_topics', [])
    rising_text = ", ".join(map(str, rising[:5])) if isinstance(rising, list) else str(rising)
    
    top_channels = report.get('top_performing_channels', [])
    top_channels_text = "\n".join(f"  • {c}" for c in top_channels[:3]) if isinstance(top_channels, list) else str(top_channels)
    
    top5_text = ""
    if top_priority:
        top5_text = "\n\n⭐ <b>이번 주 기획 우선순위 TOP 5</b>\n"
        for i, v in enumerate(top_priority[:5], 1):
            title = v.get('title', '')[:35]
            channel = v.get('channel_title', '')
            score = v.get('priority_score', 0)
            action = v.get('recommended_action', '')
            reason = v.get('strategy_reason', '')[:80]
            top5_text += f"  {i}. [{escape(channel)}] {escape(title)} — {score}점 | {escape(action)}\n"
            if reason:
                top5_text += f"     → {escape(reason)}\n"

    # 시장 변화 분석 섹션
    market_section = "\n\n⚠️ 시장 변화 분석 미완료" if market_missing else ""
    if market_state and 'error' not in market_state:
        dominant = market_state.get('dominant_emotion', '')
        gap = market_state.get('competitive_gap', [])
        opportunity = market_state.get('hangoeun_opportunity_score', 0)
        recommended = market_state.get('hangoeun_recommended_topics', [])
        fatigue = market_state.get('viewer_fatigue_signals', [])
        rec_text = "\n".join(f"  ▸ {escape(str(t))}" for t in recommended[:3])
        gap_text = "\n".join(f"  ▸ {escape(str(g))}" for g in gap[:2])
        fatigue_text = ", ".join(map(str, fatigue[:2])) if fatigue else "없음"
        market_section = f"""

🧠 <b>시장 변화 분석</b>
이번 주 감정: <b>{escape(str(dominant))}</b> | 기회점수: <b>{escape(str(opportunity))}점</b>
시청자 피로: {escape(fatigue_text)}

🎯 <b>한고은 기회 주제 (30일 후 업로드 기준)</b>
{rec_text}

🔍 <b>경쟁 공백</b>
{gap_text}
"""

    message = f"""
📊 <b>주간 경쟁 채널 리포트</b>
━━━━━━━━━━━━━━━━━━

📝 <b>이번 주 요약</b>
{escape(report.get('week_summary', ''))}

🏆 <b>잘 나가는 채널</b>
{escape(top_channels_text)}

🚀 <b>떠오르는 주제</b>
{escape(rising_text)}

🎬 <b>포맷 트렌드</b>
{escape(', '.join(map(str, report.get('format_trends', [])[:3]))) if isinstance(report.get('format_trends'), list) else ''}

💼 <b>PPL 관찰</b>
{escape(str(report.get('ppl_observations', '')))}

✨ <b>한고은 채널 액션 아이템</b>
{insights_text}

🎯 <b>다음 영상 방향성</b>
{suggestions_text}{market_section}{top5_text}
━━━━━━━━━━━━━━━━━━
📊 전체 데이터는 구글 시트에서 확인
"""
    return send_message(message.strip())


def send_collection_summary(stats):
    """일일 데이터 수집 완료 알림 (00시)"""
    message = f"""
🌙 <b>밤 12시 자동 수집 완료</b>
━━━━━━━━━━━━━━━━━━

📺 신규 영상: {stats.get('new_videos', 0)}개
🔍 분석 완료: {stats.get('analyzed', 0)}개
💬 댓글 분석: {stats.get('comments_analyzed', 0)}개
⏱️ 소요 시간: {stats.get('duration', 'N/A')}

{f"⚠️ 오류: {stats['errors']}개" if stats.get('errors', 0) > 0 else "✅ 모든 작업 정상 완료"}
"""
    return send_message(message.strip())


def send_error_alert(error_msg, context=""):
    """오류 발생 시 알림"""
    message = f"""
🚨 <b>시스템 오류 발생</b>
━━━━━━━━━━━━━━━━━━

위치: {escape(str(context))}
오류: {escape(safe_error(error_msg))}

실행 로그를 확인해주세요.
"""
    return send_message(message.strip())

def send_irregular_alert(video: dict):
    """이레귤러 영상 즉시 알림"""
    reasons = video.get('irregular_reasons', [])
    metrics = video.get('irregular_metrics', {})
    matched = video.get('matched_keywords', [])
 
    msg = (
        f"⚡ <b>이레귤러 감지!</b>\n\n"
        f"📺 <b>{escape(video.get('title', ''))}</b>\n"
        f"📻 채널: {escape(video.get('channel_title', ''))}\n\n"
        f"📊 <b>지표</b>\n"
    )
 
    for r in reasons:
        msg += f"  • {r}\n"
 
    if metrics:
        msg += (
            f"\n⏰ 업로드 후: {metrics.get('hours_since_upload', 0):.1f}시간\n"
            f"👁 총 조회수: {metrics.get('view_count', 0):,}\n"
            f"📈 시간당: {metrics.get('views_per_hour', 0):,}회\n"
        )
 
    if matched:
        msg += f"\n🏷 키워드: {', '.join(matched[:5])}\n"
 
    msg += f"\n🔗 https://www.youtube.com/watch?v={video.get('video_id', '')}"
 
    return send_message(msg)
 
 
def send_own_channel_alert(video: dict, gemini: dict, comments: dict):
    """자체 채널 새 영상 분석 알림"""
    g = gemini or {}
    c = comments or {}
    emoji = "📱" if video.get('video_type') == 'shorts' else "🎬"
    vtype = '숏츠' if video.get('video_type') == 'shorts' else '롱폼'

    msg = (
        f"{emoji} <b>[자체채널 {vtype}] 새 영상 분석</b>\n\n"
        f"📌 <b>{video.get('title', '')}</b>\n"
        f"📅 업로드: {str(video.get('published_at', ''))[:10]}\n"
        f"👁 초기 조회수: {video.get('view_count', 0):,}회\n\n"
        f"🎯 <b>콘텐츠 분석</b>\n"
        f"  주제: {g.get('topic', '')}\n"
        f"  구조: {g.get('content_structure', '')}\n"
        f"  성과 예측: {g.get('predicted_performance', '')} — {g.get('predicted_performance_reason', '')[:80]}\n"
        f"  강점: {g.get('strengths', '')[:120]}\n"
        f"  개선: {g.get('improvement_points', '')[:120]}\n"
    )

    if g.get('thumbnail_suggestion') and g.get('thumbnail_suggestion') != '없음':
        msg += f"  썸네일: {g.get('thumbnail_suggestion', '')[:80]}\n"

    if c and 'summary' in c:
        msg += (
            f"\n💬 <b>댓글 여론</b>\n"
            f"  {c.get('summary', '')}\n"
            f"  피드백: {c.get('creator_feedback', '')[:150]}\n"
        )
        reqs = c.get('next_video_requests', [])
        if reqs:
            msg += "\n📝 <b>다음 영상 요청</b>\n"
            for req in reqs[:3]:
                msg += f"  • {req}\n"

    msg += f"\n🔗 {video.get('video_url', '')}"
    send_message(msg)


def send_own_tracking_summary(results: list):
    """매주 월요일 자체 채널 추적 요약 알림"""
    if not results:
        send_message("📺 자체 채널 추적: 이번 주 추적 영상 없음")
        return

    msg = "📊 <b>자체 채널 주간 추적</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
    for r in results:
        emoji = "📱" if r.get('video_type') == 'shorts' else "🎬"
        growth = r.get('view_growth', 0)
        growth_str = f"+{growth:,}" if growth >= 0 else f"{growth:,}"
        week = r.get('week_number', '')
        done = " ✅완료" if week >= 5 else ""
        msg += (
            f"{emoji} <b>Week {week}{done}</b> | {r.get('title', '')[:35]}\n"
            f"  누적 {r.get('view_count', 0):,}회  이번 주 {growth_str}회\n\n"
        )
    send_message(msg)


def send_weekly_trend_alert(weekly_data: dict):
    """월요일 주간 트렌드 종합 알림 (트랙 A + 트랙 B 투트랙)"""
    track_a = weekly_data.get('track_a', [])
    track_b = weekly_data.get('track_b', [])
    event_kws = weekly_data.get('event_keywords', [])
    days = weekly_data.get('period_days', 7)
 
    msg = f"📊 <b>주간 트렌드 분석</b> (지난 {days}일)\n"
    msg += "━━━━━━━━━━━━━━━━━━━━\n\n"
 
    # 사건 키워드
    if event_kws:
        msg += "🔑 <b>이번 주 핵심 키워드</b>\n"
        msg += " ".join(f"#{escape(k)}" for k in event_kws[:8])
        msg += "\n\n"
 
    # 트랙 A
    msg += "🌸 <b>트랙 A | 30~60대 여성</b>\n"
    if track_a:
        for i, v in enumerate(track_a[:5], 1):
            title = v.get('title', '')[:35]
            views = v.get('view_count', 0)
            msg += f"  {i}. [{escape(v.get('channel_title',''))}] {escape(title)}\n"
            msg += f"     조회수 {views:,}회\n"
    else:
        msg += "  (해당 영상 없음)\n"
    msg += "\n"
 
    # 트랙 B
    msg += "🔥 <b>트랙 B | 10~30대 트렌드</b>\n"
    if track_b:
        for i, v in enumerate(track_b[:5], 1):
            title = v.get('title', '')[:35]
            views = v.get('view_count', 0)
            msg += f"  {i}. [{escape(v.get('channel_title',''))}] {escape(title)}\n"
            msg += f"     조회수 {views:,}회\n"
    else:
        msg += "  (해당 영상 없음)\n"
 
    return send_message(msg)
