# 한고은 채널 분석 시스템

> **현재 버전**: v1.4 (2026-05-11)  
> **목적**: 경쟁 채널 10개 + 자체 채널 자동 분석 → 콘텐츠 전략 인사이트 도출

---

## 목차
1. [시스템 개요](#시스템-개요)
2. [아키텍처](#아키텍처)
3. [파일 구성](#파일-구성)
4. [실행 모드 & 자동화 스케줄](#실행-모드--자동화-스케줄)
5. [분석 항목](#분석-항목)
6. [데이터 저장 구조](#데이터-저장-구조)
7. [환경변수](#환경변수)
8. [운영 가이드](#운영-가이드)
9. [이슈 & 해결 기록](#이슈--해결-기록)
10. [버전 히스토리](#버전-히스토리)

---

## 시스템 개요

### 분석 대상 채널 (경쟁사 10개)
| 채널명 | 채널 ID |
|---|---|
| 안녕하세요 최화정이에요 | UCIQQC0T_yj-pXoXRk5vaOAw |
| 이민정 MJ | UCFNUGAiaGRgzVCXrjVoe1Hw |
| 자유부인 한가인 | UCvnVUhn95YfQazC_bc2lU6w |
| 깡주은 | UCcjmCG5tTegWwqcSDSkDevQ |
| 고소영 | UCag6Qu6uHjQKPvBI5MW12AQ |
| 한지혜 Han Ji Hye | UC1vQa5Pbtt-uewHSxUPc8rA |
| 밉지않은 관종언니 | UCtkRVaUSpkuhqdKv39oBXKA |
| A급 장영란 | UCfVWxOKzPwSZkEcxXmrH6yw |
| 공부왕찐천재 홍진경 | UCkxbPwdaV74Erdxt97Nt23w |
| 개과천선 서인영 | UCAMGWbOnDd9dpAK7BO0Wftw |

### 자체 채널
| 채널명 | 채널 ID |
|---|---|
| 고은언니 한고은 | UCPFW9tE-hAuyR4037csZAqg |

### 자체 채널 업로드 패턴
- 롱폼: 매주 목요일
- 숏츠: 매주 금·토요일

---

## 아키텍처

```
YouTube API
    │
    ▼
youtube_collector.py  ←── 영상 수집 (search.list 금지, playlistItems 사용)
    │
    ├──▶ gemini_analyzer.py    ←── Vertex AI (gemini-2.5-flash) 구조 분석
    │
    └──▶ deepseek_processor.py ←── DeepSeek API 댓글/성공공식/리포트
              │
              ▼
    ┌─────────────────────┐
    │   sheets_writer.py  │ ←── Google Apps Script 웹훅
    │   supabase_writer.py│ ←── Supabase (PostgreSQL)
    │   telegram_notifier │ ←── 텔레그램 봇 알림
    └─────────────────────┘
              │
              ▼
         main.py (진입점, 모드별 파이프라인 조율)
              │
              ▼
    GitHub Actions (자동 스케줄 실행)
```

---

## 파일 구성

| 파일 | 역할 |
|---|---|
| `config.py` | API 키, 채널 ID, 모델명, 시트 탭명 등 전체 설정 |
| `main.py` | 진입점. 8가지 모드 분기 및 파이프라인 조율 |
| `youtube_collector.py` | YouTube Data API v3 수집 |
| `gemini_analyzer.py` | Vertex AI Gemini 영상/자체채널 분석 |
| `deepseek_processor.py` | DeepSeek 댓글 분석, 대박 영상 심층 분석, 성공공식, 리포트 |
| `sheets_writer.py` | Google Apps Script 웹훅을 통한 구글 시트 저장 |
| `supabase_writer.py` | Supabase(PostgreSQL) 이중 저장 |
| `telegram_notifier.py` | 텔레그램 봇 알림 |
| `trend_classifier.py` | 키워드 룰 기반 트렌드 분류 (AI 없음, 100% 재현 가능) |
| `strategy_scorer.py` | 룰 기반 전략 스코어 산출 (API 호출 없음, 결정론적) |

---

## 실행 모드 & 자동화 스케줄

### 실행 모드

| 명령어 | 설명 |
|---|---|
| `python main.py initial-batch [1~5]` | 초기 6개월 데이터 배치 수집 |
| `python main.py daily` | 평일 경쟁채널 새 영상 수집·분석 |
| `python main.py trend-collect` | 트렌드 수집 + 이레귤러 즉시 알림 |
| `python main.py trend-weekly` | 월요일 주간 트렌드 종합 알림 |
| `python main.py weekly` | 주간 채널 리포트 |
| `python main.py own-channel` | 자체 채널 새 영상 감지·즉시 분석 |
| `python main.py own-track` | 자체 채널 주간 추적 스냅샷 |
| `python main.py own-backfill` | Supabase → 구글 시트 재동기화 |
| `python main.py score-backfill` | 기존 분석 전체에 전략 스코어 소급 산출 |
| `python main.py score-sheets-sync` | Supabase 스코어 → 구글 시트 동기화 |
| `python main.py stats-refresh` | 경쟁채널 전체 영상 조회수·좋아요·댓글수 갱신 |

### GitHub Actions 자동화 스케줄

| 워크플로우 파일 | 실행 시간 (KST) | 명령 |
|---|---|---|
| `daily-collection.yml` | 평일 00:00 | `daily` |
| `daily-trend.yml` | 매일 10:00 | `trend-collect` |
| `weekly-report.yml` | 월요일 00:00 | `weekly` |
| `weekly-trend.yml` | 월요일 11:00 | `trend-weekly` |
| `own-channel-collect.yml` | 매일 01:00 | `own-channel` |
| `own-channel-track.yml` | 월요일 12:00 | `own-track` |
| `score-backfill.yml` | 수동 실행 | `score-backfill` |
| `score-sheets-sync.yml` | 수동 실행 | `score-sheets-sync` |
| `stats-refresh.yml` | 매일 09:00 | `stats-refresh` |

---

## 분석 항목

### Gemini 분석 (경쟁채널 & 자체채널 공통)

| 항목 | 설명 |
|---|---|
| `topic` | 핵심 주제 1줄 |
| `category` | 라이프스타일/뷰티/요리/육아/패션/여행/일상/기타 |
| `title_pattern` | 질문형/단정형/숫자형/감정형/정보형 |
| `title_keywords` | 핵심 키워드 3개 |
| `title_emotion_tone` | 따뜻함/놀람/친근함/권위감/유머/공감/기대감 |
| `hook_strategy` | 시청자 후킹 전략 |
| `content_structure` | 브이로그형/정보전달형/스토리텔링형/리뷰형/토크형 |
| `ppl_likely` | PPL 여부 (true/false) |
| `ppl_signals` | PPL 근거 |
| `target_audience` | 주 타겟 시청자층 |
| `performance_level` | 대박/평작/저조 |
| `success_factors` | 성공/실패 추정 요인 |
| `unique_differentiator` | 채널 내 이 영상만의 차별화 |
| `applicability` | 한고은 채널 적용 가능성 (상/중/하) |
| `applicability_reason` | 적용 가능성 이유 |
| `hangoeun_scenario` | 한고은 채널이라면 어떻게 만들지 (시나리오) |

### Gemini 분석 (자체채널 전용 추가 항목)

| 항목 | 설명 |
|---|---|
| `predicted_performance` | 성과 예측 (높음/보통/낮음) |
| `predicted_performance_reason` | 예측 이유 |
| `strengths` | 콘텐츠 강점 |
| `improvement_points` | 개선 포인트 |
| `thumbnail_suggestion` | 썸네일 개선 제안 |
| `competitor_angle` | 경쟁 채널 대비 차별점/개선 방향 |

### DeepSeek 분석 - 댓글 여론 (경쟁채널)

| 항목 | 설명 |
|---|---|
| `sentiment` | 긍정/부정/중립 비율 (0~100) |
| `main_keywords` | 자주 등장 키워드 5개 |
| `viewer_persona` | 추정 시청자층 |
| `praise_points` | 시청자가 좋아하는 점 3개 |
| `complaints` | 불만/요청사항 |
| `suggestions` | 콘텐츠 개선 인사이트 |
| `this_video_special` | 이 영상만의 특별한 이유 (댓글 기반) |
| `revisit_intent` | 재방문/재구독 의사 비율 (0~100) |
| `viral_signals` | 공유·추천·감동 댓글 패턴 |
| `summary` | 여론 한 줄 요약 |

### DeepSeek 분석 - 댓글 여론 (자체채널 전용 추가 항목)

| 항목 | 설명 |
|---|---|
| `next_video_requests` | 시청자 다음 영상 요청 3가지 |
| `new_viewer_signals` | 신규 시청자 댓글 패턴 |
| `fan_engagement` | 고정 팬 반응 특징 |
| `ppl_reaction` | PPL/협찬 반응 |
| `creator_feedback` | 크리에이터에게 전달할 핵심 피드백 |

### 전략 스코어 (strategy_scorer.py — API 없음, 룰 기반)

> `config.SCORE_VERSION = "v1.2"` 기준. 분석 결과를 입력으로 받아 결정론적으로 산출.

| 항목 | 설명 |
|---|---|
| `hangoeun_fit_score` | 한고은 채널 적합도 (0~100) |
| `execution_score` | 제작 실행 난이도 역산 점수 (높을수록 쉽게 만들 수 있음) |
| `repeatability_score` | 시리즈·반복 기획 가능성 (0~100) |
| `novelty_score` | 차별화·신선도 (0~100) |
| `risk_score` | 브랜드 리스크 (낮을수록 안전) |
| `ppl_potential_score` | PPL/협찬 유치 가능성 (0~100) |
| `trend_lifespan_score` | 트렌드 지속성 (EVERGREEN=90/MID_TERM=60/FAST_TREND=30) |
| `upload_delay_risk` | 업로드 지연 리스크 (FAST_TREND=80 → 빠른 실행 필요) |
| `evergreen_score` | 에버그린 콘텐츠 점수 (0~100) |
| `content_lifespan_type` | FAST_TREND / MID_TERM / EVERGREEN |
| `priority_score` | 종합 우선순위 점수 (가중합, 0~100) |
| `recommended_action` | 바로 기획화 / 각색 후 기획 / 아이디어 보관 / 우선순위 낮음 |
| `strategy_reason` | 판정 근거 한 문장 |
| `rule_trace` | 각 지표별 점수 계산 상세 내역 (JSONB) |

### DeepSeek 분석 - 대박 영상 심층 분석 (채널 평균 × 2.0배 이상)

| 항목 | 설명 |
|---|---|
| `success_drivers` | title_hook/topic_choice/timing/channel_fandom 기여도 (0~10) |
| `primary_driver` | 가장 핵심 성공 드라이버 |
| `seasonality` | 시즌성/이슈 연결 여부 |
| `what_clicked` | 터진 핵심 이유 |
| `hangoeun_version` | 한고은 채널 적용 시 제목 예시 + 구성 방향 |

---

## 데이터 저장 구조

### 구글 시트 탭

| 탭 이름 | 내용 |
|---|---|
| `영상_마스터데이터` | 수집된 모든 경쟁채널 영상 원본 |
| `초기_분석결과` | 초기 배치(1~5) 분석 결과 |
| `일일_분석결과` | 평일 일일 수집·분석 결과 |
| `채널별_성공공식` | 채널별 성공 공식 인사이트 |
| `일일_트렌드` | 트렌드 분류 결과 (트랙A/B/이레귤러) |
| `일일_사건키워드` | 날짜별 사건 키워드 |
| `주간_리포트` | 주간 채널 리포트 |
| `자체채널_분석` | 자체 채널 새 영상 즉시 분석 |
| `자체채널_추적` | 자체 채널 업로드 후 5주 추적 스냅샷 |
| `전략_스코어` | 영상별 전략 스코어 (우선순위·적합도 등 12개 지표) |

### Supabase 테이블

| 테이블 | 내용 |
|---|---|
| `videos` | 경쟁채널 영상 마스터 |
| `initial_analysis` | 초기 배치 분석 결과 |
| `daily_analysis` | 일일 분석 결과 |
| `channel_insights` | 채널 성공 공식 |
| `trend_classified` | 트렌드 분류 결과 |
| `event_keywords` | 사건 키워드 |
| `own_channel_videos` | 자체 채널 영상 목록 |
| `own_channel_analysis` | 자체 채널 즉시 분석 |
| `own_channel_snapshots` | 자체 채널 주간 추적 스냅샷 |
| `video_scores` | 영상별 전략 스코어 (video_id + score_version upsert) |

---

## 환경변수

| 변수명 | 필수 | 설명 |
|---|---|---|
| `YOUTUBE_API_KEY` | ✅ | YouTube Data API v3 |
| `DEEPSEEK_API_KEY` | ✅ | DeepSeek API |
| `TELEGRAM_BOT_TOKEN` | ✅ | 텔레그램 봇 |
| `TELEGRAM_CHAT_ID` | ✅ | 텔레그램 채팅 ID |
| `GOOGLE_WEBHOOK_URL` | ✅ | Apps Script 웹훅 URL |
| `SUPABASE_URL` | ✅ | Supabase 프로젝트 URL |
| `SUPABASE_KEY` | ✅ | Supabase anon 키 |
| `GOOGLE_SHEET_ID` | ✅ | 구글 스프레드시트 ID |
| `GOOGLE_APPLICATION_CREDENTIALS_JSON` | ✅ | GCP 서비스 계정 JSON (Vertex AI) |
| `GOOGLE_CLOUD_PROJECT` | - | GCP 프로젝트 ID (기본값 내장) |

---

## 운영 가이드

### 핵심 설계 원칙

- **YouTube API 할당량 보호**: `search.list` 사용 금지. 채널 ID는 `config.py`에 하드코딩. `playlistItems.list → videos.list` 경로만 사용
- **숏폼 필터**: 경쟁채널은 5분(300초) 미만 자동 제외. 자체채널은 숏츠 포함 전체 수집
- **대박 영상 기준**: 채널 평균 조회수 × 2.0배 이상 (`HIT_VIDEO_MULTIPLIER = 2.0`)
- **트렌드 분류**: AI 점수 없음. 100% 키워드 룰 기반 (재현 가능)
- **이중 저장**: 구글 시트(열람용) + Supabase(프로그래밍 조회용)
- **중간 저장**: 10개 영상마다 저장 → 장시간 실행 중 장애 시 손실 최소화
- **Gemini 모델**: `gemini-2.5-flash` (Vertex AI, us-central1, GCP 서비스 계정 인증)
- **DeepSeek 모델**: 단순 작업 → `deepseek-v4-flash` / 복잡 작업 → `deepseek-v4-pro`
- **분석 버전 추적**: `config.ANALYSIS_VERSION`을 `gemini_raw._version` / `deepseek_raw._version` JSONB 필드에 자동 삽입 → 프롬프트/모델 변경 시 데이터 계보 추적 가능
- **전략 스코어링**: 분석 완료 직후 `_score_and_save()` 자동 호출. 실패해도 분석 저장에 영향 없음. 기존 데이터 소급 적용 시 `score-backfill` 사용

### 자체 채널 추적 규칙
- 업로드 감지: 매일 KST 01:00 자동 실행, 최근 7일 신규 영상 확인
- 추적: `published_at` 기준 주차 계산 (`days_elapsed // 7 + 1`), 매주 월요일 스냅샷, 5주차 완료 후 자동 종료
- 중복 방지: `get_snapshot_by_week(video_id, week_number)` — 이미 저장된 주차면 skip
- 댓글 분석: 1주·2주·5주차만 실행 (API 절약)
- 숏츠 판별: `#shorts` 태그 또는 60초 이하
- 신규 영상 판별: `own_channel_analysis` 테이블 기준 (Gemini+DeepSeek 분석 완료 여부)

### 배치 재실행 시
```
GitHub Actions → initial-batch.yml → Run workflow → 배치 번호 선택
```
기존 데이터는 `delete_initial_batch()` 자동 삭제 후 재삽입 (중복 없음)

### Apps Script 재배포 필수 케이스
- 새 액션 추가 시 반드시 새 버전으로 재배포
- 배포 → 배포 관리 → 편집 → 새 버전 만들기 → 배포

---

## 이슈 & 해결 기록

| 이슈 | 원인 | 해결 |
|---|---|---|
| Gemini API 429 (크레딧 소진) | Gemini API 선불 충전 소진 | Vertex AI SDK로 전환, GCP 서비스 계정 인증 |
| Vertex AI 모델 404 | gemini-3.1-flash-lite-preview 미지원 | `gemini-2.5-flash`로 변경 |
| Supabase PGRST204 | `is_hit`, `channel_avg_views` 컬럼 없음 | ALTER TABLE로 컬럼 추가 |
| `timedelta` import 오류 | supabase_writer.py 누락 | `from datetime import datetime, timedelta` |
| Gemini JSON 파싱 실패 | 응답이 마크다운 코드블록으로 감싸짐 | `_extract_json()` regex 파싱 함수 |
| 구글 시트 탭 미생성 | Apps Script 재배포 누락 | 코드 수정 후 반드시 새 버전 재배포 |
| 배치 재실행 중복 | 기존 데이터 남아있음 | `delete_initial_batch()` 자동 삭제 |
| 자체채널 재실행 시 "새 영상 없음" | `is_own_video_analyzed()`가 `own_channel_videos` 존재만 확인 → 분석 실패 시 스킵 | `own_channel_analysis` 테이블 기준으로 변경 |
| PPL 항상 true 출력 | Gemini 프롬프트에 `"ppl_likely": true` 하드코딩 | 설명 문자열 `"PPL 가능성 있으면 true, 없으면 false"`로 교체 |
| 자체채널 추적 주차 오류 | snapshot count 기반 → 재실행 시 이미 완료한 주차 덮어쓸 위험 | `published_at` 기준 주차 계산 + `get_snapshot_by_week()` 중복 방지 |

---

## 버전 히스토리

### v1.2 (2026-05-10) — 전략 스코어링 시스템 추가
**Major**
- `strategy_scorer.py` 신규: 룰 기반 전략 점수 산출 (API 호출 없음, 결정론적)
- 13개 산출 항목: hangoeun_fit / execution / repeatability / novelty / risk / ppl_potential / trend_lifespan / upload_delay_risk / evergreen / content_lifespan_type / priority_score / recommended_action / strategy_reason
- `rule_trace` JSONB로 각 점수 산출 근거 저장
- `score_version` 관리로 룰 개정 시 버전별 재스코어링 지원
- Supabase `video_scores` 테이블 신규 (`UNIQUE(video_id, score_version)`)
- 구글 시트 `전략_스코어` 탭 신규
- `python main.py score-backfill`: 기존 6개월 분석 데이터 일괄 후처리
- `score-backfill.yml` GitHub Actions workflow_dispatch 추가
- daily/initial 분석 완료 후 자동 스코어링 연결 (분석 실패와 독립)
- 주간 리포트 텔레그램 알림 하단에 기획 우선순위 TOP5 섹션 추가

**priority_score 공식**
```
hangoeun_fit×0.30 + repeatability×0.18 + evergreen×0.15
+ novelty×0.12 + ppl_potential×0.10 + trend_lifespan×0.08
+ execution×0.07 − risk×0.15 − upload_delay_risk×0.10
```

---

### v1.4 (2026-05-11) — 시계열 스냅샷 + 썸네일 Vision 분석
**Added**
- `video_snapshots` Supabase 테이블: 경쟁채널 + 자체채널 전 영상 일별 시계열 스냅샷 (view/like/comment growth 포함)
- `thumbnail_analysis` Supabase 테이블: 썸네일 Vision 분석 결과 (face_count, main_emotion, CTR 예측 등 14개 필드)
- `snapshot-collect` 모드: stats-refresh 대체. YouTube API 수집 + `video_snapshots` 저장 통합 (매일 KST 09:00)
- `thumbnail-backfill` 모드: HIT + IRREGULAR + priority_score≥70 영상 썸네일을 Gemini 2.5 Pro로 분석
- `thumbnail-backfill.yml` 워크플로우 (수동 실행)
- `stats-refresh.yml` → `snapshot-collect` 실행으로 업그레이드

**Changed**
- `get_gemini_model('vision')` → `gemini-2.5-pro` 추가

---

### v1.3 (2026-05-11) — 통계 갱신 파이프라인 추가
**Added**
- `stats-refresh` 실행 모드: 경쟁채널 전체 영상 조회수·좋아요·댓글수 일괄 갱신 (AI 없음, YouTube API만)
- `stats-refresh.yml`: 매일 KST 09:00 자동 실행 (주간보고 전 stats 선행 갱신)
- `get_all_tracked_video_ids()` / `get_recent_videos_from_db()` in supabase_writer.py

**Changed**
- `run_weekly_report()`: 구글 시트(stale) → Supabase(최신 stats) 기준으로 변경. Supabase 없을 시 시트 fallback

---

### v1.2 (2026-05-11) — 전략 스코어링 추가
**Added**
- `strategy_scorer.py`: 룰 기반 전략 스코어 산출 (API 없음, 결정론적) — 12개 지표 + `priority_score` + `recommended_action`
- `video_scores` Supabase 테이블: `video_id + score_version` upsert
- `전략_스코어` 구글 시트 탭
- `score-backfill` / `score-sheets-sync` 실행 모드 및 GitHub Actions 워크플로우 추가
- 분석 완료 후 `_score_and_save()` 자동 호출 (실패 격리)

**Changed**
- `weekly-report.yml` 스케줄: 월요일 10:00 → 00:00 KST

---

### v1.1 (2026-05-10) — 버그 수정 패치
**Fixed**
- `is_own_video_analyzed()`: `own_channel_videos` → `own_channel_analysis` 기준으로 변경 (분석 실패 시 재시도 가능)
- Gemini 프롬프트 PPL 하드코딩 제거: 경쟁채널/자체채널 양쪽 `"ppl_likely": true` → 설명 문자열로 교체
- 분석 버전 태깅: `gemini_raw._version` / `deepseek_raw._version` 필드에 `ANALYSIS_VERSION` 자동 삽입
- 자체채널 추적 주차 계산: snapshot count 기반 → `published_at` 기준 실제 주차 계산 + `get_snapshot_by_week()` 중복 방지

---

### v1.0 (2026-05-10) — 초기 릴리스
**Major**
- 경쟁채널 10개 초기 6개월 데이터 배치 수집 완료 (배치 1~5)
- Gemini(Vertex AI) + DeepSeek 투트랙 분석 파이프라인 구축
- 자체 채널(고은언니 한고은) 즉시 분석 + 5주 추적 시스템 추가
- 트렌드 수집·분류·이레귤러 감지 시스템 구축
- GitHub Actions 자동화 스케줄 전체 활성화
- 구글 시트 9개 탭 + Supabase 9개 테이블 이중 저장 체계 완성

---

*이 문서는 시스템 변경 시 버전과 함께 업데이트합니다.*  
*관리: GitHub `my_youtube-report` 레포지토리 `SYSTEM.md`*
