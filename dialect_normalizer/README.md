# 제주어 이해와 구조화 (`dialect_normalizer/`)

제주말로 쓴 민원 문장을 표준어로 바꾸고, 근거 엔진이 받을 수 있는 구조화 질의와 되물을 질문을 만드는 모듈.

| 단계 | 파일 | 결과 |
| --- | --- | --- |
| 제주어 → 표준어 | `normalize.py` | 표준어 문장, 바꾼 낱말과 근거, 확인 질문 |
| 구조화 | `structure.py` | 구조화 질의(`evidence_engine/contracts/query.schema.json`)와 되물을 질문 |

- LLM은 Claude API만 사용. 모든 호출은 `llm.py` 한 곳을 지나감.
- `ALLOW_PAID_CALLS=1`이 없으면 실제 호출을 막음.
- 사용량은 `logs/usage.csv`에 기록함.

## 방식: 사전 + 예문 검색 + LLM

- 변환을 LLM에 통째로 맡기면 근거 없는 추측이 생김.
- 따라서 사전과 예문을 먼저 찾아 함께 넘기고, 결과를 다시 사전으로 검사함.

```
① 어절 나누기 → ② 방언 사전 찾기 → ③ 비슷한 예문 찾기 → ④ Claude 변환 → ⑤ 근거 검사
   (Python)        (Python)            (Python, TF-IDF)                     (Python)
```

- ② 방언 비율(방언으로 쓰인 횟수를 전체 등장 횟수로 나눈 값)이 10% 미만인 낱말(예: 안, 잘)은 근거에서 제외함.
- ② 방언 비율 50% 미만은 "표준어로도 자주 쓰임"으로 표시해 Claude가 신중하게 판단하게 함.
- ⑤ 사전 근거 없이 바뀐 낱말은 "추정"으로 표시하고 확인 질문을 만듦.
- Claude가 바꿨다고 했지만 실제로는 그대로인 낱말은 무시함.
- 기록 없이 바뀐 낱말은 경고로 남김.
- 확인 질문은 낱말 뜻만 묻도록 제한함. 내용이나 상황을 묻는 질문은 기본 질문으로 바꿈.
- 결과 문장에 그대로 남긴 낱말이 사전에서 표준어로 더 자주 쓰이는 말(방언 비율 50% 미만)이면 묻지 않음.
  - 막, 확 같은 낱말 때문에 질문이 늘지 않게 하려는 규칙임.

## 구조화 (`structure.py`)

```
① 개인정보 가리기 → ② 제주어 → 표준어 → ③ Claude 구조화 → ④ 코드 검사와 질문 만들기 → ⑤ 스키마 검증
   (Claude에 보내기 전)                    (Structured Outputs)
```

- 개인정보 가리기: 근거 엔진의 `evidence_engine/package/privacy.py`를 그대로 사용(`○○`). 같은 규칙을 두 군데에 두지 않기 위함.
- 유형: `교통`, `의료 접근`, `환경·청소`, `안전·재난`, `복지`, `생활물가`, `기타`
- 읍면동: 제주 43개 행정동 목록 안에서만 고르게 함.
  - "노형"처럼 줄여 말해도 "노형동"으로 찾음.
  - 모르면 되물음.
- 주장: 데이터로 확인할 수 있는 짧은 평서문 1~3개. 근거 엔진의 예시 질의(`evidence_engine/tests/fixtures`)를 few-shot 예시로 사용.
- 요구사항과 기간: 사용자가 말한 경우에만 채움. 추측하지 않음.
- `dialect_confidence`: 바꾼 낱말과 모르는 낱말 중 사전 근거가 있는 비율.
- `raw_text`: 기본으로 넣지 않음. `--include-raw`를 주면 개인정보를 가린 원문만 넣음.
- `status`: `ready`(엔진에 보낼 수 있는 상태) 또는 `need_input`(필수 정보가 부족해서 `questions`로 되묻는 상태)

## 파일

| 파일 | 역할 |
| --- | --- |
| `preprocess.py` | AI Hub 라벨 JSON으로 방언 사전, 예문 DB, 평가셋 만들기 |
| `normalize.py` | 변환 (`normalize(text)`, 명령줄 실행). 프롬프트 `RULES_RAG`, `RULES_ZERO` |
| `structure.py` | 구조화 질의 만들기 (`structure(text)`, `update(result, ...)`). 프롬프트 `RULES` |
| `llm.py` | Claude 호출 통로 (유료 호출 차단, 시간 제한, 다시 시도, 사용량 기록, 캐시, mock) |
| `evaluate.py` | LLM만 쓴 방식(A)과 사전과 예문을 함께 쓴 방식(B) 비교 평가 |
| `tests/` | pytest 41개, 네트워크와 유료 호출 없이 실행 |
| `.env.example` | 키 설정 예시 (복사해서 `.env`로 사용) |

## 사용 데이터

| 데이터 | 출처 | 용도 |
| --- | --- | --- |
| 한국어 방언 발화 데이터(제주도) | [AI Hub](https://aihub.or.kr/aihubdata/data/view.do?currMenu=115&topMenu=100&aihubDataSe=data&dataSetSn=121) | 일상대화 방언과 표준어 쌍 |
| 중·노년층 한국어 방언 데이터(제주도 부분) | [AI Hub](https://www.aihub.or.kr/aihubdata/data/view.do?dataSetSn=71558) | 50대 이상 어르신 방언 보강 |

- AI Hub 원자료와 가공 결과(`processed/`)는 AI Hub 이용 조건에 따라 저장소에 넣지 않음.
- 제출 ZIP에는 심사와 센터 운영용으로 가공 결과만 포함됨.

전처리 결과:

- 방언 사전 59,473 낱말 (어르신 데이터에서만 나온 낱말 포함)
- 예문 약 264만 문장 (일상대화 2,593,434, 어르신 46,722)
- 평가셋 일상대화 500문장, 어르신 500문장 (학습 데이터와 대화 파일, 화자가 겹치지 않게 분리)

## 사용법

### 설치

```bash
pip install -r requirements.txt
cp .env.example .env      # ANTHROPIC_API_KEY 입력. ALLOW_PAID_CALLS는 사용할 때 직접 설정
```

- Windows에서는 `cp` 대신 `copy` 사용.

### 사전과 색인 만들기 (처음 한 번)

- AI Hub에서 라벨링 데이터만 받아 이 폴더에 풀어 둠.
- 일상대화 라벨은 `label/`, 중노년층 제주 라벨은 `label_senior/`에 둠.

```bash
python preprocess.py               # processed/ 만들기
python normalize.py --build-index  # processed/example_index.pkl 만들기 (1~3분)
```

### 변환과 구조화

```bash
python normalize.py "고냉이 허고 강생이들 왕 막 파불민 농사도 잘 안 돼요"
python normalize.py --zero-shot "..."    # 비교용: 사전과 예문 없이 LLM만
python normalize.py --json "..."         # JSON 출력
python structure.py "우리 동네 버스가 확 줄어부난 할망덜 병원 가기가 막 힘들어마씸 구좌읍"
python structure.py --out q.json "..."   # 엔진에 넣을 질의 저장
```

다른 코드에서 쓸 때:

```python
from normalize import normalize
result = normalize("고냉이 허고 강생이들 왕 막 파불민")
# result["standard"], result["changes"], result["ask_user"], result["warnings"]
```

### 평가

```bash
python evaluate.py --limit 20     # 평가셋마다 20문장 (비용과 오류 먼저 확인)
python evaluate.py                # 평가셋 2개 × 방법 2개 전체
python evaluate.py --report       # 호출 없이 저장된 결과로 점수만 다시 계산
```

- 결과는 `results/<모델>/`에 저장됨.
- 중간에 끊겨도 다시 실행하면 이어서 진행함.
- AI Hub 문장이 들어간 예측 파일은 저장소에 올리지 않음. 점수 요약(`summary.json`)만 올림.

현재 결과 (Claude Haiku 5.5, 사전과 예문을 함께 쓴 방식, 평가셋마다 20문장):

| 평가셋 | ERR | 재현율 | FPR | CER(공백 포함) | BLEU | 문장당 질문 |
| --- | --- | --- | --- | --- | --- | --- |
| 일상대화 | 45.1 | 54.9 | 4.5 | 12.2 | 71.5 | 0.45 |
| 어르신 | 72.8 | 77.2 | 2.3 | 7.9 | 77.4 | 0.30 |

- 20문장 표본이라 오차가 큼.
- 비교 기준: LLM만 쓴 방식은 이전 버전 평가에서 문장당 질문 2.0~3.1개(사전과 예문을 함께 쓰면 0.45~0.65개).

## 전처리에서 고친 데이터 문제

- `{laughing}` 같은 소리 표시는 지움.
- 가명 표시(`@이름1`, `&name1&`, `#이름#`)는 `○○`로 통일함.
- 알아들을 수 없는 구간 `(( ))`이 있는 문장은 제외함.
- 이중 전사 표기 `(방언)/(표준어)`가 남은 경우 방언 쪽은 앞을, 표준어 쪽은 뒤를 남기고 `#` 기호를 지움.
- 중노년층 데이터에서 방언 어절만 `#이름#`으로 가려지고 표준어는 남은 비식별화 오류가 있었음.
  - 어절 단위로 표준어 쪽도 함께 가려 문장을 살림.
  - 제외 비율이 25%에서 1.3%로 줄어듦.

## 평가 지표

### 원칙

- 무엇을 재는지(지표 이름과 계산식)는 선행 연구의 정의를 그대로 따름.
- 우리 데이터에서 어떻게 세는지는 아래 "적용 규칙"에 모두 공개함.

### 어절 단위 지표

- 선행 연구의 어휘 정규화 평가(MultiLexNorm)처럼 입력 어절 하나를 네 칸 중 하나로 분류함.

| 분류 | 정의 (van der Goot et al., 2021) |
| --- | --- |
| TP | 작업자가 바꾼 어절을 시스템도 올바른 표준형으로 바꾼 경우 |
| FN | 작업자가 바꾼 어절을 시스템이 올바르게 바꾸지 못한 경우 (틀린 표준형으로 바꾼 경우 포함) |
| FP | 작업자가 바꾸지 않은 어절을 시스템이 바꾼 경우 |
| TN | 작업자도 시스템도 바꾸지 않은 어절 |

- "작업자가 바꾼 어절": AI Hub 라벨의 `eojeolList`에서 `isDialect = true`이고 표준형이 다른 어절.

| 지표 | 계산식 | 뜻 | 근거 |
| --- | --- | --- | --- |
| ERR (메인) | (TP − FP) / (TP + FN) | 바꿔야 할 어절 대비, 맞게 바꾼 수에서 잘못 바꾼 수를 뺀 비율. 아무것도 안 바꾸면 0이고 음수도 가능 | van der Goot (2019), MultiLexNorm (2021) 공식 지표 |
| 재현율 | TP / (TP + FN) | 바꿔야 할 방언 어절 중 맞게 바꾼 비율 | 분류 평가 표준 정의. W-NUT 2015는 토큰 단위 정밀도, 재현율, F1로 평가 |
| FPR | FP / (FP + TN) | 원래 표준어인 어절을 잘못 바꾼 비율 (낮을수록 좋음) | 분류 평가 표준 정의. 과잉 수정을 따로 보이려고 추가 |
| 단어 정확도 | (TP + TN) / 전체 어절 | 전체 어절 중 올바른 상태인 비율 | Kuparinen et al. (2023) |
| LAI 정확도 | (FP + TN) / 전체 어절 | 아무것도 안 바꿨을 때의 단어 정확도 (기준선) | Kuparinen et al. (2023), MultiLexNorm (2021) |

- 단어 정확도만 보면 원래 표준어인 어절이 많은 데이터에서 점수가 높게 나옴.
- 따라서 바꿔야 할 어절 수로 보정한 ERR을 메인으로 쓰고 LAI 정확도를 함께 적음.

### 문장 단위 지표

| 지표 | 계산식 | 근거 |
| --- | --- | --- |
| CER (공백 포함) | 글자 편집 거리 합 / 정답 문장 글자 수 합 (연속 공백은 하나로) | Kuparinen et al. (2023) |
| CER (공백 제외) | 모든 공백을 지우고 같은 방식으로 계산. 띄어쓰기 차이의 영향을 따로 보려고 함께 보고 | 위의 적용 변형 |
| BLEU | sacrebleu `corpus_bleu` 기본 설정 (13a 토크나이저, 어절 단위) | 한국어 방언 번역 연구(임상범 외, 2021, 2022) |
| chrF | sacrebleu `corpus_chrf` 기본 설정 | 기계번역 평가 표준 지표 |
| 문장 완전 일치율 | 띄어쓰기와 문장부호를 지운 뒤 정답 문장과 같은 비율 | 보조 지표 |

- 시스템 동작 지표로 문장당 추정 변경 수(사전 근거 없는 변경)와 질문 수도 함께 보고함.

### 적용 규칙

| 항목 | 규칙 | 이유 |
| --- | --- | --- |
| 단위 | AI Hub 라벨의 어절 | 정답 라벨이 어절 단위로 제공됨 |
| 정답 라벨 연결 | 입력 문장을 띄어쓰기로 나눈 어절과 `eojeolList`의 방언 어절을 앞에서부터 순서대로 맞춤. 못 맞춘 정답 쌍 수는 "미매칭"으로 함께 출력 | 문장과 어절 목록의 표기가 드물게 다름 |
| 입력과 출력 정렬 | 입력과 출력 어절을 편집 거리(difflib)로 정렬한 뒤 분류 | 선행 연구는 1:1로 짝지어진 데이터를 쓰지만 LLM 출력은 짝이 없음 |
| 바뀐 구간 | 어절 수가 같으면 1:1로, 다르면 구간 전체를 붙여 비교. 예: `죽어부난 → 죽어 버려서` | 한 어절이 여러 어절로 바뀌는 경우가 흔함 |
| 비교 방식 | 띄어쓰기와 문장부호는 무시. 정답 표준형이 출력 구간 안에 있으면 TP, 원래 어절이 남아 있으면 TN | `고양이 하고`와 `고양이하고` 같은 차이는 오류로 보지 않음 |
| 조사가 붙은 경우 | `냄새 → 냄새가`처럼 원래 어절이 그대로 들어 있으면 TN | 뜻이 바뀌지 않은 변화. 다만 일부 변경이 TN으로 관대하게 세어질 수 있음 |
| 새로 생긴 어절 | 어절 단위 지표에서는 세지 않고 CER에 반영 | 네 칸 분류는 입력 어절 기준임 |
| 가명 어절 `○○` | 제외 | 원래 낱말을 알 수 없음 |
| 틀린 표준형 | FN으로만 셈 (FP 아님) | MultiLexNorm 정의와 같음 |

### 평가의 한계

- ERR은 FP와 FN을 구분하지 못함.
  - 정답 데이터가 방언 표시를 빠뜨린 어절을 시스템이 올바르게 바꿔도 FP로 감점됨.
- 정답 표준형과 뜻은 같지만 표현이 다르면(`먹여서`, `먹이고`) FN으로 셈. 실제 품질보다 낮게 나올 수 있음.
- 편집 거리 정렬은 근사라서 출력이 입력과 크게 다르면 분류가 부정확할 수 있음.
- 표본이 작으면 오차 범위가 큼. 최종 수치는 평가셋 전체(500문장씩)로 내야 함.

### 참고문헌

- Rob van der Goot. 2019. *Normalization and Parsing Algorithms for Uncertain Input.* PhD thesis, University of Groningen. (5장: 정규화 평가와 ERR)
- Rob van der Goot, Alan Ramponi, Arkaitz Zubiaga, Barbara Plank, Benjamin Muller, Iñaki San Vicente Roncal, Nikola Ljubešić, Özlem Çetinoğlu, et al. 2021. MultiLexNorm: A Shared Task on Multilingual Lexical Normalization. *Proceedings of the Seventh Workshop on Noisy User-generated Text (W-NUT 2021)*, pp. 493–509. https://aclanthology.org/2021.wnut-1.55
- Timothy Baldwin, Marie Catherine de Marneffe, Bo Han, Young-Bum Kim, Alan Ritter, Wei Xu. 2015. Shared Tasks of the 2015 Workshop on Noisy User-generated Text: Twitter Lexical Normalization and Named Entity Recognition. *Proceedings of the ACL 2015 Workshop on Noisy User-generated Text*, pp. 126–135.
- Olli Kuparinen, Aleksandra Miletić, Yves Scherrer. 2023. Dialect-to-Standard Normalization: A Large-Scale Multilingual Evaluation. *Findings of EMNLP 2023*, pp. 13814–13828. https://aclanthology.org/2023.findings-emnlp.923
- 임상범, 박찬준, 조재춘, 양영욱. 2021. 딥러닝 기반 한국어 방언 기계번역 연구. *제33회 한글 및 한국어 정보처리 학술대회 논문집.*
- 임상범, 박찬준, 양영욱. 2022. 언어적 특성과 서비스를 고려한 딥러닝 기반 한국어 방언 기계번역 연구.
- Matt Post. 2018. A Call for Clarity in Reporting BLEU Scores. *WMT 2018.* (sacrebleu)

## 알려진 한계

- 일상대화 자료로 만든 사전이라 배수로, 가로등 같은 민원 어휘는 적을 수 있음.
- 정답 데이터에도 작업자 오타나 어절 경계 어긋남이 일부 있음. 최소 등장 횟수 조건으로 대부분 걸러짐.
- 방언 비율 기준(10%, 50%)은 평가 전에 정한 값이며, 평가 결과를 본 뒤 조정하지 않았음.

## 변경 이력

- v3.5: 표준어로도 자주 쓰는 낱말은 되묻지 않기, 같은 질문 묶기
- v3.4: 개인정보 가리기를 근거 엔진 `privacy.py`로 통일, 읍면동 이름 뒤에 다른 낱말이 붙으면 지명에서 제외, 사전이 없을 때 `ResourceMissing` 예외, 키가 없으면 `LLMError`, `.env.example`의 `ALLOW_PAID_CALLS=1` 주석 처리
- v3.3: 평가 지표를 선행 연구 정의로 교체 (ERR, 재현율, FPR, 단어 정확도, LAI 정확도, CER, BLEU)
- v3.2: 일상대화 데이터의 이중 전사 표기와 `#` 기호 정리, 문장에 읍면동이 있으면 확인 질문 생략
- v3.1: 문장 속 읍면동 우선, 말하지 않은 요구사항은 확인 질문으로, claims에서 장소명 제외
- v3.0: OpenAI에서 Claude API로 전환(`llm.py`), 구조화 모듈 추가, pytest 추가, 의존성 버전 고정
- v2.3: 방언 비율 추가, 가짜 변경 걸러내기, 질문 규칙 강화, `evaluate.py` 추가
- v2.2: 일상대화와 중노년층 데이터 통합, 비식별화 오류 처리
