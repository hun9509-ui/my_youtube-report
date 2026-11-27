# Oracle 경쟁 채널 분석 엔진 운영 점검

2026년 11월 26일 22:34 KST 기준. Oracle 서버에서 Git 작업 트리, 활성 크론, 작업별 로그를 읽어 확인했다. 아래 시각은 한국 시간이며 저장 건수는 각 작업의 로그 기준이다.

## 일일 분석이 전부 실패해도 점수가 저장된다

11월 26일 일일 작업은 영상 5건을 Vertex AI의 `gemini-2.5-flash`로 분석하려 했다. 5건 모두 `Max retries exceeded`로 끝났고 로그에도 성공 0건으로 집계됐다. 같은 실행에서 `daily_analysis` 5건과 `video_scores` 5건을 저장했다는 기록이 이어졌고, 00:17에 ‘일일 수집 완료’가 출력됐다. 따라서 해당 실행으로 생성한 전략 점수 5건은 원본 분석의 유효성을 확인해야 한다. 로그에 최초 API 오류의 상태 코드가 없어 실패 원인은 확정할 수 없다.

## 작업별 운영 기록

| 작업 | 최근 확인 시각 | 로그에 기록된 결과 |
|---|---|---|
| 스냅샷 수집 | 11월 26일 09:02 | YouTube API 13회 호출, 스냅샷 648건 저장 |
| A·B 트렌드 분류 | 11월 26일 10:00 | `trend_classified` 26건 저장 |
| B트랙 엔티티 점수화 | 11월 26일 11:30 | 엔티티 11개 점수화 |
| 별도 B트랙 수집 | 11월 26일 21:05 | 58개 채널 처리, 신규 영상 16개 저장, 엔티티 68개 추출, 클러스터 5개 갱신 |
| 주간 채널 리포트 | 11월 23일 00:02 | 시장 분석에서 DeepSeek가 HTTP 402 `Insufficient Balance`를 반환한 뒤 전체 작업은 완료로 기록됨 |

11월 23일 오류는 당시 요청에 대한 응답이다. 충전 후 DeepSeek 호출 결과와 실제 텔레그램 수신 여부는 이번 점검에서 확인하지 않았다.

## 배포 전 대조할 운영 코드

서버의 기준 커밋은 `main`의 `faccb2b`다. 실제 크론은 서버 작업 트리의 `main.py`를 실행한다. 여기에는 두 종류의 트렌드 작업이 있다. `trend-collect`는 매일 A·B 분류를 실행하며, `b-track-run`은 2시간마다 별도로 수집과 엔티티 추출을 실행한다. `b-track-score`는 매일 11:30에 엔티티를 점수화한다.

- 서버에서 수정된 추적 파일 7개는 `main.py`, `config.py`, `db_migrate.py`, `deepseek_processor.py`, `gemini_analyzer.py`, `sheets_writer.py`, `youtube_collector.py`다.
- 서버에서 확인한 Git 미추적 B트랙 모듈 6개는 `b_track_collector.py`, `b_track_config.py`, `b_track_discovery.py`, `b_track_gemini.py`, `b_track_quota.py`, `b_track_scorer.py`다.

배포 전에는 서버의 소스 변경분과 B트랙 모듈을 먼저 보존하고 [엔진 개선 PR #1](https://github.com/hun9509-ui/my_youtube-report/pull/1)과 파일별로 병합해야 한다. 병합본에서는 영상 분석 5건이 모두 실패하는 경우를 재현해 신규 전략 점수 저장이 0건이고 실행 결과가 실패로 남는지 확인한다. 이어서 기존 B트랙 수집·점수화 명령이 유지되는지 검증한다.
