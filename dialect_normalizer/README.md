# dialect_normalizer — 제주어 이해 + 구조화 (대화·초안 모듈)

민원서 작성 에이전트의 **대화·초안 모듈** 중 앞부분입니다.

| 단계 | 파일 | 출력 |
|---|---|---|
| 방언 → 표준어 | `normalize.py` | 표준어 문장, 변환 근거, 확인 질문 |
| 구조화 | `structure.py` | 근거 패키지 엔진 입력(`evidence_engine/contracts/query.schema.json`) + 되물을 질문 |

LLM은 **Claude API**만 쓰며, 모든 호출은 `llm.py` 한 곳을 거칩니다.
팀 규칙과 같이 `ALLOW_PAID_CALLS=1`이 없으면 실제 호출을 막고, 사용량을 `logs/usage.csv`에 남깁니다.

## 방식: 사전 + 예문 검색 + LLM (RAG)

LLM이 근거 없이 추측하는 것을 줄이기 위해, 변환을 LLM에게 전부 맡기지 않습니다.

```
① 어절 나누기 → ② 방언 사전 찾기 → ③ 비슷한 예문 찾기 → ④ LLM 변환 → ⑤ 근거 검사
   (Python)        (Python)            (Python, TF-IDF)      (OpenAI)      (Python)
```

- ②에서 **방언 비율**(방언으로 쓰인 횟수 ÷ 전체 등장 횟수)이 10% 미만인 단어(예: `안`, `잘`)는 근거에서 뺍니다.
  50% 미만은 "표준어로도 자주 쓰임"으로 표시해 LLM이 신중하게 판단하게 합니다.
- ⑤에서 사전 근거 없이 바뀐 단어는 `추정`으로 표시하고 사용자에게 확인 질문을 만듭니다.
- LLM이 "바꿨다"고 보고했지만 실제로는 그대로인 단어는 무시하고, 기록하지 않고 바꾼 단어는 경고로 표시합니다.
- 확인 질문은 단어 뜻만 묻도록 제한합니다 (내용·상황 질문은 기본 질문으로 교체).

## 구조화 (`structure.py`)

```
① 개인정보 가리기 → ② 방언 → 표준어 → ③ Claude 구조화 → ④ 코드 검사·질문 생성 → ⑤ 계약 스키마 검증
   (LLM 보내기 전)                        (Structured Outputs)
```

- 유형: 교통, 의료 접근, 환경·청소, 안전·재난, 생활물가, 기타 (엔진 README 권장 유형 + 기타)
- 읍면동: 제주 43개 행정동 목록 안에서만 고르게 강제하고, "노형" → "노형동"처럼 줄여 말한 것도 찾음. 모르면 되물음
- 주장(claims): 데이터로 확인할 수 있는 짧은 평서문 1~3개. 팀원 예시 질의(`tests/fixtures`)를 few-shot 예시로 사용
- 요구사항·기간: 사용자가 말한 경우에만 채움 (추측하지 않음)
- `dialect_confidence`: 바꾼 단어와 모르는 단어 중 사전 근거가 있는 비율
- `raw_text`는 기본으로 넣지 않음 (`--include-raw`를 주면 개인정보를 가린 원문만)
- `status`: `ready`(엔진에 보낼 수 있음) / `need_input`(읍면동·주장 등 필수 정보 부족 → `questions`로 되묻기)

## 파일 구성

| 파일 | 역할 |
|---|---|
| `preprocess.py` | AI Hub 라벨 JSON → 방언 사전, 예문 DB, 평가셋 생성 |
| `normalize.py` | 변환 모듈 (`normalize(text)` 함수 + 명령줄 실행) |
| `structure.py` | 구조화 질의 생성 (`structure(text)`, `update(result, ...)`) |
| `llm.py` | Claude 호출 통로 (유료 호출 차단, 사용량 기록, 캐시, mock) |
| `evaluate.py` | A(LLM만) vs B(사전+예문+LLM) 비교 평가 |
| `tests/` | pytest (네트워크·유료 호출 없음) |
| `requirements.txt` | 필요한 Python 패키지 |
| `.env.example` | API 키 설정 예시 (복사해서 `.env`로 사용) |

## 사용 데이터

| 데이터 | 출처 | 용도 |
|---|---|---|
| 한국어 방언 발화 데이터(제주도) | [AI Hub](https://aihub.or.kr/aihubdata/data/view.do?currMenu=115&topMenu=100&aihubDataSe=data&dataSetSn=121) | 일상대화 방언-표준어 쌍 |
| 중·노년층 한국어 방언 데이터(제주도 부분) | [AI Hub](https://www.aihub.or.kr/aihubdata/data/view.do?dataSetSn=71558) | 50대 이상 어르신 방언 보강 |

> AI Hub 원본 데이터와 가공 결과(`processed/`)는 저장소에 포함하지 않습니다. 아래 순서대로 직접 내려받아 생성하세요.

**전처리 결과 (v2.2 기준, v2.3에서 방언 비율 추가)**
- 방언 사전 59,629개 (어르신 데이터에서만 나온 단어 7,761개 포함)
- 예문 약 264만 문장 (일상대화 2,593,434 + 어르신 46,722)
- 평가셋: 일상대화 500문장, 어르신 500문장 (학습 데이터와 대화 파일·화자가 겹치지 않음)

## 실행 방법

### 1. 설치
macOS:
```bash
pip3 install -r requirements.txt
cp .env.example .env     # .env를 열어 ANTHROPIC_API_KEY 입력
```
Windows (PowerShell):
```powershell
py -3.11 -m pip install -r requirements.txt
copy .env.example .env
```
(Windows에서는 아래 명령의 `python3`를 `py -3.11`로 바꿔 실행)

### 2. 데이터 준비
AI Hub에서 **라벨링데이터만** 내려받아 이 폴더 안에 압축을 풉니다.
- 일상대화(제주) 라벨 → `label/`
- 중·노년층 제주 라벨 → `label_senior/`

### 3. 전처리 (처음 한 번)
```bash
python3 preprocess.py               # → processed/ 폴더 생성
python3 normalize.py --build-index  # → processed/example_index.pkl 생성 (1~3분)
```

### 4. 변환
```bash
python3 normalize.py "고냉이 허고 강생이들 왕 막 파불민 농사도 잘 안 돼요"
python3 normalize.py --zero-shot "..."   # 비교용: 사전·예문 없이 LLM만
python3 normalize.py                     # 대화형 모드
python3 normalize.py --json "..."        # 결과를 JSON으로 출력
```

### 5. 평가
```bash
python3 evaluate.py --limit 20     # 먼저 평가셋마다 20문장만 (비용·오류 확인)
python3 evaluate.py                # 전체: 평가셋 2개(일상대화·어르신) × 방법 2개
python3 evaluate.py --report       # API 호출 없이 저장된 결과로 점수만 다시 계산
```
- 결과는 `results/`에 저장되며, 중간에 끊겨도 다시 실행하면 이어서 진행합니다.
- 지표: 방언 어절 정답률(메인), 과잉 수정률, 문장 완전 일치율, chrF, 문장당 추정 변경·질문 수
- `results/examples_*.md`: A는 틀리고 B는 맞힌 문장 사례 (발표·기획서용)

### 6. 구조화
```bash
python3 structure.py "우리 동네 버스가 확 줄어부난 할망덜 병원 가기가 막 힘들어마씸 구좌읍"
python3 structure.py --out q.json "..."       # 엔진에 넣을 질의 파일 저장
python3 structure.py --check-fixtures         # 팀원 예시 10개로 유형·읍면동 정확도 확인 (Claude 10회 호출)
```
엔진과 연결: `cd ../evidence_engine && python run_engine.py --query ../dialect_normalizer/q.json`

### 7. 테스트
```bash
python3 -m pytest -q          # 네트워크·유료 호출 없이 실행
```

다른 코드에서:
```python
from normalize import normalize
result = normalize("고냉이 허고 강생이들 왕 막 파불민")
# result["standard"], result["changes"], result["ask_user"], result["warnings"]
```

## 전처리에서 처리한 데이터 문제

- `{laughing}` 같은 소리 표시 제거, 가명 표시(`@이름1`, `&name1&`, `#이름#`)는 `○○`로 통일
- 알아들을 수 없는 구간 `(( ))`이 있는 문장 제외
- **비식별화 오류**: 중·노년층 데이터에서 방언 어절만 `#이름#`으로 가려지고 표준어는 남은 경우,
  어절 단위로 표준어 쪽도 함께 가려 문장을 살림 (제외 비율 25% → 1.3%)

## 알려진 한계

- 일상대화 데이터 기반이라 민원 상황 어휘(배수로, 가로등 등)는 적을 수 있음
- 정답 데이터에도 작업자 오타·어절 경계 어긋남이 일부 있음 (최소 등장 횟수 조건으로 대부분 걸러짐)
- 방언 비율 기준(10%, 50%)은 평가 전에 정한 값이며, 평가 결과를 본 뒤 조정하지 않음

## 변경 이력

- v3.1: 문장 속 읍면동 우선, 말하지 않은 요구사항은 확인 질문으로, 지명 질문 제거, claims에 장소명 제외
- v3.0: OpenAI → Claude API 전환(`llm.py`), 구조화 모듈(`structure.py`) 추가, pytest 추가, 의존성 버전 고정, 평가 결과를 모델별 폴더에 저장
- v2.3: 방언 비율 추가, 가짜 변경 무시, 질문 규칙 강화, 문장부호 차이 무시, `evaluate.py` 추가
- v2.2: 일상대화 + 중·노년층 데이터 통합, 비식별화 오류 처리
