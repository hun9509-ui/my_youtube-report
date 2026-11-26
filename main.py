"""
한고은 채널 분석 시스템 - 메인 실행 파일

실행 모드:
  python main.py initial-batch [1~5]  : 배치별 초기 수집
  python main.py daily                : 평일 새 영상 분석
  python main.py trend-collect        : 매일 트렌드 수집 + 이레귤러 즉시 알림
  python main.py trend-weekly         : 월요일 주간 트렌드 종합 알림
  python main.py weekly               : 주간 채널 리포트
  python main.py full-backfill        : 미분석 영상 전체 Gemini+DeepSeek 분석
  python main.py comment-backfill     : 기존 분석 영상 댓글 재분석 (emotion_temperature 채우기)
  python main.py thumbnail-backfill   : 썸네일 Vision 분석 미완료분 처리
  python main.py score-backfill       : 전략 스코어 미산출분 처리
  python main.py snapshot-collect     : 전체 영상 시계열 스냅샷 수집 (매일)
  python main.py growth-pattern       : 성장 패턴 분류 업데이트
"""
import sys
import time
from datetime import datetime, timedelta, timezone
from analysis_schema import CommentAnalysis, MarketAnalysis, WeeklyReport
from gemini_client import has_text, safe_error

import config
import youtube_collector as yt
import gemini_analyzer as gemini
import deepseek_processor as ds
import sheets_writer as sheets
import supabase_writer as db
import telegram_notifier as tg
import trend_classifier as tc
import strategy_scorer as scorer

def validate_env(mode):
    if config.GEMINI_PROVIDER not in {"ai_studio", "vertexai"}:
        print("❌ GEMINI_PROVIDER는 ai_studio 또는 vertexai이어야 합니다")
        return False
    if config.TEXT_ANALYSIS_ENGINE not in {"gemini", "deepseek"}:
        print("❌ TEXT_ANALYSIS_ENGINE은 gemini 또는 deepseek이어야 합니다")
        return False

    youtube_modes = {
        "daily", "own-channel", "initial-batch", "full-backfill", "comment-backfill",
        "own-hourly", "own-track", "history-extend", "snapshot-collect", "stats-refresh",
        "trend", "trend-collect", "b-track-init", "b-track-collect", "b-track-discover",
        "b-track-run",
    }
    telegram_modes = {
        "daily", "own-channel", "weekly", "initial-batch", "full-backfill", "comment-backfill",
        "thumbnail-backfill", "own-hourly", "own-track", "score-backfill", "score-sheets-sync",
        "history-extend", "trend", "trend-collect", "trend-weekly",
    }
    text_modes = {
        "daily", "own-channel", "weekly", "initial-batch", "full-backfill",
        "comment-backfill", "own-track", "history-extend",
    }
    gemini_modes = {
        "daily", "own-channel", "initial-batch", "full-backfill", "thumbnail-backfill",
        "history-extend", "b-track-discover",
    }
    required = {"SUPABASE_URL": config.SUPABASE_URL, "SUPABASE_KEY": config.SUPABASE_KEY}
    if mode in youtube_modes:
        required["YOUTUBE_API_KEY"] = config.YOUTUBE_API_KEY
    if mode in telegram_modes:
        required["TELEGRAM_BOT_TOKEN"] = config.TELEGRAM_BOT_TOKEN
        required["TELEGRAM_CHAT_ID"] = config.TELEGRAM_CHAT_ID
    if mode in text_modes and config.TEXT_ANALYSIS_ENGINE == "deepseek":
        required["DEEPSEEK_API_KEY"] = config.DEEPSEEK_API_KEY
    needs_gemini = mode in gemini_modes or (mode in text_modes and config.TEXT_ANALYSIS_ENGINE == "gemini")
    if needs_gemini and config.GEMINI_PROVIDER == "ai_studio":
        required["GEMINI_API_KEY"] = config.GEMINI_API_KEY

    missing = [key for key, value in required.items() if not value]
    if (mode in {"b-track-scan", "b-track-run"} and config.GEMINI_PROVIDER == "ai_studio"
            and not (config.DEEPSEEK_API_KEY or config.GEMINI_API_KEY)):
        missing.append("DEEPSEEK_API_KEY 또는 GEMINI_API_KEY")
    if missing:
        print(f"❌ 누락된 환경변수: {', '.join(missing)}")
    return not missing


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
        raise ValueError(f"채널 ID 미설정 ({channel_name})")

    print(f"✅ 채널 ID: {channel_id}")
    
    # 1. 포장지 분해: 영상 목록(raw_videos)과 채널 정보(channel_info)를 따로 받기
    raw_videos, channel_info = yt.get_channel_videos(channel_id, months_back=months_back)
    if not channel_info:
        raise RuntimeError(f"YouTube 채널 조회 결과 없음 ({channel_name})")
    
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
                max_comments=config.TOP_COMMENTS_COUNT,
                raise_on_error=True,
            )
            if comments:
                comments_analysis = ds.analyze_comments(video["title"], comments)
                if "error" not in comments_analysis:
                    comments_analysis = CommentAnalysis.model_validate(comments_analysis).model_dump()
            else:
                comments_analysis = {"skipped": "댓글 수집 결과 없음"}
        except Exception as e:
            error = safe_error(e)
            print(f"  ⚠️ 댓글 분석 실패: {error}")
            comments_analysis = {"error": error}

    return {
        "video_id": video["video_id"],
        "gemini":   gemini_result,
        "comments": comments_analysis,
    }


def _save_analyses(analyses: list[dict], mode: str, batch_num: int = 0):
    valid = [a for a in analyses if has_text(a.get("gemini"), "topic")]
    if not valid:
        return 0
    if mode == "initial":
        saved = db.save_initial_analysis(valid, batch_num)
        if saved == len(valid):
            sheets.save_initial_analysis(valid, batch_num)
        return saved
    saved = db.save_daily_analysis(valid)
    if saved == len(valid):
        sheets.save_daily_analysis(valid)
    return saved


def _score_and_save(analyses: list[dict], videos_list: list[dict], source_table: str):
    """분석 완료 후 전략 스코어 자동 생성 — 실패해도 분석 저장은 영향 없음"""
    try:
        videos_map = {v.get('video_id'): v for v in videos_list}
        valid = [a for a in analyses if has_text(a.get("gemini"), "topic")]
        scores = scorer.score_batch(videos_map, valid, source_table)
        if not scores:
            return 0
        saved = db.save_video_scores(scores)
        if saved == len(scores):
            sheets.save_strategy_scores(scores, videos_map)
        print(f"  📊 전략 스코어 {saved}/{len(scores)}개 저장")
        return saved
    except Exception as e:
        print(f"  ⚠️ 전략 스코어링 실패: {safe_error(e)}")
        return 0


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
            print(f"❌ 채널 실패 ({name}): {safe_error(e)}")
            tg.send_error_alert(safe_error(e), f"배치#{batch_num} - {name}")

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
            total_saved += _save_analyses(buffer, "initial", batch_num)
            print(f"  💾 중간 저장 ({total_saved}개 누적)")
            buffer = []

    if buffer:
        total_saved += _save_analyses(buffer, "initial", batch_num)

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
                print(f"  ⚠️ hit_analysis 실패 ({video.get('video_id','')}): {safe_error(e)}")

    print(f"\n🎯 채널별 성공공식 분석")
    insights: dict[str, dict] = {}
    for name, vids in channel_videos_map.items():
        if not vids:
            continue
        try:
            insights[name] = ds.derive_channel_success_formula(name, vids)
            time.sleep(2)
        except Exception as e:
            print(f"  ⚠️ {name} 인사이트 실패: {safe_error(e)}")
            insights[name] = {"error": safe_error(e)}

    sheets.save_channel_insights(insights)
    db.save_channel_insights(insights)

    print(f"\n📊 전략 스코어링")
    _score_and_save(all_analyses, all_videos, 'initial_analysis')

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
    now = datetime.now(config.KST)
    cutoff = now - timedelta(days=config.DAILY_LOOKBACK_DAYS)
    videos = {}
    failed_channels = []
    for name in config.TARGET_CHANNELS:
        try:
            for video in collect_channel(name, months_back=1):
                published_at = datetime.fromisoformat(video["published_at"].replace("Z", "+00:00"))
                if published_at > cutoff:
                    videos[video["video_id"]] = video
        except Exception as e:
            failed_channels.append(name)
            print(f"❌ {name} 수집 실패: {safe_error(e)}")

    stats = {
        "후보 영상": len(videos), "영상 저장": 0, "기존 분석": 0,
        "신규 분석 저장": 0, "Gemini 실패": 0, "댓글 실패": 0,
        "댓글 복구": 0, "점수 저장": 0,
    }
    issues = []
    if failed_channels:
        issues.append("수집 실패 채널: " + ", ".join(failed_channels))
    processing_failed = False
    if videos:
        try:
            _process_daily_videos(videos, now.date(), stats, issues)
        except Exception as exc:
            processing_failed = True
            issues.append(f"일일 처리 중단: {safe_error(exc)}")

    all_channels_failed = len(failed_channels) == len(config.TARGET_CHANNELS)
    all_analyses_failed = videos and not (stats["신규 분석 저장"] or stats["기존 분석"])
    if all_channels_failed or processing_failed or all_analyses_failed:
        status = "실패"
    elif issues:
        status = "부분 실패"
    else:
        status = "완료"
    counts = " | ".join(f"{key} {value}" for key, value in stats.items())
    message = "\n".join([f"일일 경쟁 채널 분석 {status} ({now:%Y-%m-%d} KST)", counts, *issues])
    print(message)
    delivered = tg.send_message(message, parse_mode=None)
    return status == "완료" and delivered


def _process_daily_videos(videos, analysis_date, stats, issues):
    existing, scored = db.get_daily_state(list(videos))
    stats["기존 분석"] = len(existing)
    stats["영상 저장"] = db.save_videos(list(videos.values()))
    if stats["영상 저장"] != len(videos):
        raise RuntimeError("Supabase 영상 저장 건수가 후보 건수와 다릅니다")
    sheets.save_videos(list(videos.values()))

    pending = [video for video_id, video in videos.items() if video_id not in existing]
    results = {}
    if pending:
        results = {row["video_id"]: row for row in gemini.analyze_videos_batch(pending)}
    stats["Gemini 실패"] = sum(not has_text(results.get(v["video_id"]), "topic") for v in pending)
    if stats["Gemini 실패"]:
        issues.append("Gemini 분석 실패. 실행 로그에서 모델명과 API 오류를 확인하세요.")

    analyses = []
    recovered_ids = set()
    for video_id, video in videos.items():
        prior = existing.get(video_id)
        if prior:
            if not prior.get("processing_key") or "error" not in (prior.get("deepseek_raw") or {}):
                continue
            result = analyze_video_complete(video, prior["gemini_raw"])
            result["analysis_date"] = prior["analysis_date"]
        else:
            gemini_result = results.get(video_id)
            if not has_text(gemini_result, "topic"):
                continue
            result = analyze_video_complete(video, gemini_result)

        if "error" in result["comments"]:
            stats["댓글 실패"] += 1
            print(f"  ⚠️ 댓글 분석 실패 ({video_id}): {safe_error(result['comments']['error'])}")
            if prior:
                continue
        elif prior:
            recovered_ids.add(video_id)
        analyses.append(result)

    if stats["댓글 실패"]:
        issues.append("일부 댓글 분석 실패. 유효한 영상 주제 분석을 저장합니다.")
    if analyses:
        saved = db.save_daily_analysis(analyses, analysis_date)
        if saved != len(analyses):
            raise RuntimeError("Supabase 일일 분석 저장 건수가 요청 건수와 다릅니다")
        stats["댓글 복구"] = len(recovered_ids)
        stats["신규 분석 저장"] = saved - len(recovered_ids)
        sheets.save_daily_analysis(analyses, analysis_date)

    candidates = {
        video_id: {"video_id": video_id, "gemini": row["gemini_raw"],
                   "comments": row.get("deepseek_raw") or {}}
        for video_id, row in existing.items()
    }
    candidates.update((row["video_id"], row) for row in analyses)
    scored.difference_update(row["video_id"] for row in analyses)
    unscored = [row for video_id, row in candidates.items() if video_id not in scored]
    if unscored:
        stats["점수 저장"] = _score_and_save(unscored, list(videos.values()), "daily_analysis")
        if stats["점수 저장"] != len(unscored):
            issues.append("일부 전략 점수 저장 실패. 다음 실행에서 다시 시도합니다.")


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
        failures = []
        for cat_name, cat_id in config.TRENDING_CATEGORIES.items():
            try:
                videos = yt.get_trending_videos(category_id=cat_id, max_results=30)
                all_trending.extend(videos)
            except Exception as e:
                failures.append(f"{cat_name} 수집 실패: {safe_error(e)}")

        if len(failures) == len(config.TRENDING_CATEGORIES):
            raise RuntimeError("전체 트렌드 카테고리 수집 실패 " + ", ".join(failures))

        unique = list({v["video_id"]: v for v in all_trending}.values())
        unique = sorted(unique, key=lambda x: x.get("view_count", 0), reverse=True)[:50]
        print(f"📊 수집된 영상: {len(unique)}개")

        # 3. 트랙 분류 + 이레귤러 감지
        classified = tc.classify_videos(unique)
        print(f"🌸 트랙A: {len(classified['track_a'])}개 | "
              f"🔥 트랙B: {len(classified['track_b'])}개 | "
              f"⚡ 이레귤러: {len(classified['irregular'])}개")

        # 4. 시트/DB 저장 (월요일 종합용 누적)
        expected = sum(len(classified[key]) for key in ("track_a", "track_b", "irregular"))
        if db.save_trend_classified(classified) != expected:
            raise RuntimeError("분류 결과 저장 건수 불일치")
        sheets.save_trend_classified(classified)

        # 5. 이레귤러 즉시 알림
        if classified["irregular"]:
            for video in classified["irregular"]:
                if not tg.send_irregular_alert(video):
                    failures.append(f"{video['video_id']} 급상승 알림 발송 실패")
                time.sleep(0.5)

        if failures:
            raise RuntimeError(" / ".join(failures))
        duration = time.time() - start
        print(f"✅ 트렌드 수집 완료 ({duration:.0f}초)")
        return True

    except Exception as e:
        error = safe_error(e)
        print(f"❌ 트렌드 수집 오류: {error}")
        tg.send_error_alert(error, "트렌드 수집")
        return False


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

        if not any(weekly_data[k] for k in ("track_a", "track_b", "event_keywords")):
            print("⚠️ 주간 데이터 없음")
            return tg.send_message("⚠️ 이번 주 트렌드 데이터 부족")

        # 트랙별 TOP + 사건 키워드
        return tg.send_weekly_trend_alert(weekly_data)

    except Exception as e:
        error = safe_error(e)
        print(f"❌ 주간 트렌드 오류: {error}")
        tg.send_error_alert(error, "주간 트렌드")
        return False


# ───────────────────────────────────────────────
# 모드 5: 자체 채널 새 영상 분석
# ───────────────────────────────────────────────

def run_own_channel_hourly_snapshot():
    """
    업로드 후 24시간 이내 자체 채널 영상 시간별 스냅샷.
    새 영상 없으면 즉시 종료 (API 소모 없음).
    """
    targets = db.get_own_videos_within_hours(hours=24)
    if not targets:
        print("📭 24시간 이내 자체 채널 신규 영상 없음 — skip")
        return

    print(f"⏱️ 시간별 스냅샷 대상: {len(targets)}개")

    video_ids = [v['video_id'] for v in targets]
    fresh_list = yt.get_video_details(video_ids, min_duration=0)
    fresh_map = {v['video_id']: v for v in fresh_list}

    for target in targets:
        video_id = target['video_id']
        fresh = fresh_map.get(video_id)
        if not fresh:
            continue

        try:
            pub = datetime.fromisoformat(
                str(target['published_at']).replace('Z', '+00:00')
            )
            hours = (datetime.now(timezone.utc) - pub).total_seconds() / 3600
        except Exception:
            hours = 0

        prev = db.get_last_hourly_snapshot(video_id)
        db.save_hourly_snapshot(video_id, hours, fresh, prev)

        view_growth = fresh.get('view_count', 0) - (prev['view_count'] if prev else 0)
        vtype = '숏츠' if target.get('video_type') == 'shorts' else '롱폼'
        print(f"  📸 [{vtype}] {target['title'][:40]} | {hours:.1f}h | +{view_growth:,}회")

        # 초기 급등 감지: 6시간 이내 + 시간당 5천 이상
        if hours <= 6 and view_growth >= 5000:
            tg.send_message(
                f"🚀 <b>초기 급등 감지!</b>\n\n"
                f"📺 {target['title']}\n"
                f"⏰ 업로드 후 {hours:.1f}시간\n"
                f"📈 이번 시간 +{view_growth:,}회\n"
                f"👁 누적 {fresh.get('view_count', 0):,}회"
            )


def run_own_channel_daily() -> bool:
    """자체 채널 새 영상 감지 및 분석 결과를 저장한다."""
    now = datetime.now(config.KST)
    print(f"📺 자체 채널 분석 시작 ({now})")
    start = time.time()

    try:
        raw_videos, channel_info = yt.get_channel_videos(config.OWN_CHANNEL_ID, months_back=1)
        if not raw_videos and not channel_info:
            raise RuntimeError("자체 채널 조회 결과 없음")
        detailed = yt.get_video_details([v["video_id"] for v in raw_videos], min_duration=0) if raw_videos else []
        cutoff = now - timedelta(days=7)
        recent = [
            video for video in detailed
            if cutoff < datetime.fromisoformat(video["published_at"].replace("Z", "+00:00")) <= now
        ]
        existing = db.get_own_channel_state([v["video_id"] for v in recent])
        pending = [
            v for v in recent
            if v["video_id"] not in existing
            or (v.get("comment_count", 0) > 0
                and "error" in (existing[v["video_id"]].get("deepseek_raw") or {}))
        ]
    except Exception as exc:
        error = safe_error(exc)
        print(f"❌ 자체 채널 수집 실패: {error}")
        tg.send_error_alert(error, "자체 채널 수집")
        return False

    if not pending:
        print("📭 새 영상 없음")
        return tg.send_message(f"📺 자체 채널: 새 영상 없음 ({now:%m/%d})")

    print(f"📹 분석 대상 영상 {len(pending)}개")
    success = True
    for video in pending:
        video_id = video["video_id"]
        prior = existing.get(video_id)
        context = f"자체채널 - {video.get('title', '')[:30]}"
        try:
            video["video_type"] = "shorts" if yt.is_shorts(video) else "longform"
            print(f"📹 {video['title'][:50]}")
            if prior:
                gemini_result = prior["gemini_raw"]
            else:
                if not db.save_own_video(video):
                    raise RuntimeError("자체 채널 영상 메타데이터 저장 실패")
                gemini_result = gemini.analyze_own_video(video)
                if not has_text(gemini_result, "topic"):
                    error = gemini_result.get("error") if isinstance(gemini_result, dict) else None
                    raise RuntimeError(error or "Gemini 분석에 필수 항목 topic 누락")

            comments_result = {}
            if video.get("comment_count", 0) > 0:
                try:
                    comments = yt.get_video_comments(
                        video_id, max_comments=config.TOP_COMMENTS_COUNT, raise_on_error=True,
                    )
                    if comments:
                        comments_result = ds.analyze_own_video_comments(
                            video["title"], comments, video["video_type"],
                        )
                        if not has_text(comments_result, "summary"):
                            error = comments_result.get("error") if isinstance(comments_result, dict) else None
                            raise RuntimeError(error or "댓글 분석에 필수 항목 summary 누락")
                except Exception as exc:
                    error = safe_error(exc)
                    print(f"  ⚠️ 댓글 분석 실패 ({video_id}): {error}")
                    comments_result = {"error": error}
                    tg.send_error_alert(error, f"{context} 댓글")
                    success = False
                    if prior:
                        continue

            if not db.save_own_analysis(video_id, gemini_result, comments_result):
                raise RuntimeError("자체 채널 분석 저장 실패")
            sheets.save_own_analysis(video, gemini_result, comments_result)
            if not tg.send_own_channel_alert(video, gemini_result, comments_result):
                raise RuntimeError("자체 채널 분석 알림 전송 실패")
            time.sleep(3)

        except Exception as exc:
            error = safe_error(exc)
            print(f"❌ 영상 처리 실패 ({video_id}): {error}")
            tg.send_error_alert(error, context)
            success = False

    duration = time.time() - start
    status = "완료" if success else "실패 포함"
    print(f"자체 채널 분석 {status} ({duration / 60:.1f}분)")
    return success


# ───────────────────────────────────────────────
# 모드 6: 자체 채널 주간 추적 (매주 월요일)
# ───────────────────────────────────────────────

def _calculate_week_number(published_at_str: str) -> int:
    """업로드일 기준 현재 주차 (7일마다 +1주, 최소 1주)"""
    if not published_at_str:
        return 0
    try:
        published = datetime.fromisoformat(published_at_str.replace('Z', '+00:00'))
        now = datetime.now(timezone.utc)
        days_elapsed = (now - published).days
        return (days_elapsed // 7) + 1
    except Exception:
        return 0


def run_score_backfill():
    """기존 initial/daily 분석 데이터 전체에 전략 스코어 후처리 저장"""
    print(f"📊 전략 스코어 백필 시작 ({datetime.now()})")
    start = time.time()

    initial_rows, daily_rows = db.get_score_backfill_rows()
    total = len(initial_rows) + len(daily_rows)

    if total == 0:
        print("📭 백필할 데이터 없음 (이미 모두 스코어링됨)")
        tg.send_message(f"📊 전략 스코어 백필: 처리할 데이터 없음 ({config.SCORE_VERSION})")
        return

    all_scores = []
    all_videos_map = {}

    for video, analysis in initial_rows:
        try:
            score = scorer.score_video(video, analysis, 'initial_analysis')
            all_scores.append(score)
            if video.get('video_id'):
                all_videos_map[video['video_id']] = video
        except Exception as e:
            print(f"  ⚠️ 스코어링 실패 ({analysis.get('video_id', '')}): {safe_error(e)}")

    for video, analysis in daily_rows:
        try:
            score = scorer.score_video(video, analysis, 'daily_analysis')
            all_scores.append(score)
            if video.get('video_id'):
                all_videos_map[video['video_id']] = video
        except Exception as e:
            print(f"  ⚠️ 스코어링 실패 ({analysis.get('video_id', '')}): {safe_error(e)}")

    if not all_scores:
        print("⚠️ 스코어링 결과 없음")
        tg.send_error_alert("스코어링 결과가 비어 있습니다.", "score-backfill")
        return

    db.save_video_scores(all_scores)
    sheets.save_strategy_scores(all_scores, all_videos_map)

    action_counts = {}
    for s in all_scores:
        a = s.get('recommended_action', '')
        action_counts[a] = action_counts.get(a, 0) + 1

    duration = time.time() - start
    tg.send_message(
        f"📊 <b>전략 스코어 백필 완료</b>\n\n"
        f"📺 처리: {len(all_scores)}개\n"
        f"  ⭐ 바로 기획화: {action_counts.get('바로 기획화', 0)}개\n"
        f"  ✏️ 각색 후 기획: {action_counts.get('각색 후 기획', 0)}개\n"
        f"  📦 아이디어 보관: {action_counts.get('아이디어 보관', 0)}개\n"
        f"  ▽ 우선순위 낮음: {action_counts.get('우선순위 낮음', 0)}개\n"
        f"⏱️ 소요: {duration:.0f}초"
    )
    print(f"✅ 백필 완료 ({len(all_scores)}개, {duration:.0f}초)")


def run_score_sheets_sync():
    """Supabase video_scores → 구글 시트 전략_스코어 탭 재동기화"""
    print(f"📊 스코어 시트 동기화 시작 ({datetime.now()})")
    start = time.time()

    client = db.get_client()
    if not client:
        print("❌ Supabase 연결 실패")
        return

    try:
        result = client.table('video_scores').select('*')\
            .eq('score_version', config.SCORE_VERSION).execute()
        scores = result.data
        if not scores:
            print("📭 동기화할 스코어 없음")
            tg.send_message(f"📊 스코어 동기화: {config.SCORE_VERSION} 데이터 없음")
            return

        video_ids = [s['video_id'] for s in scores]
        vid_res = client.table('videos')\
            .select('video_id,title,channel_title,video_url,view_count,published_at')\
            .in_('video_id', video_ids).execute()
        videos_map = {v['video_id']: v for v in vid_res.data}

        saved = sheets.save_strategy_scores(scores, videos_map)
        duration = time.time() - start
        tg.send_message(
            f"📊 <b>전략 스코어 시트 동기화 완료</b>\n\n"
            f"📺 Supabase 스코어: {len(scores)}개\n"
            f"✅ 시트 신규 추가: {saved}개\n"
            f"⏱️ 소요: {duration:.0f}초"
        )
        print(f"✅ 동기화 완료: {saved}개 추가 ({duration:.0f}초)")

    except Exception as e:
        print(f"❌ 동기화 실패: {safe_error(e)}")
        tg.send_error_alert(safe_error(e), "score-sheets-sync")


def run_own_channel_backfill():
    """Supabase에 저장된 자체 채널 분석 결과 → 구글 시트 재저장"""
    print(f"📦 자체 채널 백필 시작")
    analyses = db.get_all_own_analyses()
    if not analyses:
        print("❌ Supabase에 데이터 없음")
        return

    print(f"📋 {len(analyses)}개 발견")
    for a in analyses:
        v = a.get('own_channel_videos') or {}
        video = {
            'video_id':     a.get('video_id', ''),
            'title':        v.get('title', ''),
            'published_at': str(v.get('published_at', '')),
            'video_type':   v.get('video_type', 'longform'),
            'view_count':   v.get('view_count', 0),
            'like_count':   v.get('like_count', 0),
            'comment_count':v.get('comment_count', 0),
            'duration':     v.get('duration', ''),
            'video_url':    v.get('video_url', ''),
        }
        gemini   = a.get('gemini_raw') or {}
        comments = a.get('deepseek_raw') or {}
        sheets.save_own_analysis(video, gemini, comments)
        print(f"  ✅ {video['title'][:45]}")

    print(f"✅ 백필 완료: {len(analyses)}개 → 구글 시트")


def run_own_channel_track():
    """매주 월요일 - 업로드일 기준 주차로 5주까지 스냅샷 추적"""
    print(f"📊 자체 채널 추적 시작 ({datetime.now()})")
    start = time.time()

    tracking_videos = db.get_tracking_videos()
    if not tracking_videos:
        print("📭 추적 중인 영상 없음")
        tg.send_own_tracking_summary([])
        return

    print(f"📋 추적 대상: {len(tracking_videos)}개")
    results = []

    for tracked in tracking_videos:
        video_id = tracked['video_id']
        try:
            # published_at 기준으로 현재 주차 계산
            week_number = _calculate_week_number(str(tracked.get('published_at', '')))

            if week_number < 1:
                print(f"  ⏩ 아직 1주 미만: {tracked['title'][:30]}")
                continue

            if week_number > config.OWN_CHANNEL_TRACKING_WEEKS:
                db.deactivate_tracking(video_id)
                continue

            # 이번 주차 스냅샷이 이미 있으면 skip (중복 방지)
            if db.get_snapshot_by_week(video_id, week_number):
                print(f"  ⏩ 주차{week_number} 이미 저장: {tracked['title'][:30]}")
                continue

            details = yt.get_video_details([video_id], min_duration=0)
            if not details:
                print(f"  ⚠️ 영상 정보 없음: {video_id}")
                continue
            current = details[0]

            last_snap = db.get_last_snapshot(video_id)
            prev_views = last_snap['view_count'] if last_snap else tracked.get('view_count', 0)

            # 1주, 2주, 5주차만 댓글 분석 (API 절약)
            comment_analysis = None
            if week_number in [1, 2, 5] and current.get('comment_count', 0) > 0:
                comments = yt.get_video_comments(video_id, max_comments=100)
                if comments:
                    comment_analysis = ds.analyze_own_video_comments(
                        tracked['title'], comments, tracked.get('video_type', 'longform')
                    )

            db.save_own_snapshot(video_id, week_number, current, prev_views, comment_analysis)
            db.update_own_video_stats(video_id, current)

            if week_number >= config.OWN_CHANNEL_TRACKING_WEEKS:
                db.deactivate_tracking(video_id)

            results.append({
                'title': tracked['title'],
                'video_type': tracked.get('video_type', 'longform'),
                'published_at': str(tracked.get('published_at', '')),
                'week_number': week_number,
                'view_count': current.get('view_count', 0),
                'view_growth': current.get('view_count', 0) - prev_views,
            })
            time.sleep(1)

        except Exception as e:
            print(f"⚠️ 추적 실패 ({video_id}): {safe_error(e)}")

    if results:
        sheets.save_own_tracking(results)

    tg.send_own_tracking_summary(results)

    duration = time.time() - start
    print(f"✅ 자체 채널 추적 완료 ({duration:.0f}초, {len(results)}개)")


# ───────────────────────────────────────────────
# 모드 7: 스냅샷 수집 (매일 — AI 없음)
# ───────────────────────────────────────────────

def _build_snapshot(video_meta: dict, fresh: dict, prev: dict | None, today: str) -> dict:
    """영상 1개 스냅샷 row 생성"""
    try:
        pub = datetime.fromisoformat(
            str(video_meta.get('published_at', '')).replace('Z', '+00:00')
        )
        days_since = (datetime.now(timezone.utc) - pub).days
    except Exception:
        days_since = 0

    view_now  = fresh.get('view_count', 0)
    like_now  = fresh.get('like_count', 0)
    comm_now  = fresh.get('comment_count', 0)

    prev_view = prev['view_count'] if prev else view_now
    prev_like = prev['like_count'] if prev else like_now
    prev_comm = prev['comment_count'] if prev else comm_now

    view_growth = view_now - prev_view
    like_growth = like_now - prev_like
    comm_growth = comm_now - prev_comm

    view_growth_rate = round(view_growth / prev_view * 100, 2) if prev_view else 0
    comm_growth_rate = round(comm_growth / prev_comm * 100, 2) if prev_comm else 0

    return {
        'video_id':          fresh['video_id'],
        'channel_title':     fresh.get('channel_title') or video_meta.get('channel_title', ''),
        'snapshot_date':     today,
        'days_since_publish': days_since,
        'view_count':        view_now,
        'like_count':        like_now,
        'comment_count':     comm_now,
        'view_growth':       view_growth,
        'like_growth':       like_growth,
        'comment_growth':    comm_growth,
        'view_growth_rate':  view_growth_rate,
        'comment_growth_rate': comm_growth_rate,
        'source_type':       video_meta.get('source_type', 'competitor'),
    }


def run_snapshot_collect():
    """
    경쟁채널 + 자체채널 전체 영상 stats 갱신 + 시계열 스냅샷 저장.
    stats-refresh를 대체하는 통합 모드.
    """
    print(f"📸 스냅샷 수집 시작 ({datetime.now()})")
    start = time.time()
    today = datetime.now(config.KST).date().isoformat()

    all_metas = db.get_all_videos_for_snapshot()
    if not all_metas:
        print("📭 대상 영상 없음")
        tg.send_message("📸 스냅샷: 대상 영상 없음")
        return

    print(f"📋 대상: {len(all_metas)}개 영상")
    meta_map = {v['video_id']: v for v in all_metas}
    all_ids  = list(meta_map.keys())
    api_calls = (len(all_ids) - 1) // 50 + 1

    # YouTube API로 최신 stats 수집
    fresh_videos = yt.get_video_details(all_ids, min_duration=0)
    if not fresh_videos:
        print("⚠️ YouTube API 응답 없음")
        return

    fresh_map = {v['video_id']: v for v in fresh_videos}

    # videos 테이블 일괄 upsert (stats 최신화)
    comp_fresh = [v for v in fresh_videos if meta_map.get(v['video_id'], {}).get('source_type') == 'competitor']
    if comp_fresh:
        db.save_videos(comp_fresh)

    # 스냅샷 row 생성 (직전 스냅샷 조회해서 growth 계산)
    snapshots = []
    for vid_id, fresh in fresh_map.items():
        meta = meta_map.get(vid_id, {})
        prev = db.get_last_video_snapshot(vid_id)
        # 오늘 이미 저장된 스냅샷이면 skip
        if prev and prev.get('snapshot_date') == today:
            continue
        snapshots.append(_build_snapshot(meta, fresh, prev, today))

    saved = db.save_video_snapshots(snapshots)

    duration = time.time() - start
    print(f"✅ 스냅샷 완료: {saved}개 저장 / API {api_calls}호출 / {duration:.0f}초")
    tg.send_message(
        f"📸 스냅샷 수집 완료\n"
        f"🎬 {saved}개 저장 / API {api_calls}호출 / {duration:.0f}초"
    )


def run_stats_refresh():
    """경쟁채널 전체 영상 조회수·좋아요·댓글수 갱신 (YouTube API만, AI 없음)"""
    print(f"📊 통계 갱신 시작 ({datetime.now()})")
    start = time.time()

    video_ids = db.get_all_tracked_video_ids()
    if not video_ids:
        print("📭 갱신할 영상 없음")
        tg.send_message("📊 통계 갱신: Supabase에 영상 없음")
        return

    api_calls = (len(video_ids) - 1) // 50 + 1
    print(f"📋 갱신 대상: {len(video_ids)}개 영상 (YouTube API {api_calls}호출)")

    fresh_videos = yt.get_video_details(video_ids, min_duration=0)
    saved = db.save_videos(fresh_videos)

    duration = time.time() - start
    print(f"✅ 통계 갱신 완료: {saved}개 / {api_calls}호출 / {duration:.0f}초")
    tg.send_message(
        f"📊 통계 갱신 완료\n"
        f"🎬 {saved}개 영상 / API {api_calls}호출 / {duration:.0f}초"
    )


def run_thumbnail_backfill():
    """HIT + IRREGULAR + top priority 영상 썸네일 Vision 분석 (Gemini Pro)"""
    print(f"🖼️ 썸네일 분석 시작 ({datetime.now()})")
    start = time.time()

    targets = db.get_videos_for_thumbnail_analysis(mode='full_initial', limit=500)
    if not targets:
        print("📭 분석 대상 없음 (이미 모두 완료됐거나 qualifying 영상 없음)")
        tg.send_message("🖼️ 썸네일 분석: 대상 없음")
        return

    print(f"📋 분석 대상: {len(targets)}개")
    success, errors = 0, 0

    for i, video in enumerate(targets):
        thumbnail_url = video.get('thumbnail_url', '')
        if not thumbnail_url:
            errors += 1
            continue
        try:
            result = gemini.analyze_thumbnail(thumbnail_url, video)
            if 'error' in result:
                print(f"  ❌ [{i+1}/{len(targets)}] {result['error']} — {video.get('title','')[:40]}")
                errors += 1
            else:
                db.save_thumbnail_analysis(video['video_id'], result)
                print(f"  ✅ [{i+1}/{len(targets)}] {video.get('title','')[:45]}")
                success += 1
            time.sleep(3)
        except Exception as e:
            print(f"  ❌ 예외 ({video.get('video_id','')}): {safe_error(e)}")
            errors += 1

    duration = time.time() - start
    print(f"✅ 썸네일 분석 완료: 성공 {success}개 / 실패 {errors}개 / {duration/60:.1f}분")
    tg.send_message(
        f"🖼️ 썸네일 분석 완료\n"
        f"✅ 성공 {success}개 / ❌ 실패 {errors}개 / {duration/60:.1f}분"
    )


# ───────────────────────────────────────────────
# 모드 8: 성장 패턴 업데이트 (스냅샷 데이터 후처리)
# ───────────────────────────────────────────────

def run_growth_pattern_update():
    """video_snapshots D1/D7/D30 비교 → growth_pattern 자동 분류"""
    print(f"📈 성장 패턴 분류 시작 ({datetime.now()})")
    updated = db.update_growth_patterns()
    print(f"✅ 성장 패턴 업데이트: {updated}개")
    tg.send_message(f"📈 성장 패턴 분류 완료: {updated}개 영상 업데이트")


# ───────────────────────────────────────────────
# 모드 A: 미분석 영상 전체 분석 (full-backfill)
# ───────────────────────────────────────────────

def run_full_backfill():
    """
    videos 테이블에 있지만 initial_analysis가 없는 영상 전부 분석.
    Gemini 3.1 Pro 영상분석 + 썸네일 Vision + DeepSeek 댓글분석 → initial_analysis 저장.
    """
    print(f"🔬 전체 백필 시작 ({datetime.now()})")
    start = time.time()

    targets = db.get_unanalyzed_videos()
    if not targets:
        print("📭 미분석 영상 없음")
        tg.send_message("🔬 full-backfill: 미분석 영상 없음 (모두 완료)")
        return

    print(f"📋 대상: {len(targets)}개")

    # 채널별 평균 조회수 계산 (is_hit 태그용)
    from collections import defaultdict
    ch_views: dict[str, list] = defaultdict(list)
    for v in targets:
        ch_views[v.get('channel_title', '')].append(v.get('view_count', 0))
    ch_avg = {ch: sum(vs)/len(vs) for ch, vs in ch_views.items() if vs}

    for v in targets:
        avg = ch_avg.get(v.get('channel_title', ''), 0)
        v['channel_avg_views'] = int(avg)
        v['is_hit'] = avg > 0 and v.get('view_count', 0) >= avg * config.HIT_VIDEO_MULTIPLIER

    # Gemini 영상 분석
    print(f"\n🤖 Gemini 영상분석 ({len(targets)}개, model=initial)")
    gemini_results = gemini.analyze_videos_batch(targets, delay=5, model_mode='initial')

    # 댓글 + DeepSeek 분석 → buffer 저장
    buffer: list[dict] = []
    total_saved = 0
    success, errors = 0, 0

    for i, video in enumerate(targets):
        g_result = next(
            (g for g in gemini_results if g.get('video_id') == video['video_id']),
            {'error': 'Gemini 매칭 실패'}
        )
        analysis = analyze_video_complete(video, g_result)
        analysis['is_hit'] = video.get('is_hit', False)
        analysis['channel_avg_views'] = video.get('channel_avg_views', 0)
        buffer.append(analysis)

        if 'error' not in g_result:
            success += 1
        else:
            errors += 1

        print(f"  [{i+1}/{len(targets)}] {'⭐' if video.get('is_hit') else '  '} {video.get('title','')[:45]}")

        if len(buffer) >= 10:
            _save_analyses(buffer, 'initial', batch_num=0)
            total_saved += len(buffer)
            print(f"  💾 중간 저장 ({total_saved}개 누적)")
            buffer = []
            time.sleep(1)

    if buffer:
        _save_analyses(buffer, 'initial', batch_num=0)
        total_saved += len(buffer)

    # 썸네일 backfill (같은 대상)
    print(f"\n🖼️ 썸네일 Vision 분석 ({len(targets)}개)")
    thumb_ok, thumb_err = 0, 0
    for i, video in enumerate(targets):
        thumbnail_url = video.get('thumbnail_url', '')
        if not thumbnail_url:
            thumb_err += 1
            continue
        try:
            result = gemini.analyze_thumbnail(thumbnail_url, video)
            if 'error' not in result:
                db.save_thumbnail_analysis(video['video_id'], result)
                thumb_ok += 1
                print(f"  🖼️ [{i+1}/{len(targets)}] {video.get('title','')[:45]}")
            else:
                thumb_err += 1
            time.sleep(3)
        except Exception as e:
            print(f"  ❌ 썸네일 실패 ({video.get('video_id','')}): {safe_error(e)}")
            thumb_err += 1

    # 전략 스코어
    all_analyses = buffer  # buffer는 이미 비었으니 전체 재구성 불필요 — 이미 저장됨
    # (스코어는 score-backfill로 별도 실행)

    duration = time.time() - start
    msg = (
        f"🔬 <b>full-backfill 완료</b>\n\n"
        f"📺 대상: {len(targets)}개\n"
        f"✅ 분석 저장: {total_saved}개\n"
        f"🖼️ 썸네일: {thumb_ok}개 / ❌ {thumb_err}개\n"
        f"⏱️ {duration/60:.1f}분"
    )
    tg.send_message(msg)
    print(f"\n✅ full-backfill 완료 ({duration/60:.1f}분)")


# ───────────────────────────────────────────────
# 모드 B: 댓글 재분석 백필 (comment-backfill)
# ───────────────────────────────────────────────

def run_history_extend(extra_months: int = 6):
    """
    각 채널의 기존 수집 기간 이전 extra_months개월치 추가 수집.
    이미 DB에 있는 영상은 완전히 skip — 중복 없음.
    """
    print(f"📅 히스토리 확장 시작 ({extra_months}개월 추가, {datetime.now()})")
    start = time.time()

    all_new: list[dict] = []
    channel_videos_map: dict[str, list[dict]] = {}

    for name, channel_id in config.TARGET_CHANNELS.items():
        try:
            oldest = db.get_oldest_video_date_by_channel(name)
            if not oldest:
                print(f"  ⚠️ {name}: 기존 데이터 없음, 스킵")
                continue

            # oldest 이전 구간만 수집 (oldest 이후는 before_date로 skip)
            months_total = int((datetime.now(timezone.utc) - oldest).days / 30) + extra_months
            print(f"\n  📺 {name}: oldest={oldest.date()} → {months_total}개월치 범위로 수집")

            raw_videos, channel_info = yt.get_channel_videos(
                channel_id,
                months_back=months_total,
                before_date=oldest
            )
            if not raw_videos:
                print(f"  📭 {name}: 추가 영상 없음")
                continue

            video_ids = [v['video_id'] for v in raw_videos]
            detailed = yt.get_video_details(video_ids)
            print(f"  ✅ {name}: {len(detailed)}개 신규 발견")

            all_new.extend(detailed)
            channel_videos_map[name] = detailed
            time.sleep(2)

        except Exception as e:
            print(f"  ❌ {name}: {safe_error(e)}")
            tg.send_error_alert(safe_error(e), f"history-extend - {name}")

    if not all_new:
        print("📭 추가 수집 영상 없음")
        tg.send_message("📅 히스토리 확장: 추가 수집 영상 없음")
        return

    # 채널 평균 조회수 → is_hit 태그
    from collections import defaultdict
    ch_views: dict[str, list] = defaultdict(list)
    for v in all_new:
        ch_views[v.get('channel_title', '')].append(v.get('view_count', 0))
    ch_avg = {ch: sum(vs)/len(vs) for ch, vs in ch_views.items() if vs}
    for v in all_new:
        avg = ch_avg.get(v.get('channel_title', ''), 0)
        v['channel_avg_views'] = int(avg)
        v['is_hit'] = avg > 0 and v.get('view_count', 0) >= avg * config.HIT_VIDEO_MULTIPLIER

    sheets.save_videos(all_new)
    db.save_videos(all_new)
    print(f"\n💾 videos 저장: {len(all_new)}개")

    # Gemini 분석
    print(f"\n🤖 Gemini 분석 ({len(all_new)}개)")
    gemini_results = gemini.analyze_videos_batch(all_new, delay=5, model_mode='initial')

    buffer: list[dict] = []
    total_saved = 0

    for i, video in enumerate(all_new):
        g_result = next(
            (g for g in gemini_results if g.get('video_id') == video['video_id']),
            {'error': 'Gemini 매칭 실패'}
        )
        analysis = analyze_video_complete(video, g_result)
        analysis['is_hit'] = video.get('is_hit', False)
        analysis['channel_avg_views'] = video.get('channel_avg_views', 0)
        buffer.append(analysis)
        print(f"  [{i+1}/{len(all_new)}] {'⭐' if video.get('is_hit') else '  '} {video.get('title','')[:45]}")

        if len(buffer) >= 10:
            _save_analyses(buffer, 'initial', batch_num=0)
            total_saved += len(buffer)
            print(f"  💾 중간 저장 ({total_saved}개 누적)")
            buffer = []
            time.sleep(1)

    if buffer:
        _save_analyses(buffer, 'initial', batch_num=0)
        total_saved += len(buffer)

    _score_and_save([], all_new, 'initial_analysis')

    duration = time.time() - start
    tg.send_message(
        f"📅 <b>히스토리 확장 완료</b>\n\n"
        f"📺 신규 영상: {len(all_new)}개\n"
        f"💾 분석 저장: {total_saved}개\n"
        f"⏱️ {duration/60:.1f}분"
    )
    print(f"\n✅ 히스토리 확장 완료 ({len(all_new)}개, {duration/60:.1f}분)")


def run_comment_backfill():
    """
    initial_analysis 전체에서 emotion_temperature 없는 영상 댓글 재분석.
    YouTube 댓글 재수집 + DeepSeek analyze_comments() → deepseek_raw 갱신.
    """
    print(f"💬 댓글 재분석 백필 시작 ({datetime.now()})")
    start = time.time()

    targets = db.get_videos_needing_comment_reanalysis()
    if not targets:
        print("📭 재분석 대상 없음 (모두 emotion_temperature 존재)")
        tg.send_message("💬 comment-backfill: 처리할 영상 없음")
        return

    print(f"📋 대상: {len(targets)}개")
    success, skipped, errors = 0, 0, 0

    for i, target in enumerate(targets):
        video_id = target['video_id']
        title = target.get('title', '')
        comment_count = target.get('comment_count', 0)

        if comment_count == 0:
            skipped += 1
            print(f"  [{i+1}/{len(targets)}] ⏩ 댓글 없음: {title[:40]}")
            continue

        try:
            comments = yt.get_video_comments(video_id, max_comments=config.TOP_COMMENTS_COUNT)
            if not comments:
                skipped += 1
                print(f"  [{i+1}/{len(targets)}] ⚠️ 댓글 수집 실패: {title[:40]}")
                continue

            result = ds.analyze_comments(title, comments)
            if 'error' in result:
                errors += 1
                print(f"  [{i+1}/{len(targets)}] ❌ DeepSeek: {result['error'][:50]}")
                continue

            db.update_comment_analysis(video_id, result)
            success += 1
            emo = result.get('dominant_emotion', '?')
            alg = result.get('algorithm_discovery_rate', '?')
            print(f"  [{i+1}/{len(targets)}] ✅ {title[:35]} | {emo} | alg {alg}%")
            time.sleep(2)

        except Exception as e:
            errors += 1
            print(f"  [{i+1}/{len(targets)}] ❌ 예외: {safe_error(e)}")

        if (i + 1) % 20 == 0:
            elapsed = time.time() - start
            print(f"\n  📊 중간: 완료 {success} / 스킵 {skipped} / 에러 {errors} ({elapsed/60:.1f}분)\n")

    duration = time.time() - start
    print(f"\n✅ 댓글 백필 완료: {success}개 / 스킵 {skipped}개 / 에러 {errors}개 / {duration/60:.1f}분")
    tg.send_message(
        f"💬 <b>comment-backfill 완료</b>\n\n"
        f"✅ {success}개 / ⏩ {skipped}개 / ❌ {errors}개\n"
        f"⏱️ {duration/60:.1f}분"
    )


# ───────────────────────────────────────────────
# 모드 9: 주간 채널 리포트
# ───────────────────────────────────────────────

def run_weekly_report():
    print("📊 주간 채널 리포트 생성 시작")
    today = datetime.now(config.KST).date()
    report_date = today - timedelta(days=today.weekday())
    try:
        # 1. 성장 패턴 최신화
        print("  📈 성장 패턴 분류 중...")
        db.update_growth_patterns()

        # 2. 최신 stats 기준 영상 조회
        recent = db.get_recent_videos_from_db(days_back=7, before=report_date)
        if not recent:
            raise RuntimeError("지난주 경쟁 채널 영상 데이터가 없습니다")

        from collections import defaultdict
        ch_map: dict[str, list] = defaultdict(list)
        for v in recent:
            ch_map[v.get("channel_title", "")].append({
                "title":      v.get("title", ""),
                "view_count": v.get("view_count", 0),
            })

        # 3. 채널별 성공공식
        insights: dict[str, dict] = {}
        for ch, vids in ch_map.items():
            if len(vids) >= 5:
                insight = ds.derive_channel_success_formula(ch, vids)
                if has_text(insight, "success_formula"):
                    insights[ch] = insight
                time.sleep(2)

        top = sorted(
            [{"video_id": v["video_id"], "title": v.get("title",""), "channel_title": v.get("channel_title",""),
              "view_count": v.get("view_count", 0)} for v in recent],
            key=lambda x: x["view_count"], reverse=True
        )[:20]

        # 4. 기존 주간 리포트
        report = ds.generate_weekly_report(insights, top)
        if not has_text(report, "week_summary"):
            error = report.get("error") if isinstance(report, dict) else None
            raise RuntimeError(error or "주간 리포트 요약이 비어 있습니다")
        report = WeeklyReport.model_validate(report).model_dump()
        if not db.save_weekly_report(report, report_date):
            raise RuntimeError("Supabase 주간 리포트 저장을 확인하지 못했습니다")
        sheets.save_weekly_report(report, report_date)
        sheets.save_channel_insights(insights)
        db.save_channel_insights(insights)

        # 5. 시장 변화 분석 (market intelligence)
        print("  🧠 시장 변화 분석 중...")
        snapshot_summary = db.get_snapshot_growth_summary(days_back=7, before=report_date)
        emotion_summary = db.get_emotion_summary(days_back=7, before=report_date)

        # top 영상에 growth_pattern 병합
        snap_rows = snapshot_summary.get('top_growing', [])
        snap_map = {r['video_id']: r.get('growth_pattern') for r in snap_rows}
        for v in top:
            v['growth_pattern'] = snap_map.get(v.get('video_id', ''), '')

        if snapshot_summary.get("total_snapshots"):
            market_state = ds.generate_market_intelligence(snapshot_summary, emotion_summary, top)
        else:
            market_state = {"error": "지난주 스냅샷 없음"}
        if has_text(market_state, "market_summary"):
            try:
                market_state = MarketAnalysis.model_validate(market_state).model_dump()
            except ValueError as exc:
                market_state = {"error": safe_error(exc)}
        market_saved = has_text(market_state, "market_summary") and db.save_weekly_market_state(market_state, report_date)
        if not market_saved:
            print(f"  ⚠️ 시장 변화 분석 미완료: {safe_error(market_state.get('error', '결과 검증 또는 저장 실패'))}")

        top_priority = db.get_top_priority_videos(limit=5)
        delivered = tg.send_weekly_report_alert(
            report, top_priority, market_state if market_saved else None, market_missing=not market_saved
        )

        # 채널별 광고 현황
        ad_summary = db.get_channel_ad_boost_summary()
        if ad_summary and delivered:
            delivered = tg.send_ad_boost_summary(ad_summary)
        if not delivered or not market_saved:
            print("⚠️ 주간 채널 리포트 부분 실패")
            return False
        print("✅ 주간 채널 리포트 완료")
        return True
    except Exception as e:
        error = safe_error(e)
        print(f"❌ 주간 리포트 오류: {error}")
        tg.send_error_alert(error, "주간 리포트")
        return False


# ───────────────────────────────────────────────
# 진입점
# ───────────────────────────────────────────────

def run_b_track(mode, args) -> bool:
    try:
        if mode in {"b-track-init", "b-track-collect", "b-track-run"}:
            import b_track_collector

            if mode == "b-track-init":
                b_track_collector.init_seed_sources()
            else:
                b_track_collector.run_collect()
        if mode in {"b-track-scan", "b-track-cluster", "b-track-run"}:
            import b_track_gemini

            if mode != "b-track-cluster":
                b_track_gemini.run_broad_scan()
            if mode != "b-track-scan":
                b_track_gemini.run_cluster()
        if mode == "b-track-score":
            import b_track_scorer

            max_entities = int(args[0]) if args else 500
            b_track_scorer.run_score(max_entities=max_entities)
        if mode == "b-track-discover":
            import b_track_discovery

            b_track_discovery.run_discovery()
    except Exception as exc:
        print(f"❌ {mode} 실패: {safe_error(exc)}")
        return False
    return True


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", line_buffering=True)
    if len(sys.argv) < 2:
        print("사용법: python main.py [initial-batch N|daily|trend-collect|trend-weekly|weekly]")
        sys.exit(1)

    mode = sys.argv[1].lower()
    if not validate_env(mode):
        sys.exit(1)

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
        sys.exit(0 if run_daily_collection() else 1)
    elif mode == "trend-collect":
        sys.exit(0 if run_trend_collect() else 1)
    elif mode == "trend-weekly":
        sys.exit(0 if run_trend_weekly() else 1)
    elif mode == "weekly":
        sys.exit(0 if run_weekly_report() else 1)
    elif mode == "own-hourly":
        run_own_channel_hourly_snapshot()
    elif mode == "own-channel":
        sys.exit(0 if run_own_channel_daily() else 1)
    elif mode == "own-track":
        run_own_channel_track()
    elif mode == "own-backfill":
        run_own_channel_backfill()
    elif mode == "score-backfill":
        run_score_backfill()
    elif mode == "score-sheets-sync":
        run_score_sheets_sync()
    elif mode == "stats-refresh":
        run_stats_refresh()
    elif mode == "growth-pattern":
        run_growth_pattern_update()
    elif mode == "snapshot-collect":
        run_snapshot_collect()
    elif mode == "thumbnail-backfill":
        run_thumbnail_backfill()
    elif mode == "full-backfill":
        run_full_backfill()
    elif mode == "history-extend":
        months = int(sys.argv[2]) if len(sys.argv) > 2 else 6
        run_history_extend(extra_months=months)
    elif mode == "comment-backfill":
        run_comment_backfill()
    elif mode in {
        "b-track-init", "b-track-collect", "b-track-scan", "b-track-cluster",
        "b-track-score", "b-track-discover", "b-track-run",
    }:
        sys.exit(0 if run_b_track(mode, sys.argv[2:]) else 1)
    # 구버전 호환
    elif mode == "trend":
        print("⚠️ 'trend' → 'trend-collect'로 실행됩니다")
        sys.exit(0 if run_trend_collect() else 1)
    else:
        print(f"❌ 알 수 없는 모드: {mode}")
        sys.exit(1)
