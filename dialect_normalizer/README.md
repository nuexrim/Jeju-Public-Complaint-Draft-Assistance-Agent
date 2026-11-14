# dialect_normalizer — 제주 방언 → 표준어 변환 모듈

민원서 작성 에이전트의 **1단계(이해)** 모듈입니다.
제주 방언 문장을 입력받아 표준어 문장과 **변환 근거**, **사용자에게 확인할 질문**을 돌려줍니다.

## 방식: 사전 + 예문 검색 + LLM (RAG)

LLM이 근거 없이 추측하는 것을 줄이기 위해, 변환을 LLM에게 전부 맡기지 않습니다.

```
① 어절 나누기 → ② 방언 사전 찾기 → ③ 비슷한 예문 찾기 → ④ LLM 변환 → ⑤ 근거 검사
   (Python)        (Python)            (Python, TF-IDF)      (OpenAI)      (Python)
```

- ⑤에서 사전 근거 없이 바뀐 단어는 `추정`으로 표시하고 사용자에게 확인 질문을 만듭니다.
- LLM이 기록하지 않고 바꾼 단어는 경고로 표시합니다.

## 파일 구성

| 파일 | 역할 |
|---|---|
| `preprocess.py` | AI Hub 라벨 JSON → 방언 사전, 예문 DB, 평가셋 생성 |
| `normalize.py` | 변환 모듈 (`normalize(text)` 함수 + 명령줄 실행) |
| `requirements.txt` | 필요한 Python 패키지 |
| `.env.example` | API 키 설정 예시 (복사해서 `.env`로 사용) |

## 사용 데이터

| 데이터 | 출처 | 용도 |
|---|---|---|
| 한국어 방언 발화 데이터(제주도) | [AI Hub](https://aihub.or.kr/aihubdata/data/view.do?currMenu=115&topMenu=100&aihubDataSe=data&dataSetSn=121) | 일상대화 방언-표준어 쌍 |
| 중·노년층 한국어 방언 데이터(제주도 부분) | [AI Hub](https://www.aihub.or.kr/aihubdata/data/view.do?dataSetSn=71558) | 50대 이상 어르신 방언 보강 |

> AI Hub 원본 데이터와 가공 결과(`processed/`)는 저장소에 포함하지 않습니다. 아래 순서대로 직접 내려받아 생성하세요.

**전처리 결과 (v2.2 기준)**
- 방언 사전 59,629개 (어르신 데이터에서만 나온 단어 7,761개 포함)
- 예문 약 264만 문장 (일상대화 2,593,434 + 어르신 46,722)
- 평가셋: 일상대화 500문장, 어르신 500문장 (학습 데이터와 대화 파일·화자가 겹치지 않음)

## 실행 방법

### 1. 설치
```bash
pip3 install -r requirements.txt
cp .env.example .env     # .env를 열어 API 키와 모델 이름 입력
```

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

## 알려진 한계 / 다음 작업

- 사전에 `잘`, `안`, `막`처럼 표준어로 훨씬 자주 쓰이는 단어가 방언 항목으로 섞여 있음
  → 방언 비율(방언으로 쓰인 횟수 ÷ 전체 등장 횟수) 필터 추가 예정
- LLM이 실제로 바꾸지 않은 단어를 바꿨다고 보고하는 경우가 있음 → 결과 문장 대조로 걸러낼 예정
- 문장부호 차이까지 경고로 표시됨 → 무시하도록 수정 예정
- 일상대화 데이터 기반이라 민원 상황 어휘(배수로, 가로등 등)는 적을 수 있음
- `evaluate.py`(A/B 비교 평가) 작성 예정
