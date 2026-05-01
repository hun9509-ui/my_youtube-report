# 한고은 채널 분석 시스템

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
