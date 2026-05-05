"""
한고은 채널 분석 시스템 - 메인 실행 파일

실행 모드:
  python main.py initial-batch [1~5]  : 배치별 초기 수집
  python main.py daily                : 평일 새 영상 분석
  python main.py trend-collect        : 매일 트렌드 수집 + 이레귤러 즉시 알림
  python main.py trend-weekly         : 월요일 주간 트렌드 종합 알림
  python main.py weekly               : 주간 채널 리포트
"""
import sys
sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

print("=" * 60, flush=True)
print("🎬 한고은 채널 분석 시스템 시작", flush=True)
print("=" * 60, flush=True)

import time
import traceback
from datetime import datetime, timedelta, timezone

print("⏳ 모듈 import 중...", flush=True)
import config;                  print("  ✓ config", flush=True)
import youtube_collector as yt; print("  ✓ youtube_collector", flush=True)
import gemini_analyzer as gemini; print("  ✓ gemini_analyzer", flush=True)
import deepseek_processor as ds; print("  ✓ deepseek_processor", flush=True)
import sheets_writer as sheets; print("  ✓ sheets_writer", flush=True)
import supabase_writer as db;   print("  ✓ supabase_writer", flush=True)
import telegram_notifier as tg; print("  ✓ telegram_notifier", flush=True)
import trend_classifier as tc;  print("  ✓ trend_classifier", flush=True)

# ── 환경변수 체크 ──────────────────────────────
print("\n⏳ 환경변수 체크 중...", flush=True)
required = {
    'YOUTUBE_API_KEY':    config.YOUTUBE_API_KEY,
    'DEEPSEEK_API_KEY':   config.DEEPSEEK_API_KEY,
    'TELEGRAM_BOT_TOKEN': config.TELEGRAM_BOT_TOKEN,
    'TELEGRAM_CHAT_ID':   config.TELEGRAM_CHAT_ID,
    'GOOGLE_WEBHOOK_URL': config.GOOGLE_WEBHOOK_URL,
}
missing = [k for k, v in required.items() if not v]
if missing:
    print(f"❌ 누락된 환경변수: {missing}", flush=True)
    sys.exit(1)

if config.SUPABASE_URL and config.SUPABASE_KEY:
    print("  ✓ 모든 환경변수 정상 (Supabase 포함)", flush=True)
else:
    print("  ⚠️ Supabase 미설정 → 시트만 저장됩니다", flush=True)


# ───────────────────────────────────────────────
# 채널 수집 헬퍼
# ───────────────────────────────────────────────

def collect_channel(channel_name: str, months_back: int = 6) -> list[dict]:
    """채널명 → config의 하드코딩 ID로 영상 수집 (search.list 사용 안 함)"""
    print(f"\n{'='*60}")
    print(f"📺 채널: {channel_name}")
    print(f"{'='*60}")

    channel_id = config.TARGET_CHANNELS.get(channel_name)
    if not channel_id:
        print(f"❌ config에서 채널 ID 없음: {channel_name}")
        return []

    print(f"✅ 채널 ID: {channel_id}")
    
    # 1. 포장지 분해: 영상 목록(raw_videos)과 채널 정보(channel_info)를 따로 받기
    raw_videos, channel_info = yt.get_channel_videos(channel_id, months_back=months_back)
    
    if not raw_videos:
        print("📹 수집된 기본 영상 없음")
        return []
        
    # 2. 영상 ID만 추출해서 '조회수, 길이' 등 상세 정보 가져오기
    video_ids = [v['video_id'] for v in raw_videos]
    detailed_videos = yt.get_video_details(video_ids)
    
    print(f"📹 영상 수집: {len(detailed_videos)}개 (숏폼/5분 미만 제외)")
    return detailed_videos


def analyze_video_complete(video: dict, gemini_result: dict) -> dict:
    """영상 1개 완전 분석. comment_count 0이면 댓글 분석 skip."""
    comment_count = video.get("comment_count", 0)

    if comment_count == 0:
        comments_analysis = {"skipped": "댓글 없음"}
    else:
        try:
            comments = yt.get_video_comments(
                video["video_id"],
                max_comments=config.TOP_COMMENTS_COUNT
            )
            if comments:
                comments_analysis = ds.analyze_comments(video["title"], comments)
            else:
                comments_analysis = {"skipped": "댓글 수집 결과 없음"}
        except Exception as e:
            print(f"  ⚠️ 댓글 분석 실패: {e}")
            comments_analysis = {"error": str(e)}

    return {
        "video_id": video["video_id"],
        "gemini":   gemini_result,
        "comments": comments_analysis,
    }


def _save_analyses(analyses: list[dict], mode: str, batch_num: int = 0):
    if mode == "initial":
        sheets.save_initial_analysis(analyses, batch_num)
        db.save_initial_analysis(analyses, batch_num)
    else:
        sheets.save_daily_analysis(analyses)
        db.save_daily_analysis(analyses)


# ───────────────────────────────────────────────
# 모드 1: 초기 배치 수집
# ───────────────────────────────────────────────

def run_initial_batch(batch_num: int):
    print(f"\n🚀 초기 배치 #{batch_num} 시작 ({datetime.now()})")
    start = time.time()

    if batch_num not in config.INITIAL_BATCHES:
        print(f"❌ 잘못된 배치 번호: {batch_num} (1~5)")
        return

    channels = config.INITIAL_BATCHES[batch_num]
    print(f"📋 대상 채널: {channels}")

    # 재실행 시 기존 데이터 삭제
    print(f"🗑️ 기존 배치#{batch_num} 데이터 초기화 중...")
    db.delete_initial_batch(batch_num)

    all_videos: list[dict] = []
    channel_videos_map: dict[str, list[dict]] = {}

    for name in channels:
        try:
            videos = collect_channel(name, months_back=config.INITIAL_MONTHS)
            all_videos.extend(videos)
            channel_videos_map[name] = videos
            time.sleep(2)
        except Exception as e:
            print(f"❌ 채널 실패 ({name}): {e}")
            traceback.print_exc()
            tg.send_error_alert(str(e), f"배치#{batch_num} - {name}")

    print(f"\n📊 배치#{batch_num} 총 영상: {len(all_videos)}개")
    if not all_videos:
        tg.send_message(f"⚠️ 배치#{batch_num}: 분석할 영상 없음")
        return

    # 채널별 평균 조회수 계산 → 대박 영상 태그
    channel_avg: dict[str, float] = {}
    for name, vids in channel_videos_map.items():
        if vids:
            avg = sum(v.get('view_count', 0) for v in vids) / len(vids)
            channel_avg[name] = avg
            yt_title = vids[0].get('channel_title', '')
            if yt_title:
                channel_avg[yt_title] = avg
            print(f"  📊 {name} 평균 조회수: {int(avg):,}회")

    hit_count = 0
    for v in all_videos:
        avg = channel_avg.get(v.get('channel_title', ''), 0)
        v['channel_avg_views'] = int(avg)
        v['is_hit'] = avg > 0 and v.get('view_count', 0) >= avg * config.HIT_VIDEO_MULTIPLIER
        if v['is_hit']:
            hit_count += 1
    print(f"  ⭐ 대박 영상 (평균 ×{config.HIT_VIDEO_MULTIPLIER}): {hit_count}개")

    sheets.save_videos(all_videos)
    db.save_videos(all_videos)

    print(f"\n🤖 Gemini 분석 ({len(all_videos)}개)")
    gemini_results = gemini.analyze_videos_batch(all_videos, delay=5, model_mode='initial')

    print(f"\n💬 댓글/DeepSeek 분석")
    buffer: list[dict] = []
    all_analyses: list[dict] = []
    total_saved = 0

    for i, video in enumerate(all_videos):
        g_result = next(
            (g for g in gemini_results if g.get("video_id") == video["video_id"]),
            {"error": "Gemini 매칭 실패"}
        )
        analysis = analyze_video_complete(video, g_result)
        analysis['is_hit'] = video.get('is_hit', False)
        analysis['channel_avg_views'] = video.get('channel_avg_views', 0)
        buffer.append(analysis)
        all_analyses.append(analysis)
        print(f"  [{i+1}/{len(all_videos)}] {'⭐' if video.get('is_hit') else '  '} {video.get('title','')[:45]}")

        if len(buffer) >= 10:
            _save_analyses(buffer, "initial", batch_num)
            total_saved += len(buffer)
            print(f"  💾 중간 저장 ({total_saved}개 누적)")
            buffer = []

    if buffer:
        _save_analyses(buffer, "initial", batch_num)
        total_saved += len(buffer)

    print(f"\n✅ 분석 저장 완료: {total_saved}개")

    # 대박 영상 심층 분석
    hit_videos = [v for v in all_videos if v.get('is_hit')]
    if hit_videos:
        print(f"\n⭐ 대박 영상 심층 분석 ({len(hit_videos)}개)")
        for video in hit_videos:
            try:
                g_result = next((g for g in gemini_results if g.get('video_id') == video['video_id']), {})
                a_result = next((a for a in all_analyses if a.get('video_id') == video['video_id']), {})
                hit_analysis = ds.analyze_hit_video(video, g_result, a_result.get('comments', {}))
                db.save_hit_analysis(video['video_id'], hit_analysis)
                print(f"  ⭐ [{video.get('view_count',0):,}회] {video.get('title','')[:45]}")
                time.sleep(2)
            except Exception as e:
                print(f"  ⚠️ hit_analysis 실패 ({video.get('video_id','')}): {e}")

    print(f"\n🎯 채널별 성공공식 분석")
    insights: dict[str, dict] = {}
    for name, vids in channel_videos_map.items():
        if not vids:
            continue
        try:
            insights[name] = ds.derive_channel_success_formula(name, vids)
            time.sleep(2)
        except Exception as e:
            print(f"  ⚠️ {name} 인사이트 실패: {e}")
            insights[name] = {"error": str(e)}

    sheets.save_channel_insights(insights)
    db.save_channel_insights(insights)

    duration = time.time() - start
    tg.send_message(
        f"✅ <b>배치#{batch_num} 완료</b>\n\n"
        f"📺 채널: {', '.join(channels)}\n"
        f"🎬 영상: {len(all_videos)}개\n"
        f"⭐ 대박: {hit_count}개\n"
        f"💾 저장: {total_saved}개\n"
        f"⏱️ 소요: {duration/60:.1f}분"
    )
    print(f"\n✅ 배치#{batch_num} 완료 ({duration/60:.1f}분)")


# ───────────────────────────────────────────────
# 모드 2: 일일 수집 (평일)
# ───────────────────────────────────────────────

def run_daily_collection():
    print(f"🌙 일일 수집 시작 ({datetime.now()})")
    start = time.time()
    stats = {"new": 0, "analyzed": 0, "errors": 0}
    all_new: list[dict] = []

    cutoff = datetime.now(timezone.utc) - timedelta(days=2)

    for name in config.TARGET_CHANNELS:
        try:
            videos = collect_channel(name, months_back=1)
            new = [
                v for v in videos
                if datetime.fromisoformat(
                    v["published_at"].replace("Z", "+00:00")
                ) > cutoff
            ]
            all_new.extend(new)
            time.sleep(1)
        except Exception as e:
            print(f"❌ {name}: {e}")
            stats["errors"] += 1

    if not all_new:
        print("📭 새 영상 없음")
        tg.send_message(f"📭 새 영상 없음 ({datetime.now().strftime('%m/%d')})")
        return

    sheets.save_videos(all_new)
    db.save_videos(all_new)
    stats["new"] = len(all_new)

    print(f"\n🤖 Gemini 분석 ({len(all_new)}개)")
    gemini_results = gemini.analyze_videos_batch(all_new, delay=5, model_mode='daily')

    analyses: list[dict] = []
    for video in all_new:
        g_result = next(
            (g for g in gemini_results if g.get("video_id") == video["video_id"]),
            {"error": "Gemini 매칭 실패"}
        )
        analyses.append(analyze_video_complete(video, g_result))
        stats["analyzed"] += 1

    _save_analyses(analyses, "daily")

    duration = time.time() - start
    tg.send_message(
        f"✅ <b>일일 분석 완료</b>\n\n"
        f"📺 새 영상: {stats['new']}개\n"
        f"🤖 분석: {stats['analyzed']}개\n"
        f"⚠️ 에러: {stats['errors']}개\n"
        f"⏱️ 소요: {duration/60:.1f}분"
    )
    print("✅ 일일 수집 완료")


# ───────────────────────────────────────────────
# 모드 3: 트렌드 매일 수집 + 이레귤러 즉시 알림
# ───────────────────────────────────────────────

def run_trend_collect():
    """
    매일 실행:
      1. 트렌드 영상 수집 (TOP 50 × 카테고리)
      2. 키워드 룰로 트랙 A/B 분류
      3. 이레귤러 감지 시 즉시 알림 (조건: 24h 50만 OR 시간당 5만)
      4. 모든 분류 결과 시트/DB 저장 (월요일 종합 알림용 누적)
    """
    print(f"🌅 트렌드 수집 시작 ({datetime.now()})")
    start = time.time()

    try:
        # 1. 트렌드 영상 수집
        all_trending = []
        for cat_name, cat_id in config.TRENDING_CATEGORIES.items():
            try:
                videos = yt.get_trending_videos(category_id=cat_id, max_results=30)
                all_trending.extend(videos)
            except Exception as e:
                print(f"카테고리 {cat_name} 수집 실패: {e}")

        unique = list({v["video_id"]: v for v in all_trending}.values())
        unique = sorted(unique, key=lambda x: x.get("view_count", 0), reverse=True)[:50]
        print(f"📊 수집된 영상: {len(unique)}개")

        # 2. 영상별 상세 정보 보강 (published_at 등 정확하게)
        if unique:
            video_ids = [v["video_id"] for v in unique]
            detailed = yt.get_video_details(video_ids)
            # 트렌딩 정보 + 상세 정보 병합
            detail_map = {v["video_id"]: v for v in detailed}
            unique = [detail_map.get(v["video_id"], v) for v in unique]
            unique = [v for v in unique if v.get("video_id")]

        # 3. 트랙 분류 + 이레귤러 감지
        classified = tc.classify_videos(unique)
        print(f"🌸 트랙A: {len(classified['track_a'])}개 | "
              f"🔥 트랙B: {len(classified['track_b'])}개 | "
              f"⚡ 이레귤러: {len(classified['irregular'])}개")

        # 4. 시트/DB 저장 (월요일 종합용 누적)
        sheets.save_trend_classified(classified)
        db.save_trend_classified(classified)

        # 5. 이레귤러 즉시 알림
        if classified["irregular"]:
            for video in classified["irregular"]:
                tg.send_irregular_alert(video)
                time.sleep(0.5)
            print(f"⚡ 이레귤러 알림 {len(classified['irregular'])}건 발송")

        duration = time.time() - start
        print(f"✅ 트렌드 수집 완료 ({duration:.0f}초)")

    except Exception as e:
        print(f"❌ 트렌드 수집 오류: {e}")
        traceback.print_exc()
        tg.send_error_alert(str(e), "트렌드 수집")


# ───────────────────────────────────────────────
# 모드 4: 월요일 주간 트렌드 종합 알림
# ───────────────────────────────────────────────

def run_trend_weekly():
    """
    월요일 실행:
      지난 7일간 누적된 트렌드 데이터에서
      트랙 A / 트랙 B 각각 TOP을 종합해 알림.
    """
    print(f"📊 주간 트렌드 종합 ({datetime.now()})")

    try:
        # 지난 7일간 누적된 트렌드 데이터 조회
        weekly_data = db.get_weekly_trend_summary(days_back=7)

        if not weekly_data:
            print("⚠️ 주간 데이터 없음")
            tg.send_message("⚠️ 이번 주 트렌드 데이터 부족")
            return

        # 트랙별 TOP + 사건 키워드
        tg.send_weekly_trend_alert(weekly_data)
        print("✅ 주간 트렌드 알림 발송 완료")

    except Exception as e:
        print(f"❌ 주간 트렌드 오류: {e}")
        traceback.print_exc()
        tg.send_error_alert(str(e), "주간 트렌드")


# ───────────────────────────────────────────────
# 모드 5: 주간 채널 리포트 (기존)
# ───────────────────────────────────────────────

def run_weekly_report():
    print("📊 주간 채널 리포트 생성 시작")
    try:
        recent = sheets.get_videos_from_sheet(days_back=7)

        from collections import defaultdict
        ch_map: dict[str, list] = defaultdict(list)
        for v in recent:
            ch_map[v.get("채널명", "")].append({
                "title":      v.get("제목", ""),
                "view_count": int(v.get("조회수", 0) or 0),
            })

        insights: dict[str, dict] = {}
        for ch, vids in ch_map.items():
            if len(vids) >= 3:
                insights[ch] = ds.derive_channel_success_formula(ch, vids)
                time.sleep(2)

        sheets.save_channel_insights(insights)
        db.save_channel_insights(insights)

        top = sorted(
            [{"title": v.get("제목",""), "channel_title": v.get("채널명",""),
              "view_count": int(v.get("조회수",0) or 0)} for v in recent],
            key=lambda x: x["view_count"], reverse=True
        )[:20]

        report = ds.generate_weekly_report(insights, top)
        sheets.save_weekly_report(report)
        tg.send_weekly_report_alert(report)
        print("✅ 주간 채널 리포트 완료")
    except Exception as e:
        print(f"❌ 주간 리포트 오류: {e}")
        traceback.print_exc()
        tg.send_error_alert(str(e), "주간 리포트")


# ───────────────────────────────────────────────
# 진입점
# ───────────────────────────────────────────────

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("사용법: python main.py [initial-batch N|daily|trend-collect|trend-weekly|weekly]")
        sys.exit(1)

    mode = sys.argv[1].lower()

    if mode == "initial-batch":
        if len(sys.argv) < 3:
            print("❌ 배치 번호 필요")
            sys.exit(1)
        try:
            run_initial_batch(int(sys.argv[2]))
        except ValueError:
            print(f"❌ 배치 번호는 정수: {sys.argv[2]}")
            sys.exit(1)
    elif mode == "daily":
        run_daily_collection()
    elif mode == "trend-collect":
        run_trend_collect()
    elif mode == "trend-weekly":
        run_trend_weekly()
    elif mode == "weekly":
        run_weekly_report()
    # 구버전 호환
    elif mode == "trend":
        print("⚠️ 'trend' → 'trend-collect'로 실행됩니다")
        run_trend_collect()
    else:
        print(f"❌ 알 수 없는 모드: {mode}")
        sys.exit(1)
