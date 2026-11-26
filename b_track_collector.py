"""
B-track YouTube 수집 모듈
trend_sources에 등록된 채널에서 영상 메타데이터를 수집해 trend_videos에 저장
"""
from googleapiclient.discovery import build
from datetime import datetime, timedelta, timezone
import config
import b_track_config as btc
from b_track_quota import QuotaGuard, QuotaExceeded
from youtube_collector import parse_duration
from supabase import create_client


def get_yt():
    return build('youtube', 'v3', developerKey=config.YOUTUBE_API_KEY)


def get_db():
    return create_client(config.SUPABASE_URL, config.SUPABASE_KEY)


def resolve_channel_id(handle_or_id: str) -> tuple[str, str]:
    """핸들(@name) 또는 채널ID를 실제 channel_id로 변환. (channel_id, title) 반환"""
    yt = get_yt()

    if handle_or_id.startswith('UC'):
        resp = yt.channels().list(id=handle_or_id, part='id,snippet').execute()
        if resp.get('items'):
            item = resp['items'][0]
            return item['id'], item['snippet']['title']
        return None, None

    # @핸들 또는 채널명 → search로 해석
    query = handle_or_id.lstrip('@')
    resp = yt.search().list(
        q=query,
        type='channel',
        part='id,snippet',
        maxResults=1
    ).execute()

    if not resp.get('items'):
        return None, None
    item = resp['items'][0]
    ch_id = item['id'].get('channelId', '')
    title = item['snippet']['title']
    return ch_id, title


def collect_channel_videos(channel_id: str, channel_title: str, guard: QuotaGuard,
                            months_back: int = None) -> list[dict]:
    """채널에서 영상 메타데이터 수집. guard로 유닛 차감(초과 시 QuotaExceeded)."""
    months_back = months_back or btc.COLLECTION_MONTHS
    yt = get_yt()
    cutoff = datetime.now(timezone.utc) - timedelta(days=months_back * 30)

    guard.spend('channels')
    channel_resp = yt.channels().list(
        id=channel_id,
        part='contentDetails'
    ).execute()
    if not channel_resp.get('items'):
        return []

    playlist_id = channel_resp['items'][0]['contentDetails']['relatedPlaylists']['uploads']
    video_ids = []
    page_token = None

    while True:
        guard.spend('playlistItems')
        resp = yt.playlistItems().list(
            playlistId=playlist_id,
            part='snippet,contentDetails',
            maxResults=50,
            pageToken=page_token
        ).execute()

        stop = False
        for item in resp.get('items', []):
            pub = datetime.fromisoformat(
                item['snippet']['publishedAt'].replace('Z', '+00:00')
            )
            if pub < cutoff:
                stop = True
                break
            video_ids.append(item['contentDetails']['videoId'])

        if stop or not resp.get('nextPageToken'):
            break
        page_token = resp['nextPageToken']

    if not video_ids:
        return []

    videos = []
    for i in range(0, len(video_ids), 50):
        batch = video_ids[i:i+50]
        guard.spend('videos')
        stats_resp = yt.videos().list(
            id=','.join(batch),
            part='snippet,statistics,contentDetails'
        ).execute()

        for v in stats_resp.get('items', []):
            view_count = int(v['statistics'].get('viewCount', 0))
            if view_count < btc.MIN_VIEW_COUNT:
                continue

            desc = v['snippet'].get('description', '')
            dur_str = v['contentDetails'].get('duration', '')
            dur_sec = parse_duration(dur_str)
            vtype = 'shorts' if 0 < dur_sec <= btc.SHORTS_MAX_SECONDS else 'longform'
            videos.append({
                'video_id': v['id'],
                'channel_id': channel_id,
                'channel_title': channel_title,
                'title': v['snippet'].get('title', ''),
                'description_short': desc[:500] if desc else '',
                'published_at': v['snippet']['publishedAt'],
                'view_count': view_count,
                'like_count': int(v['statistics'].get('likeCount', 0)),
                'comment_count': int(v['statistics'].get('commentCount', 0)),
                'duration': dur_str,
                'duration_seconds': dur_sec,
                'video_type': vtype,
                'thumbnail_url': v['snippet'].get('thumbnails', {}).get('medium', {}).get('url', ''),
                'source_track': 'trend',
                'broad_scan_done': False,
            })

    return videos


def init_seed_sources():
    """seed 채널들을 trend_sources에 등록 (없는 것만)"""
    db = get_db()
    print("🌱 Seed 소스 초기화 중...")

    for ch in btc.SEED_CHANNELS:
        # 이미 있는지 확인
        existing = db.table('trend_sources').select('id').eq(
            'source_name', ch['source_name']
        ).execute()
        if existing.data:
            print(f"  - {ch['source_name']}: 이미 등록됨")
            continue

        # 채널 ID 해석
        channel_id, resolved_title = resolve_channel_id(ch['source_ref'])
        if not channel_id:
            print(f"  ⚠️ {ch['source_name']}: 채널 ID 해석 실패 ({ch['source_ref']})")
            continue

        db.table('trend_sources').insert({
            'source_type': 'youtube_channel',
            'source_name': ch['source_name'],
            'source_ref': channel_id,  # resolved channel_id 저장
            'category_hint': ch['category_hint'],
            'priority': ch['priority'],
            'active': True,
        }).execute()
        print(f"  ✅ {ch['source_name']} → {channel_id} ({resolved_title})")


def run_collect(max_sources: int = None):
    """활성 소스에서 영상 수집.
    - YouTube 유닛은 QuotaGuard로 차감, 예산 소진 시 즉시 중단·익일 이월 (A-track 침범 금지).
    - 오래 안 본 채널부터(last_collected_at ASC NULLS FIRST), 최근 COLLECT_REFRESH_HOURS 내 수집분은 skip.
    """
    db = get_db()
    guard = QuotaGuard()
    print(f"  시작 {guard.report()}")

    # never-collected(NULL) 먼저 → 그다음 오래된 것부터.
    # (PostgREST asc는 NULLS LAST가 기본이라 두 단계로 명시 분리)
    never = db.table('trend_sources').select('*').eq('active', True) \
        .is_('last_collected_at', 'null').order('priority', desc=True).execute().data
    older = db.table('trend_sources').select('*').eq('active', True) \
        .not_.is_('last_collected_at', 'null') \
        .order('last_collected_at', desc=False).execute().data
    sources = never + older
    if max_sources:
        sources = sources[:max_sources]
    if not sources:
        print("  ⚠️ 등록된 소스 없음. b-track-init 먼저 실행하세요.")
        return 0
    print(f"  대상: never-collected {len(never)}개 + 기수집 {len(older)}개")

    refresh_cutoff = datetime.now(timezone.utc) - timedelta(hours=btc.COLLECT_REFRESH_HOURS)
    total_new, processed, skipped, stopped = 0, 0, 0, False

    for src in sources:
        # 최근 수집한 채널은 이번 회차 skip
        lc = src.get('last_collected_at')
        if lc:
            try:
                if datetime.fromisoformat(lc.replace('Z', '+00:00')) > refresh_cutoff:
                    skipped += 1
                    continue
            except Exception:
                pass

        channel_id = src['source_ref']
        channel_title = src['source_name']

        try:
            videos = collect_channel_videos(channel_id, channel_title, guard)
        except QuotaExceeded as e:
            print(f"\n🛑 유닛 예산 소진 — 수집 중단, 익일 이월 ({channel_title})\n   {e}")
            stopped = True
            break
        except Exception as e:
            print(f"\n⚠️ {channel_title}: 수집 실패, 스킵 ({e})")
            db.table('trend_sources').update({
                'last_collected_at': datetime.now(timezone.utc).isoformat(),
            }).eq('id', src['id']).execute()
            processed += 1
            continue

        if not videos:
            print(f"\n📡 {channel_title}: 영상 없음 ({guard.report()})")
            db.table('trend_sources').update({
                'last_collected_at': datetime.now(timezone.utc).isoformat(),
            }).eq('id', src['id']).execute()
            processed += 1
            continue

        # 타입별(숏폼/롱폼) baseline을 수집분에서 직접 계산 (별도 API 호출 없음, 유닛 절약)
        type_sum, type_cnt = {}, {}
        for v in videos:
            t = v['video_type']
            type_sum[t] = type_sum.get(t, 0) + v['view_count']
            type_cnt[t] = type_cnt.get(t, 0) + 1
        type_avg = {t: type_sum[t] / type_cnt[t] for t in type_cnt}

        for v in videos:
            avg_t = type_avg.get(v['video_type'], 0)
            v['channel_avg_views'] = round(avg_t)
            v['view_over_baseline'] = round(v['view_count'] / avg_t, 2) if avg_t > 0 else 1.0

        n_short = type_cnt.get('shorts', 0)
        n_long = type_cnt.get('longform', 0)
        print(f"\n📡 {channel_title}: 영상 {len(videos)}개 (숏폼 {n_short}/롱폼 {n_long}) "
              f"평균 숏폼 {round(type_avg.get('shorts',0)):,}/롱폼 {round(type_avg.get('longform',0)):,} ({guard.report()})")

        # 이미 있는 video_id 제외
        existing_ids = set()
        for i in range(0, len(videos), 100):
            batch_ids = [v['video_id'] for v in videos[i:i+100]]
            resp = db.table('trend_videos').select('video_id').in_('video_id', batch_ids).execute()
            existing_ids.update(r['video_id'] for r in resp.data)

        seen = set()
        new_videos = []
        for v in videos:
            if v['video_id'] not in existing_ids and v['video_id'] not in seen:
                seen.add(v['video_id'])
                new_videos.append(v)

        if new_videos:
            for i in range(0, len(new_videos), 100):
                db.table('trend_videos').insert(new_videos[i:i+100]).execute()
            total_new += len(new_videos)
            print(f"  ✅ 신규 {len(new_videos)}개 저장")
        else:
            print("  모두 기존 수집됨")

        db.table('trend_sources').update({
            'last_collected_at': datetime.now(timezone.utc).isoformat(),
            'total_collected': src.get('total_collected', 0) + len(new_videos),
        }).eq('id', src['id']).execute()
        processed += 1

    tag = "예산소진 중단" if stopped else "완료"
    print(f"\n✅ 수집 {tag}: 처리 {processed}채널 / skip {skipped} / 신규 {total_new}개 / {guard.report()}")
    return total_new
