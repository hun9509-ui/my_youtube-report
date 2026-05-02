"""
한고은 채널 분석 시스템 - 메인 실행 파일

실행 모드:
  - python main.py initial-batch [1~5] : 배치별 초기 수집
  - python main.py daily               : 매일 평일 새 영상
  - python main.py trend               : 매일 트렌드 알림
  - python main.py weekly              : 매주 월요일 리포트
  - python main.py initial             : (구) 일괄 수집 (사용 비권장)
"""
import sys
sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

print("=" * 60, flush=True)
print("🎬 한고은 채널 분석 시스템 시작", flush=True)
print("=" * 60, flush=True)

import time
import traceback
from datetime import datetime, timedelta

print("⏳ 모듈 import 중...", flush=True)
import config
print("  ✓ config", flush=True)
import youtube_collector as yt
print("  ✓ youtube_collector", flush=True)
import gemini_analyzer as gemini
print("  ✓ gemini_analyzer", flush=True)
import deepseek_processor as ds
print("  ✓ deepseek_processor", flush=True)
import sheets_writer as sheets
print("  ✓ sheets_writer", flush=True)
import supabase_writer as db
print("  ✓ supabase_writer", flush=True)
import telegram_notifier as tg
print("  ✓ telegram_notifier", flush=True)

# 환경변수 체크
print("\n⏳ 환경변수 체크 중...", flush=True)
required_vars = {
    'YOUTUBE_API_KEY': config.YOUTUBE_API_KEY,
    'GEMINI_API_KEY': config.GEMINI_API_KEY,
    'DEEPSEEK_API_KEY': config.DEEPSEEK_API_KEY,
    'TELEGRAM_BOT_TOKEN': config.TELEGRAM_BOT_TOKEN,
    'TELEGRAM_CHAT_ID': config.TELEGRAM_CHAT_ID,
    'GOOGLE_WEBHOOK_URL': config.GOOGLE_WEBHOOK_URL,
}
optional_vars = {
    'SUPABASE_URL': config.SUPABASE_URL,
    'SUPABASE_KEY': config.SUPABASE_KEY,
}
missing = [k for k, v in required_vars.items() if not v]
if missing:
    print(f"❌ 누락된 환경변수: {missing}", flush=True)
    sys.exit(1)
missing_opt = [k for k, v in optional_vars.items() if not v]
if missing_opt:
    print(f"⚠️ 선택 환경변수 미설정 (Supabase 비활성): {missing_opt}", flush=True)
else:
    print("  ✓ 모든 환경변수 정상 (Supabase 포함)", flush=True)


def collect_channel(channel_name, months_back=6):
    """단일 채널 영상 수집"""
    print(f"\n{'='*60}")
    print(f"📺 채널: {channel_name}")
    print(f"{'='*60}")
    
    channel_info = yt.search_channel_id(channel_name)
    if not channel_info:
        print(f"❌ 채널을 찾을 수 없음: {channel_name}")
        return [], None
    
    print(f"✅ 채널 발견: {channel_info['channel_title']}")
    
    videos, channel_meta = yt.get_channel_videos(
        channel_info['channel_id'], 
        months_back=months_back
    )
    print(f"📹 영상 수집: {len(videos)}개")
    
    if not videos:
        return [], channel_meta
    
    video_ids = [v['video_id'] for v in videos]
    video_details = yt.get_video_details(video_ids)
    print(f"📊 상세 정보 수집 완료")
    
    return video_details, channel_meta


def analyze_video_complete(video, gemini_result):
    """영상 1개를 완전 분석 (Gemini + 댓글 + DeepSeek)"""
    try:
        comments = yt.get_video_comments(video['video_id'], max_comments=config.TOP_COMMENTS_COUNT)
        if comments:
            comments_analysis = ds.analyze_comments(video['title'], comments)
        else:
            comments_analysis = {'skipped': '댓글 없음 또는 비활성화'}
    except Exception as e:
        print(f"  ⚠️ 댓글 분석 실패: {e}")
        comments_analysis = {'error': str(e)}
    
    return {
        'video_id': video['video_id'],
        'gemini': gemini_result,
        'comments': comments_analysis
    }


def run_initial_batch(batch_num):
    """배치별 초기 수집 + 분석 (★ 새로운 메인 모드)"""
    print(f"\n🚀 초기 배치 #{batch_num} 시작 ({datetime.now()})")
    start_time = time.time()
    
    if batch_num not in config.INITIAL_BATCHES:
        print(f"❌ 잘못된 배치 번호: {batch_num} (1~5만 가능)")
        return
    
    channels = config.INITIAL_BATCHES[batch_num]
    print(f"📋 처리 대상 채널: {channels}")
    
    all_videos = []
    channel_videos_map = {}
    
    # 1. 채널별 영상 수집
    for channel_name in channels:
        try:
            videos, _ = collect_channel(channel_name, months_back=config.INITIAL_MONTHS)
            all_videos.extend(videos)
            channel_videos_map[channel_name] = videos
            time.sleep(2)
        except Exception as e:
            print(f"❌ 채널 처리 실패 ({channel_name}): {e}")
            traceback.print_exc()
            tg.send_error_alert(str(e), f"배치 #{batch_num} - {channel_name}")
    
    print(f"\n📊 배치 #{batch_num} 총 영상: {len(all_videos)}개")
    
    if not all_videos:
        print("⚠️ 분석할 영상 없음")
        tg.send_message(f"⚠️ 배치 #{batch_num}: 분석할 영상 없음")
        return
    
    # 2. 영상 마스터 저장 (시트 + Supabase)
    print(f"\n💾 영상 마스터 저장")
    sheets.save_videos(all_videos)
    db.save_videos(all_videos)
    
    # 3. Gemini 분석 (delay 5초로 단축)
    print(f"\n🤖 Gemini 분석 시작 ({len(all_videos)}개)")
    gemini_results = gemini.analyze_videos_batch(all_videos, delay=5)
    
    # 4. 댓글 + DeepSeek 분석 + 중간 저장
    print(f"\n💬 댓글 + DeepSeek 분석 시작")
    analyses_buffer = []
    total_saved = 0
    
    for i, video in enumerate(all_videos):
        gemini_result = next(
            (g for g in gemini_results if g.get('video_id') == video['video_id']),
            {'error': 'Gemini 매칭 실패'}
        )
        
        # 에러 결과면 건너뛰지 말고 그대로 저장 (학습 자산화)
        analysis = analyze_video_complete(video, gemini_result)
        analyses_buffer.append(analysis)
        
        print(f"  [{i+1}/{len(all_videos)}] {video.get('title', '')[:40]}")
        
        # 10개마다 중간 저장 (timeout 대비)
        if len(analyses_buffer) >= 10:
            sheets.save_initial_analysis(analyses_buffer, batch_num)
            db.save_initial_analysis(analyses_buffer, batch_num)
            total_saved += len(analyses_buffer)
            print(f"  💾 중간 저장 ({total_saved}개 누적)")
            analyses_buffer = []
    
    # 잔여 저장
    if analyses_buffer:
        sheets.save_initial_analysis(analyses_buffer, batch_num)
        db.save_initial_analysis(analyses_buffer, batch_num)
        total_saved += len(analyses_buffer)
    
    print(f"\n✅ 분석 저장 완료: {total_saved}개")
    
    # 5. 채널 인사이트
    print(f"\n🎯 채널별 성공공식 분석")
    channel_insights = {}
    for channel_name, videos in channel_videos_map.items():
        if not videos:
            continue
        try:
            insight = ds.derive_channel_success_formula(channel_name, videos)
            channel_insights[channel_name] = insight
            time.sleep(2)
        except Exception as e:
            print(f"  ⚠️ {channel_name} 인사이트 실패: {e}")
            channel_insights[channel_name] = {'error': str(e)}
    
    sheets.save_channel_insights(channel_insights)
    db.save_channel_insights(channel_insights)
    
    # 6. 완료 알림
    duration = time.time() - start_time
    tg.send_message(
        f"✅ <b>배치 #{batch_num} 완료</b>\n\n"
        f"📺 채널: {', '.join(channels)}\n"
        f"🎬 영상: {len(all_videos)}개\n"
        f"💾 분석 저장: {total_saved}개\n"
        f"⏱️ 소요: {duration/60:.1f}분"
    )
    print(f"\n✅ 배치 #{batch_num} 완료 ({duration/60:.1f}분)")


def run_daily_collection():
    """매일 평일 - 어제 새 영상만"""
    print(f"🌙 일일 수집 시작 ({datetime.now()})")
    start_time = time.time()
    
    stats = {'new_videos': 0, 'analyzed': 0, 'errors': 0}
    all_new_videos = []
    
    for channel_name in config.TARGET_CHANNELS:
        try:
            videos, _ = collect_channel(channel_name, months_back=1)
            yesterday = datetime.now() - timedelta(days=2)
            new_videos = [
                v for v in videos 
                if datetime.fromisoformat(v['published_at'].replace('Z', '+00:00')).replace(tzinfo=None) > yesterday
            ]
            all_new_videos.extend(new_videos)
            time.sleep(1)
        except Exception as e:
            print(f"❌ {channel_name}: {e}")
            stats['errors'] += 1
    
    if not all_new_videos:
        print("📭 새 영상 없음")
        tg.send_message(f"📭 오늘 새 영상 없음 ({datetime.now().strftime('%m/%d')})")
        return
    
    # 영상 저장
    new_count = sheets.save_videos(all_new_videos)
    db.save_videos(all_new_videos)
    stats['new_videos'] = new_count
    
    # Gemini 분석
    print(f"\n🤖 Gemini 분석 ({len(all_new_videos)}개)")
    gemini_results = gemini.analyze_videos_batch(all_new_videos, delay=5)
    
    # 댓글 + DeepSeek
    analyses = []
    for video in all_new_videos:
        gemini_result = next(
            (g for g in gemini_results if g.get('video_id') == video['video_id']),
            {'error': 'Gemini 매칭 실패'}
        )
        analysis = analyze_video_complete(video, gemini_result)
        analyses.append(analysis)
        stats['analyzed'] += 1
    
    # 시트 + Supabase 동시 저장
    sheets.save_daily_analysis(analyses)
    db.save_daily_analysis(analyses)
    
    duration = time.time() - start_time
    tg.send_message(
        f"✅ <b>일일 분석 완료</b>\n\n"
        f"📺 새 영상: {stats['new_videos']}개\n"
        f"🤖 분석: {stats['analyzed']}개\n"
        f"⚠️ 에러: {stats['errors']}개\n"
        f"⏱️ 소요: {duration/60:.1f}분"
    )
    print(f"✅ 일일 수집 완료")


def run_daily_trend():
    """일일 트렌드 알림 (현재 코드 그대로 유지, 차후 재설계 예정)"""
    print(f"🌅 일일 트렌드 분석 시작")
    
    try:
        all_trending = []
        for cat_name, cat_id in config.TRENDING_CATEGORIES.items():
            try:
                videos = yt.get_trending_videos(category_id=cat_id, max_results=30)
                all_trending.extend(videos)
            except Exception as e:
                print(f"카테고리 {cat_name} 수집 실패: {e}")
        
        unique_trending = {v['video_id']: v for v in all_trending}.values()
        unique_trending = sorted(unique_trending, key=lambda x: x['view_count'], reverse=True)[:50]
        
        gemini_analysis = gemini.detect_daily_trends(list(unique_trending))
        summary = ds.summarize_daily_trends(list(unique_trending), gemini_analysis)
        
        sheets.save_daily_trends(summary)
        tg.send_daily_trend_alert(summary)
        
        print(f"✅ 일일 트렌드 알림 완료")
    except Exception as e:
        print(f"❌ 오류: {e}")
        traceback.print_exc()
        tg.send_error_alert(str(e), "일일 트렌드 분석")


def run_weekly_report():
    """주간 리포트 (현재 코드 유지)"""
    print(f"📊 주간 리포트 생성 시작")
    
    try:
        recent_videos = sheets.get_videos_from_sheet(days_back=7)
        
        from collections import defaultdict
        channel_videos = defaultdict(list)
        for v in recent_videos:
            channel_videos[v.get('채널명', '')].append({
                'title': v.get('제목', ''),
                'view_count': int(v.get('조회수', 0) or 0)
            })
        
        channel_insights = {}
        for channel, vids in channel_videos.items():
            if len(vids) >= 3:
                insight = ds.derive_channel_success_formula(channel, vids)
                channel_insights[channel] = insight
                time.sleep(2)
        
        sheets.save_channel_insights(channel_insights)
        db.save_channel_insights(channel_insights)
        
        top_videos = sorted(
            [{'title': v.get('제목', ''), 
              'channel_title': v.get('채널명', ''), 
              'view_count': int(v.get('조회수', 0) or 0)} 
             for v in recent_videos],
            key=lambda x: x['view_count'],
            reverse=True
        )[:20]
        
        report = ds.generate_weekly_report(channel_insights, top_videos)
        sheets.save_weekly_report(report)
        tg.send_weekly_report_alert(report)
        
        print(f"✅ 주간 리포트 완료")
    except Exception as e:
        print(f"❌ 오류: {e}")
        traceback.print_exc()
        tg.send_error_alert(str(e), "주간 리포트")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("사용법: python main.py [initial-batch N|daily|trend|weekly|initial]")
        sys.exit(1)
    
    mode = sys.argv[1].lower()
    
    if mode == "initial-batch":
        if len(sys.argv) < 3:
            print("❌ 배치 번호 필요: python main.py initial-batch [1-5]")
            sys.exit(1)
        try:
            batch_num = int(sys.argv[2])
            run_initial_batch(batch_num)
        except ValueError:
            print(f"❌ 배치 번호는 정수여야 함: {sys.argv[2]}")
            sys.exit(1)
    elif mode == "daily":
        run_daily_collection()
    elif mode == "trend":
        run_daily_trend()
    elif mode == "weekly":
        run_weekly_report()
    elif mode == "initial":
        print("⚠️ initial 모드는 사용 비권장. initial-batch로 5번 나눠 실행하세요.")
        sys.exit(1)
    else:
        print(f"알 수 없는 모드: {mode}")
        sys.exit(1)
