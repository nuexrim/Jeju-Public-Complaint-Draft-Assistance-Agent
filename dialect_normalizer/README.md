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

- 개인정보 가리기는 근거 엔진의 `evidence_engine/package/privacy.py`를 그대로 씁니다(자리표시 `○○`). 구현을 두 벌로 두지 않습니다.
- 유형: 교통, 의료 접근, 환경·청소, 안전·재난, 복지, 생활물가, 기타 (엔진 README 권장 유형 + 기타)
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
cp .env.example .env     # .env를 열어 ANTHROPIC_API_KEY 입력 (ALLOW_PAID_CALLS 는 쓸 때 직접 켬)
```
Windows (PowerShell):
```powershell
py -3.11 -m pip install -r requirements.txt
copy .env.example .env
```
(아래 명령은 가상환경을 켠 뒤 기준입니다. 가상환경 없이 Windows에서 실행하면 `python`을 `py -3.11`로 바꿉니다)

### 2. 데이터 준비
AI Hub에서 **라벨링데이터만** 내려받아 이 폴더 안에 압축을 풉니다.
- 일상대화(제주) 라벨 → `label/`
- 중·노년층 제주 라벨 → `label_senior/`

### 3. 전처리 (처음 한 번)
```bash
python preprocess.py               # → processed/ 폴더 생성
python normalize.py --build-index  # → processed/example_index.pkl 생성 (1~3분)
```

### 4. 변환
```bash
python normalize.py "고냉이 허고 강생이들 왕 막 파불민 농사도 잘 안 돼요"
python normalize.py --zero-shot "..."   # 비교용: 사전·예문 없이 LLM만
python normalize.py                     # 대화형 모드
python normalize.py --json "..."        # 결과를 JSON으로 출력
```

### 5. 평가
```bash
python evaluate.py --limit 20     # 먼저 평가셋마다 20문장만 (비용·오류 확인)
python evaluate.py                # 전체: 평가셋 2개(일상대화·어르신) × 방법 2개
python evaluate.py --report       # API 호출 없이 저장된 결과로 점수만 다시 계산
```
- 결과는 `results/`에 저장되며, 중간에 끊겨도 다시 실행하면 이어서 진행합니다.
- 지표: ERR(메인), 재현율, FPR, 단어 정확도, LAI 정확도, CER, BLEU, chrF 등 — 계산식과 근거는 아래 [평가 지표](#평가-지표) 참고
- `results/examples_*.md`: A는 틀리고 B는 맞힌 문장 사례 (발표·기획서용)

### 6. 구조화
```bash
python structure.py "우리 동네 버스가 확 줄어부난 할망덜 병원 가기가 막 힘들어마씸 구좌읍"
python structure.py --out q.json "..."       # 엔진에 넣을 질의 파일 저장
python structure.py --check-fixtures         # 팀원 예시 10개로 유형·읍면동 정확도 확인 (Claude 10회 호출)
```
엔진과 연결: `cd ../evidence_engine && python run_engine.py --query ../dialect_normalizer/q.json`

### 7. 테스트
```bash
python -m pytest -q          # 네트워크·유료 호출 없이 실행
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
- 이중 전사 표기 `(방언)/(표준어)`가 남은 경우 방언 쪽은 앞, 표준어 쪽은 뒤를 남기고 `#` 기호 제거
- **비식별화 오류**: 중·노년층 데이터에서 방언 어절만 `#이름#`으로 가려지고 표준어는 남은 경우,
  어절 단위로 표준어 쪽도 함께 가려 문장을 살림 (제외 비율 25% → 1.3%)

## 평가 지표

### 원칙
- **무엇을 재는가**(지표 이름·계산식)는 선행 연구의 정의를 그대로 따른다.
- **우리 데이터에서 어떻게 세는가**(적용 규칙)는 아래 "적용 규칙"에 모두 공개한다.

### 어절 단위 지표
선행 연구의 어휘 정규화 평가(MultiLexNorm)와 같이, 입력 어절 하나를 아래 네 칸 중 하나로 분류한다.

| 분류 | 정의 (van der Goot et al., 2021) |
|---|---|
| TP | 작업자가 바꾼 어절을 시스템도 올바른 표준형으로 바꿈 |
| FN | 작업자가 바꾼 어절을 시스템이 올바르게 바꾸지 못함 (틀린 표준형으로 바꾼 경우 포함) |
| FP | 작업자가 바꾸지 않은 어절을 시스템이 바꿈 |
| TN | 작업자도 시스템도 바꾸지 않음 |

"작업자가 바꾼 어절"은 AI Hub 라벨의 `eojeolList`에서 `isDialect = true`이고 표준형이 다른 어절이다.

| 지표 | 계산식 | 뜻 | 근거 |
|---|---|---|---|
| **ERR (메인)** | (TP − FP) / (TP + FN) | 바꿔야 할 어절 대비, 맞게 바꾼 수에서 잘못 바꾼 수를 뺀 비율. 아무것도 안 바꾸면 0, 음수 가능 | van der Goot (2019), MultiLexNorm (2021) 공식 지표 |
| 재현율 | TP / (TP + FN) | 바꿔야 할 방언 어절 중 맞게 바꾼 비율 | 분류 평가 표준 정의. W-NUT 2015는 토큰 단위 정밀도·재현율·F1로 평가 |
| FPR | FP / (FP + TN) | 원래 표준어인 어절을 잘못 바꾼 비율 (낮을수록 좋음) | 분류 평가 표준 정의 (위 논문들이 직접 보고한 지표는 아님. 과잉 수정을 따로 보이기 위해 추가) |
| 단어 정확도 | (TP + TN) / 전체 어절 | 전체 어절 중 올바른 상태인 비율 | Kuparinen et al. (2023) |
| LAI 정확도 | (FP + TN) / 전체 어절 | 아무것도 안 바꿨을 때의 단어 정확도 (기준선) | Kuparinen et al. (2023), MultiLexNorm (2021) |

단어 정확도만 보면 원래 표준어인 어절이 많은 데이터에서 점수가 높게 나온다. 그래서 바꿔야 할 어절 수로 보정한 ERR을 메인으로 쓰고, LAI 정확도를 함께 적는다.

### 문장 단위 지표

| 지표 | 계산식 | 근거 |
|---|---|---|
| CER (공백 포함) | 글자 편집 거리 합 / 정답 문장 글자 수 합 (연속 공백은 하나로) | Kuparinen et al. (2023) |
| CER (공백 제외) | 위와 같되 모든 공백을 지우고 계산. 띄어쓰기 차이의 영향을 따로 보기 위해 함께 보고 | 위의 적용 변형 |
| BLEU | sacrebleu `corpus_bleu` 기본 설정 (13a 토크나이저, 어절 단위) | 한국어 방언 번역 연구(임상범 외, 2021·2022)에서 사용 |
| chrF | sacrebleu `corpus_chrf` 기본 설정 | 기계번역 평가 표준 지표 |
| 문장 완전 일치율 | 띄어쓰기·문장부호를 지운 뒤 정답 문장과 같은 비율 | 보조 지표 |

시스템 동작 지표로 문장당 **추정 변경 수**(사전 근거 없는 변경)와 **질문 수**도 함께 보고한다.

### 적용 규칙 (우리 데이터에 맞춰 정한 것)

| 항목 | 규칙 | 이유 |
|---|---|---|
| 단위 | AI Hub 라벨의 **어절** | 정답 라벨이 어절 단위로 제공됨 |
| 정답 라벨 연결 | 입력 문장을 띄어쓰기로 나눈 어절과 `eojeolList`의 방언 어절을 앞에서부터 순서대로 맞춤. 맞추지 못한 정답 쌍 수는 "미매칭"으로 함께 출력 | 문장과 어절 목록의 표기가 드물게 다름 |
| 입력·출력 정렬 | 선행 연구는 입력과 출력이 1:1로 짝지어진 데이터를 쓰지만, LLM 출력은 짝이 없음. 그래서 입력·출력 어절을 **편집 거리(difflib)로 정렬**한 뒤 분류 | 정렬 없는 출력에 같은 정의를 적용하기 위한 근사 |
| 바뀐 구간 | 어절 수가 같으면 1:1로, 다르면(쪼개짐·합쳐짐) 구간 전체를 붙여 비교. 예: `죽어부난 → 죽어 버려서` | 한 어절이 여러 어절로 바뀌는 경우가 흔함 |
| 비교 방식 | 띄어쓰기·문장부호는 무시. 정답 표준형이 출력 구간 안에 들어 있으면 TP, 원래 어절이 출력 구간 안에 남아 있으면 TN | `고양이 하고` / `고양이하고` 같은 띄어쓰기 차이는 오류로 보지 않음 |
| 조사가 붙은 경우 | `냄새 → 냄새가`처럼 원래 어절이 그대로 포함되면 TN | 뜻이 바뀌지 않은 변화. 단, 이 때문에 일부 변경이 TN으로 관대하게 세어질 수 있음 |
| 새로 생긴 어절 | 어절 단위 지표에서는 세지 않고 CER에 반영 | 네 칸 분류는 입력 어절 기준 |
| 가명 어절 `○○` | 제외 | 원래 단어를 알 수 없음 |
| 틀린 표준형 | FN으로만 셈 (FP 아님) | MultiLexNorm 정의와 동일 |

### 한계
- ERR은 FP와 FN을 구분하지 못한다. 정답 데이터가 방언 표시를 빠뜨린 어절을 시스템이 올바르게 바꿔도 FP로 감점된다.
- 정답 표준형과 뜻은 같지만 표현이 다르면(`먹여서` / `먹이고`) FN으로 센다. 그래서 실제 품질보다 낮게 나올 수 있다.
- 편집 거리 정렬은 근사이므로, 출력이 입력과 크게 다르면 분류가 부정확할 수 있다.
- `--limit 20`처럼 표본이 작으면 오차 범위가 크다. 최종 수치는 평가셋 전체(500문장씩)로 낸다.

### 참고문헌
- Rob van der Goot. 2019. *Normalization and Parsing Algorithms for Uncertain Input.* PhD thesis, University of Groningen. (5장: 정규화 평가와 ERR)
- Rob van der Goot, Alan Ramponi, Arkaitz Zubiaga, Barbara Plank, Benjamin Muller, Iñaki San Vicente Roncal, Nikola Ljubešić, Özlem Çetinoğlu, et al. 2021. MultiLexNorm: A Shared Task on Multilingual Lexical Normalization. *Proceedings of the Seventh Workshop on Noisy User-generated Text (W-NUT 2021)*, pp. 493–509. https://aclanthology.org/2021.wnut-1.55
- Timothy Baldwin, Marie Catherine de Marneffe, Bo Han, Young-Bum Kim, Alan Ritter, Wei Xu. 2015. Shared Tasks of the 2015 Workshop on Noisy User-generated Text: Twitter Lexical Normalization and Named Entity Recognition. *Proceedings of the ACL 2015 Workshop on Noisy User-generated Text*, pp. 126–135.
- Olli Kuparinen, Aleksandra Miletić, Yves Scherrer. 2023. Dialect-to-Standard Normalization: A Large-Scale Multilingual Evaluation. *Findings of EMNLP 2023*, pp. 13814–13828. https://aclanthology.org/2023.findings-emnlp.923
- 임상범, 박찬준, 조재춘, 양영욱. 2021. 딥러닝 기반 한국어 방언 기계번역 연구. *제33회 한글 및 한국어 정보처리 학술대회 논문집.*
- 임상범, 박찬준, 양영욱. 2022. 언어적 특성과 서비스를 고려한 딥러닝 기반 한국어 방언 기계번역 연구.
- Matt Post. 2018. A Call for Clarity in Reporting BLEU Scores. *WMT 2018.* (sacrebleu)

## 알려진 한계

- 일상대화 데이터 기반이라 민원 상황 어휘(배수로, 가로등 등)는 적을 수 있음
- 정답 데이터에도 작업자 오타·어절 경계 어긋남이 일부 있음 (최소 등장 횟수 조건으로 대부분 걸러짐)
- 방언 비율 기준(10%, 50%)은 평가 전에 정한 값이며, 평가 결과를 본 뒤 조정하지 않음

## 변경 이력

- v3.4 (연결): 마스킹을 근거 엔진 `privacy.py`로 통일(님 호칭 이름 추가, 날짜 숫자·'할망씨' 과잉 가림 해소), 읍면동 이름 뒤에 다른 낱말이 붙으면 지명으로 보지 않음('연동되어'), 사전·색인이 없을 때 `sys.exit` 대신 `ResourceMissing` 예외, 키가 없으면 `LLMError`, `.env.example`의 `ALLOW_PAID_CALLS=1` 주석 처리, AI Hub 문장이 든 평가 결과 하위 폴더도 커밋 제외

- v3.3: 평가 지표를 선행 연구 정의로 교체 — ERR(메인)·재현율·FPR·단어 정확도·LAI 정확도(어절 편집 거리 정렬 후 TP/FN/FP/TN 분류), CER(공백 포함·제외)·BLEU 추가, README에 계산식·근거·적용 규칙 정리
- v3.2: 일상대화 데이터의 이중 전사 표기 "(방언)/(표준어)"와 '#' 기호 정리(학습 문장 2.9%, 일상대화 평가 문장 21%에 남아 있었음), 문장에 읍면동이 직접 있으면 확인 질문 생략
- v3.1: 문장 속 읍면동 우선, 말하지 않은 요구사항은 확인 질문으로, 지명 질문 제거, claims에 장소명 제외
- v3.0: OpenAI → Claude API 전환(`llm.py`), 구조화 모듈(`structure.py`) 추가, pytest 추가, 의존성 버전 고정, 평가 결과를 모델별 폴더에 저장
- v2.3: 방언 비율 추가, 가짜 변경 무시, 질문 규칙 강화, 문장부호 차이 무시, `evaluate.py` 추가
- v2.2: 일상대화 + 중·노년층 데이터 통합, 비식별화 오류 처리
