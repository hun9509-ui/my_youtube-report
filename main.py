"""
한고은 채널 분석 시스템 - 메인 실행 파일

실행 모드:
  - python main.py initial       : 초기 6개월치 데이터 수집 (1회만)
  - python main.py daily         : 매일 00시 새 영상 수집/분석
  - python main.py trend         : 매일 오전 10시 트렌드 알림
  - python main.py weekly        : 매주 월요일 주간 리포트
"""
import sys
import time
import traceback
from datetime import datetime, timedelta

import config
import youtube_collector as yt
import gemini_analyzer as gemini
import deepseek_processor as ds
import sheets_writer as sheets
import telegram_notifier as tg


def collect_and_analyze_channel(channel_name, months_back=6):
    """단일 채널의 영상을 수집하고 분석"""
    print(f"\n{'='*60}")
    print(f"📺 채널: {channel_name}")
    print(f"{'='*60}")
    
    # 1. 채널 ID 검색
    channel_info = yt.search_channel_id(channel_name)
    if not channel_info:
        print(f"❌ 채널을 찾을 수 없음: {channel_name}")
        return [], []
    
    print(f"✅ 채널 발견: {channel_info['channel_title']}")
    
    # 2. 영상 목록 수집
    videos, channel_meta = yt.get_channel_videos(
        channel_info['channel_id'], 
        months_back=months_back
    )
    print(f"📹 영상 수집: {len(videos)}개")
    
    if not videos:
        return [], []
    
    # 3. 영상 상세 정보
    video_ids = [v['video_id'] for v in videos]
    video_details = yt.get_video_details(video_ids)
    print(f"📊 상세 정보 수집 완료")
    
    return video_details, channel_meta


def run_initial_collection():
    """초기 6개월치 데이터 일괄 수집 (1회만 실행)"""
    print("🚀 초기 데이터 수집 시작 (최근 6개월)")
    start_time = time.time()
    
    all_videos = []
    channel_videos_map = {}
    
    for channel_name in config.TARGET_CHANNELS:
        try:
            videos, meta = collect_and_analyze_channel(channel_name, months_back=config.INITIAL_MONTHS)
            all_videos.extend(videos)
            channel_videos_map[channel_name] = videos
            time.sleep(2)
        except Exception as e:
            print(f"❌ 채널 처리 실패 ({channel_name}): {e}")
            tg.send_error_alert(str(e), f"초기 수집 - {channel_name}")
    
    print(f"\n📊 총 수집된 영상: {len(all_videos)}개")
    
    # 시트에 영상 저장
    saved_count = sheets.save_videos(all_videos)
    print(f"💾 시트 저장 완료: {saved_count}개")
    
    # Gemini 분석 (배치)
    print(f"\n🤖 Gemini 영상 분석 시작...")
    gemini_results = gemini.analyze_videos_batch(all_videos[:100], delay=4.5)
    
    # 댓글 분석 (상위 영상만)
    print(f"\n💬 댓글 분석 시작 (상위 30개 영상)")
    top_videos = sorted(all_videos, key=lambda x: x.get('view_count', 0), reverse=True)[:30]
    analyses = []
    
    for i, video in enumerate(top_videos):
        gemini_result = next((g for g in gemini_results if g.get('video_id') == video['video_id']), {})
        
        try:
            comments = yt.get_video_comments(video['video_id'], max_comments=config.TOP_COMMENTS_COUNT)
            comments_analysis = ds.analyze_comments(video['title'], comments) if comments else {}
        except Exception as e:
            comments_analysis = {'error': str(e)}
        
        analyses.append({
            'video_id': video['video_id'],
            'gemini': gemini_result,
            'comments': comments_analysis
        })
        print(f"  댓글 분석 {i+1}/{len(top_videos)}: {video['title'][:40]}")
    
    sheets.save_analysis(analyses)
    
    # 채널별 성공 공식 도출
    print(f"\n🎯 채널별 성공 공식 분석...")
    channel_insights = {}
    for channel_name, videos in channel_videos_map.items():
        try:
            insight = ds.derive_channel_success_formula(channel_name, videos)
            channel_insights[channel_name] = insight
            time.sleep(2)
        except Exception as e:
            channel_insights[channel_name] = {'error': str(e)}
    
    sheets.save_channel_insights(channel_insights)
    
    duration = time.time() - start_time
    print(f"\n✅ 초기 수집 완료 ({duration/60:.1f}분 소요)")
    
    tg.send_message(f"✅ <b>초기 데이터 수집 완료</b>\n\n"
                    f"📺 영상: {len(all_videos)}개\n"
                    f"🤖 분석: {len(analyses)}개\n"
                    f"⏱️ 소요: {duration/60:.1f}분")


def run_daily_collection():
    """매일 00시 - 어제 새로 올라온 영상만 수집/분석"""
    print(f"🌙 일일 자동 수집 시작 ({datetime.now()})")
    start_time = time.time()
    
    stats = {'new_videos': 0, 'analyzed': 0, 'comments_analyzed': 0, 'errors': 0}
    
    all_new_videos = []
    
    for channel_name in config.TARGET_CHANNELS:
        try:
            videos, meta = collect_and_analyze_channel(channel_name, months_back=1)
            
            # 어제 이후 영상만 필터
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
    
    if all_new_videos:
        # 저장
        new_count = sheets.save_videos(all_new_videos)
        stats['new_videos'] = new_count
        
        # 분석
        gemini_results = gemini.analyze_videos_batch(all_new_videos, delay=4.5)
        analyses = []
        
        for video in all_new_videos:
            gemini_result = next((g for g in gemini_results if g.get('video_id') == video['video_id']), {})
            
            try:
                comments = yt.get_video_comments(video['video_id'], max_comments=config.TOP_COMMENTS_COUNT)
                comments_analysis = ds.analyze_comments(video['title'], comments) if comments else {}
                stats['comments_analyzed'] += 1
            except Exception:
                comments_analysis = {}
            
            analyses.append({
                'video_id': video['video_id'],
                'gemini': gemini_result,
                'comments': comments_analysis
            })
            stats['analyzed'] += 1
        
        sheets.save_analysis(analyses)
    
    duration = time.time() - start_time
    stats['duration'] = f"{duration/60:.1f}분"
    
    tg.send_collection_summary(stats)
    print(f"✅ 일일 수집 완료")


def run_daily_trend():
    """매일 오전 10시 - 트렌드 분석 + 알림"""
    print(f"🌅 일일 트렌드 분석 시작")
    
    try:
        # 한국 트렌딩 영상 수집
        all_trending = []
        for cat_name, cat_id in config.TRENDING_CATEGORIES.items():
            try:
                videos = yt.get_trending_videos(category_id=cat_id, max_results=30)
                all_trending.extend(videos)
            except Exception as e:
                print(f"카테고리 {cat_name} 수집 실패: {e}")
        
        # 중복 제거
        unique_trending = {v['video_id']: v for v in all_trending}.values()
        unique_trending = sorted(unique_trending, key=lambda x: x['view_count'], reverse=True)[:50]
        
        # Gemini로 트렌드 패턴 감지
        gemini_analysis = gemini.detect_daily_trends(list(unique_trending))
        
        # 딥시크로 텔레그램용 요약
        summary = ds.summarize_daily_trends(list(unique_trending), gemini_analysis)
        
        # 시트 저장
        sheets.save_daily_trends(summary)
        
        # 텔레그램 발송
        tg.send_daily_trend_alert(summary)
        
        print(f"✅ 일일 트렌드 알림 완료")
    except Exception as e:
        print(f"❌ 오류: {e}")
        traceback.print_exc()
        tg.send_error_alert(str(e), "일일 트렌드 분석")


def run_weekly_report():
    """매주 월요일 오전 10시 - 주간 리포트"""
    print(f"📊 주간 리포트 생성 시작")
    
    try:
        # 지난 주 영상 데이터 가져오기
        recent_videos = sheets.get_videos_from_sheet(days_back=7)
        
        # 채널별 성공 공식 재계산
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
        
        # TOP 영상 추출
        top_videos = sorted(
            [{'title': v.get('제목', ''), 
              'channel_title': v.get('채널명', ''), 
              'view_count': int(v.get('조회수', 0) or 0)} 
             for v in recent_videos],
            key=lambda x: x['view_count'],
            reverse=True
        )[:20]
        
        # 주간 리포트 생성
        report = ds.generate_weekly_report(channel_insights, top_videos)
        
        # 저장
        sheets.save_weekly_report(report)
        
        # 텔레그램 발송
        tg.send_weekly_report_alert(report)
        
        print(f"✅ 주간 리포트 완료")
    except Exception as e:
        print(f"❌ 오류: {e}")
        traceback.print_exc()
        tg.send_error_alert(str(e), "주간 리포트")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("사용법: python main.py [initial|daily|trend|weekly]")
        sys.exit(1)
    
    mode = sys.argv[1].lower()
    
    if mode == "initial":
        run_initial_collection()
    elif mode == "daily":
        run_daily_collection()
    elif mode == "trend":
        run_daily_trend()
    elif mode == "weekly":
        run_weekly_report()
    else:
        print(f"알 수 없는 모드: {mode}")
        sys.exit(1)
