"""
B-track Gemini 브로드 스캔 모듈
미분석 trend_videos에서 엔티티를 추출해 trend_entities + entity_mentions에 저장
"""
import json
import re
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import config
import b_track_config as btc
from b_track_config import _read_pages, _safe_error
import deepseek_processor as ds
import gemini_analyzer as ga
from supabase import create_client

def get_db():
    return create_client(config.SUPABASE_URL, config.SUPABASE_KEY)


BROAD_SCAN_PROMPT = """당신은 한국 패션/뷰티/라이프스타일 트렌드 분석 AI입니다.
아래 유튜브 영상 목록에서 각 영상의 주요 엔티티와 트렌드 신호를 추출하세요.

엔티티 타입: brand(브랜드), item(아이템/제품), person(인물/셀럽), meme(밈/유행어), phrase(유행문구), place(장소/팝업), aesthetic(스타일/감성)
카테고리: fashion(패션), beauty(뷰티), fragrance(향수/공간향), active(러닝/운동/액티브), accessory(액세서리), lifestyle(라이프스타일), social(사회/밈)
content_format: buying_guide(구매가이드), review(리뷰), trend_report(트렌드분석), brand_news(브랜드소식), ranking(랭킹), comparison(비교), social_meme(사회밈), vlog(일상)
trend_signal: early(초기신호), rising(상승중), mainstream(대중화), saturated(포화), fatigue(피로)
gender_scope: unisex(남녀모두), men_skew(남성중심), women_skew(여성중심)

영상 목록:
{videos_block}

각 video_id에 대해 정확히 아래 JSON 배열 형식으로 응답하세요:
[
  {{
    "video_id": "string",
    "primary_categories": ["fashion"],
    "content_format": "buying_guide",
    "gender_scope": "unisex",
    "trend_signal": "rising",
    "entities": [
      {{
        "name": "브랜드/아이템명",
        "entity_type": "brand",
        "category": "fashion",
        "confidence": 0.9
      }}
    ]
  }}
]

규칙:
- 엔티티는 영상 제목과 설명에서만 추출 (추측 금지)
- 엔티티가 없으면 "entities": [] 로 반환
- JSON 배열만 반환, 다른 텍스트 없이"""


def _extract_json_list(text: str) -> list:
    text = text.strip()
    block = re.search(r'```(?:json)?\s*([\s\S]*?)```', text)
    if block:
        text = block.group(1).strip()
    arr = re.search(r'\[[\s\S]*\]', text)
    items = json.loads(arr.group() if arr else text)
    if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
        raise ValueError("브로드 스캔 응답은 JSON 객체 배열이어야 합니다")
    return items


def _normalize(name: str) -> str:
    """엔티티 이름 정규화 (소문자, 공백 제거)"""
    return re.sub(r'\s+', '', name.lower().strip())


# 인메모리 엔티티 id 캐시 — (normalized_name, entity_type) → id
# 같은 run 안에서 반복 등장하는 엔티티의 DB 왕복(SELECT/UPDATE)을 제거.
_ENTITY_CACHE: dict = {}


def _upsert_entity(db, name: str, entity_type: str, category: str) -> int | None:
    """trend_entities에 upsert 후 id 반환. 캐시 우선."""
    norm = _normalize(name)
    if not norm:
        return None

    key = (norm, entity_type)
    cached = _ENTITY_CACHE.get(key)
    if cached is not None:
        return cached

    # DB 조회 (캐시 미스일 때만). last_seen_at은 이 경로에서만 갱신 → 쓰기 throttle.
    existing = db.table('trend_entities').select('id').eq(
        'normalized_name', norm
    ).eq('entity_type', entity_type).execute()

    if existing.data:
        eid = existing.data[0]['id']
        db.table('trend_entities').update({
            'last_seen_at': datetime.now(timezone.utc).isoformat()
        }).eq('id', eid).execute()
        _ENTITY_CACHE[key] = eid
        return eid

    # 신규 삽입 (동시/배치 내 중복으로 unique 충돌 시 재조회로 복구)
    eid = None
    try:
        resp = db.table('trend_entities').insert({
            'name': name,
            'normalized_name': norm,
            'entity_type': entity_type,
            'category': category,
        }).execute()
        eid = resp.data[0]['id'] if resp.data else None
    except Exception:
        again = db.table('trend_entities').select('id').eq(
            'normalized_name', norm
        ).eq('entity_type', entity_type).execute()
        eid = again.data[0]['id'] if again.data else None

    if eid is not None:
        _ENTITY_CACHE[key] = eid
    return eid


def run_broad_scan(max_videos: int = None):
    """미분석 trend_videos에 브로드 스캔 실행 (DeepSeek 우선, 실패시 무료 Gemini 키 폴백)"""
    db = get_db()
    max_videos = max_videos or btc.MAX_VIDEOS_PER_RUN
    batch_size = btc.BROAD_SCAN_BATCH_SIZE

    # 미분석 영상 로드
    videos = _read_pages(
        lambda: db.table('trend_videos').select(
            'video_id,channel_id,channel_title,title,description_short,view_count,published_at'
        ).eq('broad_scan_done', False).order('view_over_baseline', desc=True).order('video_id'),
        max_rows=max_videos,
    )
    if not videos:
        print("  ℹ️ 브로드 스캔할 영상 없음")
        return 0

    print(f"🔍 브로드 스캔 시작: {len(videos)}개 영상")

    total_entities = 0
    processed = 0
    failed_batches = 0

    for batch_start in range(0, len(videos), batch_size):
        batch = videos[batch_start:batch_start + batch_size]

        # 프롬프트용 텍스트 블록 생성
        videos_block = '\n'.join([
            f"[{v['video_id']}] {v['channel_title']} | {v['title']} | {(v['description_short'] or '')[:200]}"
            for v in batch
        ])

        prompt = BROAD_SCAN_PROMPT.format(videos_block=videos_block)

        # Gemini job 시작 로그
        job_resp = db.table('gemini_jobs').insert({
            'job_type': 'entity_extraction',
            'model': btc.BROAD_SCAN_MODEL,
            'input_count': len(batch),
            'status': 'running',
        }).execute()
        job_id = job_resp.data[0]['id'] if job_resp.data else None

        try:
            try:
                text = ds.call_deepseek_raw(prompt)
                results = _extract_json_list(text) if text else []
            except Exception as e:
                print(f"  ⚠️ DeepSeek 브로드 스캔 실패: {_safe_error(e)}")
                results = []
            if results:
                get_model = getattr(config, 'get_deepseek_model', lambda **_: 'deepseek')
                used_model = get_model(complex_task=False)
            else:
                used_model = btc.BROAD_SCAN_MODEL
                generate_with_meta = getattr(ga, 'genai_generate_text_with_meta', None)
                if callable(generate_with_meta):
                    text, actual_model = generate_with_meta(prompt)
                    used_model = actual_model or used_model
                else:
                    text = ga.genai_generate_text(prompt, model_name=used_model)
                results = _extract_json_list(text)
        except Exception as e:
            failed_batches += 1
            error = _safe_error(e)
            print(f"  ❌ 배치 {batch_start//batch_size + 1} Gemini 오류: {error}")
            if job_id:
                db.table('gemini_jobs').update({
                    'status': 'failed',
                    'error_summary': error,
                    'finished_at': datetime.now(timezone.utc).isoformat(),
                }).eq('id', job_id).execute()
            time.sleep(2)
            continue

        # 결과 파싱 및 저장
        batch_entities = 0
        succeeded_video_ids = []
        video_meta = {v['video_id']: v for v in batch}

        for item in results:
            vid = item.get('video_id')
            vmeta = video_meta.pop(vid, None)
            if vmeta is None:
                continue

            # 영상 1개의 멘션을 모아 한 번에 upsert (on_conflict=DO NOTHING). 왕복 대폭 감소.
            mention_rows = []
            seen_eids = set()
            raw_entities = item.get('entities')
            entities = raw_entities if isinstance(raw_entities, list) else []
            for ent in entities:
                if not isinstance(ent, dict):
                    continue
                name = str(ent.get('name') or '').strip()
                etype = str(ent.get('entity_type') or 'item').strip() or 'item'
                category = str(ent.get('category') or 'lifestyle').strip() or 'lifestyle'
                raw_confidence = ent.get('confidence')
                try:
                    confidence = float(raw_confidence if raw_confidence is not None else 0.8)
                except (TypeError, ValueError):
                    confidence = 0.8

                if not name or confidence < 0.5:
                    continue

                eid = _upsert_entity(db, name, etype, category)
                if not eid or eid in seen_eids:   # 같은 영상 내 중복 엔티티 제거
                    continue
                seen_eids.add(eid)

                mention_rows.append({
                    'entity_id': eid,
                    'video_id': vid,
                    'channel_id': vmeta['channel_id'],
                    'mentioned_in': 'title',
                    'confidence': confidence,
                    'view_count_at_scan': int(vmeta.get('view_count') or 0),
                    'published_at': vmeta['published_at'],
                })

            # 영상 단위 격리: 한 영상 실패해도 나머지 배치는 계속
            if mention_rows:
                try:
                    db.table('entity_mentions').upsert(
                        mention_rows,
                        on_conflict='entity_id,video_id',
                        ignore_duplicates=True,
                    ).execute()
                except Exception as me:
                    print(f"    ⚠️ mention skip (vid={vid}): {_safe_error(me)[:80]}")
                    continue
                batch_entities += len(mention_rows)
            succeeded_video_ids.append(vid)

        completed = len(succeeded_video_ids)
        if completed:
            db.table('trend_videos').update({
                'broad_scan_done': True,
                'broad_scan_at': datetime.now(timezone.utc).isoformat(),
            }).in_('video_id', succeeded_video_ids).execute()
        else:
            failed_batches += 1

        # job 완료 기록
        if job_id:
            job_result = {
                'status': 'done' if completed else 'failed',
                'model': used_model,
                'finished_at': datetime.now(timezone.utc).isoformat(),
                'estimated_cost_usd': round(len(batch) * 0.00005, 6),
            }
            if not completed:
                job_result['error_summary'] = '완료된 영상 없음'
            db.table('gemini_jobs').update(job_result).eq('id', job_id).execute()

        total_entities += batch_entities
        processed += completed
        print(f"  배치 {batch_start//batch_size + 1}: {completed}/{len(batch)}영상 완료 → 엔티티 {batch_entities}개")

        time.sleep(1)  # rate limit 방지

    if failed_batches > 0 and processed == 0:
        raise RuntimeError(f"B-track 브로드 스캔 실패: {failed_batches}배치에서 완료된 영상 없음")

    print(f"\n✅ 브로드 스캔 완료: {processed}영상 처리, 엔티티 {total_entities}개 추출")
    return processed


def run_cluster():
    """entity_mentions 집계 → trend_clusters 갱신 (Gemini 불필요, 순수 집계)"""
    db = get_db()
    print("📊 트렌드 클러스터 집계 중...")

    cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    mentions = _read_pages(
        lambda: db.table('entity_mentions').select('entity_id,channel_id,view_count_at_scan,published_at')
        .gte('created_at', cutoff).order('entity_id').order('video_id')
    )

    if not mentions:
        print("  ℹ️ 최근 7일 언급 없음")
        return

    # entity별 집계
    agg = defaultdict(lambda: {
        'channels': set(), 'views': 0, 'count': 0
    })
    for m in mentions:
        eid = m['entity_id']
        agg[eid]['channels'].add(m['channel_id'])
        agg[eid]['views'] += int(m.get('view_count_at_scan') or 0)
        agg[eid]['count'] += 1

    # 상위 엔티티 → 클러스터 갱신
    top_eids = sorted(agg, key=lambda eid: -agg[eid]['count'])[:100]
    entities = _read_pages(
        lambda: db.table('trend_entities').select('id,name,category').in_('id', top_eids).order('id')
    )
    entity_map = {e['id']: e for e in entities}

    updated = 0
    for eid in top_eids:
        if eid not in entity_map:
            continue
        entity = entity_map[eid]
        stats = agg[eid]
        n_channels = len(stats['channels'])
        n_mentions = stats['count']
        total_views = stats['views']

        # 트렌드 스테이지 판단
        if n_channels >= btc.MAINSTREAM_MIN_CHANNELS:
            stage = 'mainstream'
        elif n_channels >= btc.RISING_MIN_CHANNELS:
            stage = 'rising'
        else:
            stage = 'early'

        duplication_score = min(n_channels / 20.0, 1.0)
        velocity_score = min(n_mentions / 50.0, 1.0)

        # 기존 클러스터 확인
        existing = db.table('trend_clusters').select('id').eq(
            'cluster_name', entity['name']
        ).execute()

        cluster_data = {
            'cluster_name': entity['name'],
            'category': entity['category'],
            'trend_stage': stage,
            'mention_count_7d': n_mentions,
            'distinct_channels_7d': n_channels,
            'total_views_7d': total_views,
            'velocity_score': round(velocity_score, 3),
            'duplication_score': round(duplication_score, 3),
            'computed_at': datetime.now(timezone.utc).isoformat(),
        }

        if existing.data:
            db.table('trend_clusters').update(cluster_data).eq(
                'id', existing.data[0]['id']
            ).execute()
        else:
            db.table('trend_clusters').insert(cluster_data).execute()

        updated += 1

    print(f"✅ 클러스터 {updated}개 갱신 완료")
