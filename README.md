# 한고은 채널 분석 시스템

## 분석 엔진 개선 브랜치

- `pip install -r requirements.txt`로 의존성을 설치합니다. 검증 DB에 `DATABASE_URL`을 설정하고 `python db_migrate.py`를 실행합니다. 기존 행을 보존하면서 네 테이블에 `processing_key`를 추가하고 B-track 점수 및 답변 카드 테이블을 준비합니다.
- Gemini 기본 제공자는 Google AI Studio(`GEMINI_PROVIDER=ai_studio`)입니다. `GEMINI_API_KEY`를 환경변수에 설정하면 요청 헤더로 인증합니다. `GEMINI_PROVIDER=vertexai`를 선택하면 Google Cloud ADC와 `GOOGLE_CLOUD_PROJECT`, `GOOGLE_CLOUD_LOCATION`을 사용합니다.
- A-track Gemini 기본 모델은 `gemini-4-hydrogen`이며, 기본 폴백 순서는 `gemini-3.8-flash` → `gemini-3.5-flash` → `gemini-2.5-flash-lite`입니다. `GEMINI_MODEL`과 용도별 `GEMINI_DAILY_MODEL`, `GEMINI_INITIAL_MODEL`, `GEMINI_TEXT_MODEL`, `GEMINI_VISION_MODEL`로 주 모델을 지정합니다. `GEMINI_FALLBACK_MODELS`에는 쉼표로 구분한 폴백 모델을, `GEMINI_THINKING_LEVEL`에는 사용할 사고 수준을 설정할 수 있습니다.
- 댓글·주간 텍스트 분석의 기본 엔진은 DeepSeek(`TEXT_ANALYSIS_ENGINE=deepseek`)입니다. `DEEPSEEK_API_KEY`를 설정하고 필요에 따라 `DEEPSEEK_MODEL`, `DEEPSEEK_COMPLEX_MODEL`로 모델을 지정합니다. 텍스트 분석도 Gemini로 실행하려면 `TEXT_ANALYSIS_ENGINE=gemini`로 설정합니다.
- `daily`는 최근 7일 후보를 확인해 유효한 기존 분석을 재사용합니다. `own-channel`은 유효한 Gemini 분석만 저장하며, 댓글 분석이 실패해도 Gemini 결과는 저장하고 종료 코드 1을 반환합니다.
- 로컬 A-track 회귀 검증은 `python -m unittest tests/test_pipeline.py -v`로 실행합니다. 테스트가 네트워크 호출을 대체합니다.

### B-track 실행

| 명령 | 작업 |
| --- | --- |
| `python main.py b-track-init` | 시드 채널 등록 |
| `python main.py b-track-collect` | 시드 채널의 영상 메타데이터 수집 |
| `python main.py b-track-scan` | 미분석 영상 브로드 스캔 |
| `python main.py b-track-cluster` | 엔티티 멘션을 트렌드 클러스터로 집계 |
| `python main.py b-track-score [최대 엔티티 수]` | 엔티티 점수와 답변 카드 생성. 기본 500개 |
| `python main.py b-track-discover` | 시드 후보 채널 발굴 및 분류 |
| `python main.py b-track-run` | 수집 → 스캔 → 클러스터 순차 실행 |

B-track에는 `SUPABASE_URL`과 `SUPABASE_KEY`가 필요합니다. 시드 등록, 수집, 발굴, 통합 실행에는 `YOUTUBE_API_KEY`도 필요합니다. 스캔과 통합 실행에는 `DEEPSEEK_API_KEY` 또는 `GEMINI_API_KEY`가 필요하며, AI Studio로 발굴 후보를 분류할 때는 `GEMINI_API_KEY`가 필요합니다. B-track Gemini 기본 모델은 `gemini-3.5-flash-lite`이고 기본 폴백은 `gemini-2.5-flash-lite`입니다. 주 모델은 `GEMINI_BTRACK_MODEL`로 지정합니다.

## 📋 시스템 개요

YouTube 경쟁 채널 자동 분석 + 트렌드 모니터링 + 텔레그램 알림 시스템

### 자동 실행 스케줄

| 시간 | 작업 |
|------|------|
| 매일 00시 (KST) | 어제 새 영상 자동 수집/분석 |
| 매일 10시 (KST) | 일일 트렌드 알림 (텔레그램) |
| 월요일 10시 (KST) | 주간 종합 리포트 (텔레그램) |

### 분석 대상 채널 (10개)

최화정, 이민정, 한가인, 강주은, 고소영, 한지혜, 이지혜, 장영란, 홍진경, 서인영

---

## 🚀 설치 가이드 (단계별)

### 1단계: GitHub 저장소 생성

1. https://github.com/new 접속
2. Repository name: `hangoeun-analyzer` (또는 원하는 이름)
3. **Private**으로 설정 (중요!)
4. "Create repository" 클릭

### 2단계: 코드 업로드

받으신 모든 파일을 GitHub 저장소에 업로드:
- 웹에서 "uploading an existing file" 클릭
- 모든 .py, .txt, .yml 파일 드래그
- ".github/workflows/" 폴더 구조 그대로 유지

### 3단계: Google 서비스 계정 생성 (구글 시트 자동 쓰기용)

1. https://console.cloud.google.com 접속
2. 좌측 메뉴 → "IAM 및 관리자" → "서비스 계정"
3. "서비스 계정 만들기" 클릭
4. 이름: `sheets-bot` (자유)
5. "역할" 단계 → "편집자" 선택
6. 만들어진 계정 클릭 → "키" 탭 → "키 추가" → "새 키 만들기" → JSON
7. 다운로드된 JSON 파일 내용 전체 복사 (메모장으로 열어서)

### 4단계: 구글 시트 권한 부여

1. https://docs.google.com/spreadsheets/d/1wyCxiyqeMoXxKZ9meLSYFh-hcVogS0Ltn_NblspyoZM/edit 접속
2. 우측 상단 "공유" 버튼
3. 위에서 만든 서비스 계정 이메일 추가 (예: `sheets-bot@xxx.iam.gserviceaccount.com`)
4. 권한: **편집자**
5. "전송" 클릭

### 5단계: GitHub Secrets 등록 (가장 중요!)

GitHub 저장소 → "Settings" → "Secrets and variables" → "Actions" → "New repository secret"

아래 7개 모두 등록:

| Name | Value |
|------|-------|
| `YOUTUBE_API_KEY` | YouTube API 키 |
| `GEMINI_API_KEY` | Gemini API 키 |
| `DEEPSEEK_API_KEY` | 딥시크 API 키 |
| `TELEGRAM_BOT_TOKEN` | 텔레그램 봇 토큰 |
| `TELEGRAM_CHAT_ID` | 텔레그램 Chat ID |
| `GOOGLE_SHEET_ID` | `1wyCxiyqeMoXxKZ9meLSYFh-hcVogS0Ltn_NblspyoZM` |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | 3단계에서 받은 JSON 전체 |

### 6단계: 첫 실행 (초기 데이터 수집)

1. GitHub 저장소 → "Actions" 탭
2. 좌측 "초기 데이터 수집 (수동 실행)" 클릭
3. 우측 "Run workflow" 버튼 → "Run workflow"
4. 실행 진행 상황은 Actions 탭에서 확인
5. 완료되면 텔레그램으로 알림 옴 (약 30분~1시간 소요)

### 7단계: 자동 실행 확인

이후 자동 스케줄링 작동:
- 매일 00시: `daily.yml`
- 매일 10시: `trend.yml`  
- 매주 월요일 10시: `weekly.yml`

---

## 🛠 문제 해결

### 채널을 못 찾을 때
`config.py`의 `TARGET_CHANNELS` 리스트에서 채널명을 정확한 채널명으로 수정

### Rate limit 오류
- Gemini: 분당 15회 제한 → 자동으로 대기
- YouTube API: 일일 10,000 유닛 → 충분함

### 비용 모니터링
- 딥시크: https://platform.deepseek.com → Billing
- 5월 31일까지 V4 Pro 75% 할인, 이후 V4 Flash 자동 전환

---

## 📊 결과 확인

- **구글 시트**: 모든 데이터 영구 저장
  - `영상_마스터데이터`: 모든 영상 메타데이터
  - `AI_분석결과`: Gemini + 댓글 분석 결과
  - `채널별_성공공식`: 채널별 인사이트
  - `일일_트렌드`: 매일 트렌드 기록
  - `주간_리포트`: 주간 종합 분석

- **텔레그램**: 실시간 알림
  - 일일 트렌드 (오전 10시)
  - 주간 리포트 (월요일 오전 10시)
  - 시스템 오류 즉시 알림
