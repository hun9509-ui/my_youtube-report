"""
B-track 시드 채널 발굴 파이프라인

단계0: DB 내부 발굴 (유닛 0) — trend_videos에 있으나 trend_sources에 없는 채널
단계1: search 후보수집 (search 100유닛/회, 예산가드 내 최대)
단계2: 사실조회 (channels.list/playlistItems 1유닛/회, 헤프게)
단계3: Gemini 분류/점수화 (크레딧)
단계4: 검증표 출력 + trend_source_candidates 저장 (자동 등록 절대 안 함)

원칙:
- YouTube API = 사실 조회만. Gemini = 판단만. 섞지 않는다.
- A-track 채널(경쟁사+자체)은 후보에서 제외 (트랙 분리).
- trend_sources 자동 insert 금지. 사람이 표 보고 승인한 것만 별도 등록.
"""
import json
import re
import time
from datetime import datetime, timezone

import vertexai
from vertexai.generative_models import GenerativeModel
from supabase import create_client

import config
import b_track_config as btc
from b_track_collector import get_yt
from b_track_quota import QuotaGuard, QuotaExceeded

_vertex_initialized = False


def _init_vertex():
    global _vertex_initialized
    if not _vertex_initialized:
        vertexai.init(project=config.GOOGLE_CLOUD_PROJECT, location=config.GOOGLE_CLOUD_LOCATION)
        _vertex_initialized = True


def get_db():
    return create_client(config.SUPABASE_URL, config.SUPABASE_KEY)


def _atrack_channel_ids() -> set:
    """A-track 채널(경쟁사 + 자체) — 후보에서 제외"""
    ids = set(config.TARGET_CHANNELS.values())
    ids.add(config.OWN_CHANNEL_ID)
    return ids


def _existing_exclusions(db) -> set:
    """이미 시드거나 이미 후보로 점수화된 채널 — 재발굴 제외"""
    excl = set()
    seeds = db.table('trend_sources').select('source_ref').execute()
    excl.update(s['source_ref'] for s in seeds.data)
    cands = db.table('trend_source_candidates').select('channel_id').execute()
    excl.update(c['channel_id'] for c in cands.data)
    return excl


# ─────────────────────────────────────────────
# 단계 0: DB 내부 발굴 (유닛 0)
# ─────────────────────────────────────────────
def stage0_db_internal(db, exclude: set) -> dict:
    """trend_videos에 등장하지만 시드/후보가 아닌 채널.
    반환: {channel_id: {'channel_title':.., 'paths':['stage0']}}"""
    rows = db.table('trend_videos').select('channel_id,channel_title').execute()
    found = {}
    for r in rows.data:
        cid = r['channel_id']
        if cid in exclude or cid in _atrack_channel_ids():
            continue
        if cid not in found:
            found[cid] = {'channel_title': r.get('channel_title', ''), 'paths': ['stage0']}
    print(f"  단계0(DB내부): 후보 {len(found)}개")
    return found


# ─────────────────────────────────────────────
# 단계 1: search 후보수집 (예산가드)
# ─────────────────────────────────────────────
def stage1_search(guard: QuotaGuard, exclude: set, found: dict) -> dict:
    """키워드별 search로 채널 후보 수집. search 예산 소진 시 중단."""
    yt = get_yt()
    atrack = _atrack_channel_ids()
    keywords = btc.DISCOVERY_KEYWORDS
    searched = 0

    for kw in keywords:
        if not guard.can_spend('search'):
            print(f"  단계1: search 예산 소진 — '{kw}'부터 익일 이월 (검색 {searched}회 완료)")
            break
        try:
            guard.spend('search')
        except QuotaExceeded:
            print(f"  단계1: 예산 초과로 중단 ({kw})")
            break

        try:
            resp = yt.search().list(
                q=kw, type='video', part='snippet',
                maxResults=btc.DISCOVERY_SEARCH_MAX_RESULTS,
                order='relevance', regionCode=btc.DISCOVERY_REGION,
                relevanceLanguage=btc.DISCOVERY_RELEVANCE_LANG,
            ).execute()
        except Exception as e:
            print(f"  단계1: '{kw}' search 실패: {e}")
            continue

        searched += 1
        new_in_kw = 0
        for item in resp.get('items', []):
            sn = item.get('snippet', {})
            cid = sn.get('channelId', '')
            if not cid or cid in exclude or cid in atrack:
                continue
            path = f"search:{kw}"
            if cid in found:
                if path not in found[cid]['paths']:
                    found[cid]['paths'].append(path)
            else:
                found[cid] = {'channel_title': sn.get('channelTitle', ''), 'paths': [path]}
                new_in_kw += 1
        print(f"  단계1: '{kw}' → 신규채널 {new_in_kw}개 ({guard.report()})")
        time.sleep(0.3)

    print(f"  단계1 합계: 누적 후보 {len(found)}개")
    return found


# ─────────────────────────────────────────────
# 단계 2: 사실조회 (읽기, 헤프게)
# ─────────────────────────────────────────────
def stage2_fetch_facts(guard: QuotaGuard, found: dict) -> dict:
    """channels.list(배치50)로 사실 수집 + 최근 영상 제목.
    반환: {channel_id: {facts...}}"""
    yt = get_yt()
    ids = list(found.keys())
    facts = {}

    # channels.list 배치 50개씩 (1유닛/배치)
    for i in range(0, len(ids), 50):
        batch = ids[i:i+50]
        if not guard.can_spend('channels'):
            print(f"  단계2: 읽기 예산 소진 — channels 조회 중단")
            break
        try:
            guard.spend('channels')
            resp = yt.channels().list(
                id=','.join(batch), part='snippet,statistics,contentDetails'
            ).execute()
        except QuotaExceeded:
            print("  단계2: 예산 초과 — 중단")
            break
        except Exception as e:
            print(f"  단계2: channels.list 실패: {e}")
            continue

        for ch in resp.get('items', []):
            cid = ch['id']
            stats = ch.get('statistics', {})
            sn = ch.get('snippet', {})
            subs = int(stats.get('subscriberCount', 0)) if not stats.get('hiddenSubscriberCount') else 0
            facts[cid] = {
                'channel_title': sn.get('title', found.get(cid, {}).get('channel_title', '')),
                'description_short': (sn.get('description', '') or '')[:500],
                'subscriber_count': subs,
                'video_count': int(stats.get('videoCount', 0)),
                'channel_published_at': sn.get('publishedAt'),
                'uploads_playlist': ch.get('contentDetails', {}).get('relatedPlaylists', {}).get('uploads', ''),
                'recent_titles': [],
                'paths': found.get(cid, {}).get('paths', []),
            }

    # 구독자 미달 필터링 (최근 제목 수집 전에 제거해서 읽기 유닛 절약)
    qualified = {
        cid: f for cid, f in facts.items()
        if f['subscriber_count'] >= btc.DISCOVERY_MIN_SUBSCRIBERS
    }
    dropped = len(facts) - len(qualified)
    print(f"  단계2: 사실조회 {len(facts)}개 / 구독자 {btc.DISCOVERY_MIN_SUBSCRIBERS}+ 통과 {len(qualified)}개 (탈락 {dropped})")

    # 최근 영상 제목 수집 (playlistItems 1유닛/채널, 예산 내)
    for cid, f in qualified.items():
        if not f['uploads_playlist'] or not guard.can_spend('playlistItems'):
            continue
        try:
            guard.spend('playlistItems')
            resp = yt.playlistItems().list(
                playlistId=f['uploads_playlist'], part='snippet',
                maxResults=btc.DISCOVERY_RECENT_TITLES,
            ).execute()
            f['recent_titles'] = [
                it['snippet']['title'] for it in resp.get('items', [])
            ]
        except QuotaExceeded:
            print("  단계2: 제목수집 중 예산 초과 — 나머지 생략")
            break
        except Exception:
            continue

    print(f"  단계2 완료 ({guard.report()})")
    return qualified


# ─────────────────────────────────────────────
# 단계 3: Gemini 분류/점수화 (크레딧)
# ─────────────────────────────────────────────
CLASSIFY_PROMPT = """당신은 한국 패션/뷰티/라이프스타일 트렌드 채널 큐레이터입니다.
아래 유튜브 채널들이 'B-track 트렌드 레이더 시드'로 적합한지 판단하세요.

B-track 목표: 패션/뷰티/향수/러닝/액세서리/라이프스타일/브랜드/사회유행을
"지금 왜 뜨는지" 읽을 수 있는, 남녀 모두 볼 수 있는 트렌드 채널.
부적합: 특정 개인의 잡탕 일상 브이로그, 키즈, 게임, 먹방 전용, 정치/뉴스,
시사, 종교, 특정 연예인 팬채널 등 트렌드 신호가 약한 채널.

카테고리: fashion/beauty/fragrance/active/accessory/lifestyle/brand/social/other
gender_scope: unisex/men_skew/women_skew

채널 목록:
{channels_block}

각 채널에 대해 정확히 아래 JSON 배열로만 응답:
[
  {{
    "channel_id": "string",
    "primary_category": "fashion",
    "seed_fitness_score": 0.0,
    "gender_scope": "unisex",
    "is_vlog_jumble": false,
    "reason": "한 줄 근거"
  }}
]
- seed_fitness_score: 0.0~1.0. B-track 시드 적합도. 잡탕 브이로그/무관채널은 0.2 이하.
- is_vlog_jumble: 트렌드 신호 없는 개인 일상 브이로그면 true.
- JSON 배열만 반환."""


def _extract_json_list(text: str) -> list:
    text = text.strip()
    block = re.search(r'```(?:json)?\s*([\s\S]*?)```', text)
    if block:
        text = block.group(1).strip()
    arr = re.search(r'\[[\s\S]*\]', text)
    if arr:
        return json.loads(arr.group())
    return json.loads(text)


def stage3_classify(facts: dict) -> dict:
    """Gemini로 채널별 분류/점수. facts에 분류결과 병합."""
    _init_vertex()
    model = GenerativeModel(btc.BROAD_SCAN_MODEL)
    ids = list(facts.keys())
    batch_size = btc.DISCOVERY_GEMINI_BATCH

    for i in range(0, len(ids), batch_size):
        batch_ids = ids[i:i+batch_size]
        block_lines = []
        for cid in batch_ids:
            f = facts[cid]
            titles = ' / '.join(f['recent_titles'][:8])
            block_lines.append(
                f"[{cid}] 채널명: {f['channel_title']} | 구독자: {f['subscriber_count']:,} | "
                f"소개: {f['description_short'][:120]} | 최근영상: {titles}"
            )
        prompt = CLASSIFY_PROMPT.format(channels_block='\n'.join(block_lines))

        try:
            resp = model.generate_content(prompt)
            results = _extract_json_list(resp.text)
        except Exception as e:
            print(f"  단계3: Gemini 배치 {i//batch_size+1} 실패: {e}")
            continue

        for r in results:
            cid = r.get('channel_id')
            if cid in facts:
                facts[cid]['primary_category'] = r.get('primary_category', 'other')
                facts[cid]['seed_fitness_score'] = float(r.get('seed_fitness_score', 0))
                facts[cid]['gender_scope'] = r.get('gender_scope', 'unisex')
                facts[cid]['is_vlog_jumble'] = bool(r.get('is_vlog_jumble', False))
                facts[cid]['gemini_reason'] = r.get('reason', '')
        print(f"  단계3: Gemini 분류 배치 {i//batch_size+1} ({len(batch_ids)}개)")
        time.sleep(1)

    return facts


# ─────────────────────────────────────────────
# 단계 4: 저장 + 검증표 (자동 등록 없음)
# ─────────────────────────────────────────────
def stage4_save_and_report(db, facts: dict):
    """trend_source_candidates에 upsert (status='candidate'). 표 출력. 등록 안 함."""
    if not facts:
        print("  단계4: 후보 없음")
        return

    rows = []
    for cid, f in facts.items():
        # 발굴 경로 수 = 가산점 (여러 키워드/경로에서 중복 등장)
        hit_count = len(f.get('paths', []))
        rows.append({
            'channel_id': cid,
            'channel_title': f.get('channel_title', ''),
            'subscriber_count': f.get('subscriber_count', 0),
            'video_count': f.get('video_count', 0),
            'channel_published_at': f.get('channel_published_at'),
            'description_short': f.get('description_short', ''),
            'recent_titles': f.get('recent_titles', []),
            'primary_category': f.get('primary_category', 'other'),
            'seed_fitness_score': f.get('seed_fitness_score', 0),
            'gender_scope': f.get('gender_scope', 'unisex'),
            'is_vlog_jumble': f.get('is_vlog_jumble', False),
            'gemini_reason': f.get('gemini_reason', ''),
            'discovery_paths': ','.join(f.get('paths', [])),
            'discovery_hit_count': hit_count,
            'status': 'candidate',
            'last_scored_at': datetime.now(timezone.utc).isoformat(),
        })

    for i in range(0, len(rows), 100):
        db.table('trend_source_candidates').upsert(rows[i:i+100]).execute()

    # 최종 랭킹 = seed_fitness_score + 발굴경로 가산점(0.05/path, 최대 0.2)
    def rank(r):
        return r['seed_fitness_score'] + min(0.05 * (r['discovery_hit_count'] - 1), 0.2)

    ranked = sorted(rows, key=rank, reverse=True)
    print(f"\n  💾 후보 {len(rows)}개 저장 완료 (자동 등록 안 함)")
    print(f"\n{'='*100}")
    print("  📋 발굴 후보 TOP (사람 승인 대기 — trend_sources 자동등록 없음)")
    print(f"{'='*100}")
    print(f"  {'점수':>5} {'카테고리':<10} {'성별':<10} {'구독자':>10} {'채널명':<22} 근거")
    print(f"  {'-'*96}")
    for r in ranked[:40]:
        flag = '⚠잡탕' if r['is_vlog_jumble'] else ''
        print(
            f"  {rank(r):>5.2f} {r['primary_category']:<10} {r['gender_scope']:<10} "
            f"{r['subscriber_count']:>10,} {r['channel_title'][:20]:<22} "
            f"{flag}{r['gemini_reason'][:40]}"
        )
    print(f"{'='*100}")
    print("  ▶ 등록하려면: 채널을 골라 알려주세요. trend_sources 등록은 승인 후에만 진행합니다.")


# ─────────────────────────────────────────────
# 오케스트레이터
# ─────────────────────────────────────────────
def run_discovery():
    print(f"🔭 B-track 시드 발굴 시작 ({datetime.now()})")
    db = get_db()
    guard = QuotaGuard()
    print(f"  시작 {guard.report()}")

    exclude = _existing_exclusions(db)
    print(f"  제외 대상(기존 시드+후보): {len(exclude)}개")

    # 단계0
    found = stage0_db_internal(db, exclude)
    # 단계1
    try:
        found = stage1_search(guard, exclude, found)
    except QuotaExceeded as e:
        print(f"  단계1 예산 중단: {e}")

    if not found:
        print("  발굴 후보 없음 — 종료")
        return

    # 단계2
    facts = stage2_fetch_facts(guard, found)
    if not facts:
        print("  사실조회 결과 없음 — 종료")
        return

    # 단계3
    facts = stage3_classify(facts)
    # 단계4
    stage4_save_and_report(db, facts)
    print(f"\n✅ 발굴 완료 ({guard.report()})")
