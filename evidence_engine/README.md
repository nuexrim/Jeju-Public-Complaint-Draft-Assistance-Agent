# 근거 엔진 (`evidence_engine/`)

구조화된 민원 질의를 받아 제주 공공데이터로 주장을 확인하고, 판정과 출처가 붙은 근거 패키지를 돌려주는 엔진.

- 입력: 구조화 질의 (`contracts/query.schema.json`)
- 출력: 근거 패키지 (`contracts/evidence.schema.json`), 뜻 선택지 (`contracts/interpretation.schema.json`)
- 값은 등록된 지표 함수와 읽기 전용 SQL로만 계산함.
- 판정은 규칙으로 내림.

## 실행

```bash
cd evidence_engine
python run_engine.py --query tests/fixtures/q01.json                 # 근거 패키지 출력
python run_engine.py --query tests/fixtures/q01.json --out out.json  # 파일로 저장
python run_engine.py --query tests/fixtures/q01.json --no-ontology   # 카탈로그 검색만
python run_engine.py --query tests/fixtures/q01.json --interpret     # 주장별 뜻 선택지
python run_engine.py --query tests/fixtures/q01.json --mock          # DB 없이 형식만
```

- 종료 코드: 성공이면 0, 입력 계약 위반이나 파일 오류면 2.

## 처리 순서

1. 입력 검증과 개인정보 가리기 (`package/privacy.py`), 원문 버리기
2. 읍면동 이름을 행정동 코드로 바꾸기 (`data/spatial/`)
3. 개념 확장: 주장 낱말을 온톨로지 개념으로 바꾸고 관계를 3단계까지 따라가 데이터 후보 찾기 (`retrieval/`, `ontology/`)
4. 카탈로그 검색: 데이터셋 카드 1,330건을 BM25로 찾고 개념 태그, 공간, 최신성으로 순위 매기기 (`catalog/`)
5. 두 결과를 합친 뒤 지표 등록부에서 지표 고르기 (`indicators/registry.yaml`)
6. 읽기 전용 SQL로 계산하고 같은 유형 지역과 비교하기 (`compute/`)
7. 규칙 판정, 교차 확인, 차트 (`verdict/`, `compute/charts.py`)
8. 근거 패키지 조립과 출력 스키마 검증 (`package/`)

- 순위 점수: 경로 0.4, 검색 0.3, 공간 일치 0.2, 최신성 0.1의 가중합(`config.yaml`의 `ranking`).

## 지표

- 판정 지표 17개와 맥락 지표 3개 등록.
- 새 지표는 `indicators/registry.yaml`에 표와 계산 방식, 뜻 선택지 문장을 적어 추가함.

| 주제 | 판정 지표 |
| --- | --- |
| 버스와 대중교통 | 인구 1천 명당 버스 정류소 수, 정류소 이용 인원 증감률(최근 12개월 대 직전 12개월), 노인 1인당 연간 경로 승차 인원, 제주 전체 19시 이후 승차 비중 |
| 병원과 약국 | 인구 1천 명당 의원 수, 65세 이상 1천 명당 의원 수, 공공심야약국 수, 가장 가까운 응급의료기관까지 거리, 인구 1만 명당 안전상비의약품 판매업소 수 |
| 교통사고와 보행 안전 | 교통사고 다발지역 사고 건수, 초등학교 통학로 위험 지점 수, 인구 1만 명당 보행자 교통사고 건수 |
| 쓰레기와 재활용 | 클린하우스 1곳당 인구, 인구 1만 명당 재활용도움센터 수 |
| 재난 대피시설 | 인구 1만 명당 이재민 수용 및 임시주거시설 수 |
| 방범과 CCTV | 인구 1만 명당 공공 CCTV 카메라 대수 (공개 목록 기준, 약한 자료) |
| 경로당 | 65세 이상 1천 명당 경로당 수 |

- 맥락 지표: 1년 인구 증감률, 65세 이상 인구 비율, 인구 밀도.
- 맥락 지표는 판정에 쓰지 않고 "함께 볼 자료"로만 붙임.

## 판정 기준

- 주장은 "이 지역에 문제가 있다"는 뜻으로 봄.
- 같은 유형 지역(읍면끼리, 동끼리)과 비교함.
- 기준값은 `config.yaml`의 `verdict`에 있음.

| 지표 종류 | 자료로 확인됨 | 자료와 다름 | 그 사이 |
| --- | --- | --- | --- |
| 순위형 | 비교 지역의 66.7% 이상이 이 지역보다 나을 때 | 33.3% 이하만 이 지역보다 나을 때 | 일부만 확인됨 |
| 증감형 (감소 주장) | 증감률 -5% 이하 | +2% 이상 | 일부만 확인됨 |

- 대리 지표나 약한 자료로 판정하면 "일부만 확인됨"으로 낮춤.
- 결측이 30%를 넘거나 기준일이 2년을 넘으면 주의 사항을 붙임.
- SQL은 읽기 전용. 결과는 최대 1,000행, 대기 시간은 10초까지.

## 폴더

| 폴더 | 내용 |
| --- | --- |
| `contracts/` | 입출력 JSON Schema |
| `data/ingest/` | 수집기와 수집 대상 정의(`datasets.yaml`) |
| `data/spatial/` | 행정동 코드표, 이름과 법정동 매칭, 좌표 공간 결합 |
| `data/` | `build_db.py`(적재), `derived.py`(파생 표), `prefetch.py`(작은 표 미리 받기), `snapshot.py`, `data_spec.py` |
| `catalog/` | 데이터셋 카드, BM25 인덱스, 개념 태그 |
| `ontology/` | 상위 개념(`seed_core.ttl`), 확정 온톨로지(`jeju.ttl`), 경로 탐색, 역량 질문 평가 |
| `retrieval/` | 개념 확장, 카탈로그 검색, 병합 순위 |
| `indicators/` | 지표 등록부와 계산 |
| `compute/` | 읽기 전용 SQL 실행, 차트 |
| `verdict/` | 규칙 판정 |
| `package/` | 근거 패키지 조립, 개인정보 가리기, 범용 틀, 화면 표기 이름 |
| `agent/` | LLM 클라이언트(유료 호출 가드), 근거 탐색 에이전트, SQL 초안 도우미 |
| `tests/` | pytest 291개와 예시 질의 10개 |

## LLM 사용

- 엔진은 기본 설정(`llm.provider: mock`)에서 LLM을 부르지 않음.
- 공급자를 켜면 두 기능이 동작함.
  - 근거 탐색 에이전트(`agent/evidence_agent.py`): Claude가 등록 지표 계산(`check_indicator`)과 끝내기(`finish`) 두 도구만으로 어떤 교차 확인을 붙일지 고름. SQL 작성이나 숫자 생성 불가. 주장 하나에 최대 4번 호출.
  - SQL 초안(`agent/planner.py`): 맞는 지표가 없는 주장에 읽기 전용 SQL 초안을 받아 실행하고, 결과에 "검토 전" 주의 사항을 붙임.
- 제출본은 두 기능을 끈 상태로 동작함.
- 꺼져 있거나 실패하면 지표마다 정해 둔 교차 확인 목록을 사용함.

## 데이터

- 적재와 스냅샷, 새 데이터 추가 방법: [데이터 파이프라인](../docs/data-pipeline.md)
- 데이터 목록과 출처: [데이터 활용 명세](../데이터_활용_명세.md)
