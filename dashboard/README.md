# 유튜브 전략 대시보드 (리뉴얼)

고은언니 한고은 채널 전략판. **Track A · 셀럽 유튜브**와 **Track B · 패션+유행**을 분리해 보는 읽기 전용 Streamlit 앱.

## 실행

```bash
pip install -r dashboard/requirements.txt
cp dashboard/.env.example dashboard/.env   # SUPABASE_URL / SUPABASE_KEY 입력 (커밋 금지)
streamlit run dashboard/app.py
```

키는 환경변수 → `dashboard/.env` → 저장소 루트 `.env` 순서로 읽습니다. 코드에 하드코딩하지 않습니다.

## 구조

```
dashboard/
  app.py                 # 단일 라우터 · st.navigation 그룹 (홈 / Track A / Track B)
  views/                 # 페이지 (구식 pages/ 자동 등록을 쓰지 않음)
    home.py              # 전략 브리핑: A 바로 기획화 TOP · 자체 채널 · B 시그널 · 수집 헬스
    track_a_scores.py    # A1 기획 후보 큐 (v2.0 기본, v1.2 비교)
    track_a_celeb.py     # A2 경쟁 16채널 리더보드 · 히트 패턴 (카테고리/구조/제목/썸네일/감정온도/광고·PPL)
    track_a_own.py       # A3 고은언니 한고은 (롱폼/쇼츠 · 3/6/12/24h 궤적 · 주차 성장 · AI 분석)
    track_a_weekly.py    # A4 주간 시장 · 리포트 · 성장 패턴 · 트랙별 급상승
    track_b_radar.py     # B1 트렌드 레이더 (trend_entity_scores 최신 스냅샷 + answer cards)
    track_b_clusters.py  # B2 클러스터 · 셀럽 미채택 레이더 · 소스 · 고효율 피드
  lib/
    queries.py           # read-only 조회 · _read_pages 페이지네이션 · 5분 캐시
    transforms.py        # 순수 변환 (null/빈 데이터 방어, 단위 테스트 대상)
    constants.py         # 자체 채널명 · 점수 버전 · 라벨 (config.py 와 정합성 테스트)
    supabase_client.py
  components/            # ui.py(디자인 시스템) · cards.py · charts.py(Altair 테마)
```

## 원칙

- DB 쓰기 없음 (테스트가 insert/update/upsert/delete/rpc 호출을 금지 검사).
- PostgREST 1,000행 절단 방지: `_read_pages(lambda: query, page_size=500)`.
- 대용량 B-track 테이블은 최신 `scored_at` / `card_date` / `computed_at` 스냅샷만, 명시 컬럼으로 조회.
- `video_scores` 는 항상 `score_version` 단일 필터 → v1.2/v2.0 미혼합.
- 경쟁 채널 목록은 실제 `videos.channel_title` 에서 만들고, 자체 채널(`고은언니 한고은`)은 서버·클라이언트 양쪽에서 제외.

## 테스트

```bash
python3 -m unittest discover -s tests -p "test_dashboard.py" -v
```

인메모리 Fake Supabase 로 쿼리/변환을 검증하고, `AppTest` 로 7개 페이지를 샘플·빈 DB·테이블 장애 3가지 상황에서 렌더합니다.
