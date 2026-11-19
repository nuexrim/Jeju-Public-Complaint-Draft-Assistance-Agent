# 데이터 파이프라인

근거 DB(`evidence_engine/data/jeju.duckdb`)를 원자료부터 다시 만들거나, 새 데이터를 추가할 때 참고하는 문서.

- 센터 PC에서 실행만 할 때는 불필요
- 설치 스크립트가 스냅샷으로 DB를 만들어 줌
- 명령은 모두 `evidence_engine/` 폴더에서 실행
- 원자료는 `data/ingest/raw/` 아래에 모이며, 저장소에는 올리지 않음

## 준비

`evidence_engine/.env.example`을 `evidence_engine/.env`로 복사하고 키를 채움.

| 이름 | 용도 | 발급 방법 |
| --- | --- | --- |
| `JEJUDATAHUB_PROJECT_KEY` | 제주데이터허브 Open API | 제주데이터허브 회원가입 → 프로젝트 생성 → 키 발급. 쓸 API마다 데이터 상세 화면에서 "프로젝트 담기" 필수 (안 하면 HTTP 403) |
| `DATA_GO_KR_SERVICE_KEY` | 공공데이터포털 Open API (행정동 통반 인구, 법정동 인구) | data.go.kr 회원가입 → API 상세에서 활용신청 → 마이페이지의 일반 인증키(Decoding) |

- 두 키 모두 무료
- 데이터를 새로 받을 때만 필요

## 원자료부터 만들기

| 순서 | 명령 또는 작업 | 결과 |
| --- | --- | --- |
| 1 | `python data/ingest/fetch_hub_catalog.py` | 데이터허브 전체 목록 |
| 2 | `python data/ingest/hub_files.py` (큰 파일까지 받으려면 `--large`) | 데이터허브 파일 |
| 3 | `python data/ingest/hub_api.py` | 데이터허브 Open API 결과 (API마다 하루 1,000건 제한) |
| 4 | `datasets.yaml`의 `portal_files`에 적힌 파일을 공공데이터포털에서 받아 `raw/datagokr/`에 파일명 그대로 넣기 | 허브 등록 데이터의 원본 파일 18종 |
| 5 | [법정동 연계정보](https://www.data.go.kr/data/15136368/fileData.do)와 [법정동별 주민등록 인구](https://www.data.go.kr/data/15099158/fileData.do) CSV를 `raw/emd_link/`에 넣고 `python data/ingest/portal_api.py --month <YYYY-MM>` | 리 이름 연결표, 인구 분모 |
| 6 | [vuski/admdongkor](https://github.com/vuski/admdongkor)의 `HangJeongDong_ver<날짜>.geojson`을 `raw/boundary/`에 넣기 | 행정동 경계 |
| 7 | 센터에서 받은 데이터 명세서 xlsx를 `raw/center/`에 넣기 | 데이터셋 카드용 (외부 공개 금지) |
| 8 | `python data/build_db.py` | DuckDB 적재와 파생 표 (`pop_emd`, `emd_geo`, `bus_usage_monthly`), 약 20초 |
| 9 | `python catalog/build_cards.py` → `python catalog/tag_concepts.py --rules` | 데이터셋 카드와 규칙 개념 태그 |
| 10 | `python data/ingest/hub_files.py --small 10` → `python data/prefetch.py` | 10MB 이하 표 미리 받기, 진단표 `prefetch_profile` |
| 11 | `python data/snapshot.py --export <날짜>` | 재현용 스냅샷 |
| 12 | `python data/data_spec.py` | `데이터_활용_명세.md` 갱신 |

적재한 표마다 출처, 기준일, 행 수, 읍면동 연결 비율이 `dataset` 표에 기록됨.

## 전처리에서 하는 일

- 읍면동 키 붙이기: 다음 방식으로 행정동 코드를 붙임
  - 읍면동 이름 컬럼이 있으면 이름으로
  - 주소만 있으면 주소 문자열에서 찾음
  - 법정동이나 리 이름은 법정동 연계표로
  - 좌표만 있으면 행정동 경계와 공간 결합으로
- 기간 맞추기: `yyyy`, `yyyymm`, `yyyymmdd`, 연과 월이 나뉜 컬럼 등 파일마다 다른 형식을 `datasets.yaml`에 적어 두고 같은 형식으로 바꿈
- 깨진 파일 고치기: 따옴표 없이 쉼표가 들어간 컬럼이 있는 CSV는 `ragged` 설정으로 다시 나눠 읽음
- 서귀포시 7개 동(송산, 정방, 중앙, 천지, 영천, 동홍, 서홍): 법정동이 여러 행정동에 걸쳐 있어서 통반 단위 인구를 행정동 코드로 더해 인구를 채움
- 버스 정류소 이용 실적: 정류소 이름만 있어서 이름으로 행정동에 연결함
  - 띄어쓰기, 쉼표, 괄호 설명 차이는 맞춤
  - 같은 이름이 여러 동에 있으면 연결하지 않음
- 기준일이 2년을 넘는 표로 만든 근거는 근거 카드에 "오래된 자료"로 표시함

## 스냅샷

```bash
python data/snapshot.py --export 2026-11-17     # DB 전체를 parquet(zstd)로 내보내기 + manifest.json
python data/snapshot.py --verify 2026-11-17     # 해시 대조
python data/snapshot.py --restore 2026-11-17    # 스냅샷으로 DB 다시 만들기 (센터 PC 설치에서 사용)
```

- `config.yaml`의 `snapshot`에 날짜를 적으면 엔진이 "오래된 자료"를 판단하는 기준일도 그 날짜로 고정됨
- 따라서 언제 실행해도 같은 결과가 나옴
- 스냅샷 날짜를 바꿀 때는 `config.yaml`, `설치.bat`, `make_submission.py` 세 곳을 함께 바꿔야 함
  - 테스트가 세 값이 같은지 검사함

## 새 데이터 추가하기

허브에 있지만 아직 받지 않은 데이터를 근거로 쓸 때의 절차. 데이터 하나에 30분에서 1시간 정도 소요.

1. 받기
   - 파일: `hub_files.py --ids <허브 id>`
   - API: 프로젝트 담기 후 `hub_api.py`
   - 허브가 외부 링크로만 안내하는 데이터: 공공데이터포털에서 직접 받음
2. `data/ingest/datasets.yaml`에 허브 id, 표 이름, 기간 컬럼과 형식, 읍면동 컬럼(이름 또는 주소), 좌표 컬럼을 적음
   - 컬럼명은 받은 파일의 실제 머리글을 그대로 사용
3. `python data/build_db.py`로 다시 적재
4. `indicators/registry.yaml`에 지표 등록
   - 지표로 등록해야 판정 근거가 됨
5. `python -m complaint_agent.eval.run_eval`로 정답 세트 결과가 나빠지지 않았는지 확인
6. 스냅샷, 데이터 활용 명세, 제출 ZIP 재생성

## 온톨로지 (개발 단계 전용)

센터 PC에는 확정본 `ontology/jeju.ttl`만 들어가며, 아래 단계는 실행하지 않음.

1. `git clone https://github.com/NadeenAhmad/RIGOR external/RIGOR`
2. 전용 가상환경 `external/.venv-rigor`에 rdflib, sentence-transformers, faiss-cpu, owlready2 설치
   - HermiT 일관성 검사에 Java 필요
3. `python ontology/export_schema.py`로 스키마와 RAG 문서 만들기
4. `external/.venv-rigor/bin/python ontology/build_ontology.py`로 후보 빌드와 HermiT 검사
   - 기본은 LLM 호출이 없는 결정적 빌드 (seed 상위 개념, RIGOR 직접 매핑, `table_concepts.yaml` 연결, 한글 라벨)
5. 검수 후 `build_ontology.py --accept`로 `ontology/jeju.ttl` 확정
6. `python ontology/evaluate_cq.py`로 역량 질문 평가 (15개 중 10개 이상이면 온톨로지 단계 사용)

- `--paid --max-calls N`을 주면 RIGOR의 LLM 보강 사용 가능
- 단, 이번 제출본의 온톨로지는 결정적 빌드로 만듦
