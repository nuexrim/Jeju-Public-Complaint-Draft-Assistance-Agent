# 민원서 초안 (`complaint_draft/`)

근거 패키지까지 받은 결과(`handoff`)로 **표준어 민원서(제출용)**를 만듭니다. 화면에는 표준어 민원서만 보여 줍니다.

## 실행

저장소 맨 위에서 실행합니다.

```bash
pip install -r complaint_draft/requirements.txt     # streamlit
streamlit run complaint_draft/app.py                 # 브라우저가 열림 (예시 3개 내장)
python -m pytest -q complaint_draft/tests            # 테스트 (네트워크·유료 호출 없음)
```

화면은 두 가지입니다. 옆 칸(사이드바)은 쓰지 않습니다.

- **기본 화면** (`http://localhost:8501`): 불편 쓰기(지역·주제 고르기) → 되묻기 → 뜻 고르기 → 최종 확인 → 근거 고르기 → 민원서 초안까지 한 화면에서 이어집니다.
  `complaint_agent` 의 `Agent` 를 그대로 부르므로 루트 `requirements.txt` 설치가 필요합니다.
  Claude 를 쓰려면 실행하는 셸에서 `ALLOW_PAID_CALLS=1` 을 켭니다(없으면 규칙 구조화). 근거 DB가 없으면 엔진 mock 으로 돌고 복사가 막힙니다.
- **개발용 정보** (`http://localhost:8501/?mode=dev`): 기본 화면은 어르신용이라 엔진 알림(예: "Claude 구조화를 쓰지 못해…")과 `handoff.json 받기` 단추를 숨깁니다. 주소 끝에 `?mode=dev`를 붙이면 보입니다.
- **예시·파일로 보기** (`http://localhost:8501/?mode=file`): 개발·시연용. 예시 3개나 handoff 파일(`python -m complaint_agent --out handoff.json "..."`)로 초안만 봅니다.

## 대화 흐름 (`flow.py`)

`complaint_agent/conversation.py` 의 터미널 흐름과 같은 순서·같은 횟수 제한을, 화면이 한 단계씩 부를 수 있게 나눈 것입니다.
Streamlit 과 무관한 함수라 다른 화면에서도 쓸 수 있습니다.

| 단계(`state["stage"]`) | 보여 줄 것 | 답 넣기 |
| --- | --- | --- |
| `input` | 글 칸, 지역(`agent.place_choices()`), 주제(`structure.topic_names()`) | `start(agent, text, places=[...], topics=[...])` |
| `questions` | `question_view(state)` 질문 하나 (읍면동 질문에 `choices`가 오면 목록에서 고름) | `answer_question(agent, state, text=..., yes=..., places=[...])` |
| `meanings` | `meaning_view(state)` 주장별 뜻(최대 5개 + 기타), `max_picks` | `submit_meanings(agent, state, {"c1": ["c1-o1", "c1-o3"]})` |
| `confirm` | `confirm_rows(state)` (지역·주제·민원 종류·문제·바라는 점) | `confirm(agent, state, yes)` — 아니요면 되묻기부터 다시 |
| `evidence` | `state["evidence_items"]` 근거 카드 (`usable`, `stale`) | `choose_evidence(agent, state, [index, ...])` |
| `draft` | `state["draft"]`, `state["handoff"]` | `back_to_evidence(state)` 로 근거 다시 고르기 |
| `stopped` | `state["stop_reason"]` | 처음부터 다시 |

- 불편 쓰기에서 고른 읍면동은 `understand()` 바로 다음에 `answer(r, emd_names=[...])` 로 넘깁니다. 고르지 않았으면 엔진이 되묻기에 고르기 목록(`choices`)을 붙여 보냅니다. 고른 장소(`r["places"]`)는 `summary`·`evidence` 에 함께 넘깁니다.
- 고를 수 있는 장소 수는 엔진의 `place_choices()["max_picks"]` 를 그대로 씁니다. "제주 전체"를 고르면 그것 하나만 넘깁니다.
- 고른 주제는 `agent.understand(text, topics=[...])` 로 엔진에 넘깁니다. 엔진은 유형이 비었거나 "기타"면 고른 주제의 유형으로 정하고(경로당 → 복지), 문장에 주제가 없을 때 뜻 선택지를 찾는 힌트로 씁니다. 문장에 주제가 있으면 문장을 따릅니다(고른 주제로 막지 않음).
- 뜻 고르기에서 "기타"만 고르면 직접 쓰기 없이 하신 말씀 그대로 둡니다. 근거 자료가 없는 주제(선택지가 "자료 없음"·"기타"뿐)인 주장은 묻지 않고 넘어갑니다.
- 최종 확인에서 "아니요"를 누르면 쓰신 글을 다시 이해해 되묻기로 돌아갑니다(고른 지역·주제는 그대로). 엔진이 더 물을 것이 없다고 해도 이해한 문장을 한 번 보여 주고 고칠 기회를 줍니다. 두 번째 "아니요"면 멈춥니다.
- 근거 고르기에서는 민원서에 넣을 수 있는 근거(`supported`·`partial`이고 값이 있음)만 체크할 수 있고, 처음에는 모두 체크돼 있습니다. 체크를 푼 근거는 초안의 `excluded` 에 "직접 뺀 근거"로 남습니다.

제주어 화면의 원문은 처음 쓴 말과 "더 자세히" 질문에 덧붙인 말을 그대로 씁니다(`state["said"]`).

## 입력

`complaint_agent` 의 `Agent.handoff()` 결과 그대로입니다 (`query`, `package`, `structured_by`, `notices`, `pii_masked`).

제주어 원문은 handoff 에 들어 있지 않습니다. 다음 중 하나로 넘깁니다.

- `build_draft(handoff, source_text="...")`
- handoff 에 `"source_text"` 키를 추가 (연결 모듈의 `result["source_text"]`, 이미 가린 원문)
- 화면의 "원래 하신 말씀" 칸에 입력

원문은 초안 데이터(`sections[].dialect`)에만 남고 화면에는 보이지 않습니다.

## 출력 (`build_draft`)

```json
{
  "request_id": "...",
  "sections": [
    {"id": "title",    "title": "제목",     "standard": "...", "dialect": "...", "dialect_source": "standard_fallback"},
    {"id": "content",  "title": "민원 내용", "standard": "...", "dialect": "원문 그대로", "dialect_source": "original"},
    {"id": "request",  "title": "요청 사항", "standard": "...", "dialect": "...", "dialect_source": "standard_fallback"},
    {"id": "evidence", "title": "근거 자료", "standard": "...", "dialect": "...", "dialect_source": "standard_fallback"}
  ],
  "submission": {"title": "구좌읍 버스 증차 요청", "body": "[발생 장소]\n...\n\n[불편 사항]\n...\n\n[요청 사항]\n...\n\n[근거 자료]\n...",
                 "title_chars": 12, "body_chars": 551, "title_limit": 200, "body_limit": 40000,
                 "complaint_type": "교통", "agency": "제주특별자치도 제주시",
                 "portals": [], "steps": []},
  "evidence": [{"claim_id": "c1", "verdict": "supported", "indicator": "...", "value": -12.43, "unit": "%",
                "value_text": "-12.43%", "region_compare": "...", "sources": [], "as_of": "2026-09", "stale": false,
                "index": 0, "caveats": []}],
  "excluded": [{"kind": "evidence", "claim_id": "c2", "claim": "...", "label": "...", "reason": "자료와 다름"},
               {"kind": "user", "claim_id": "c1", "claim": "...", "label": "...", "reason": "직접 뺀 근거"}],
  "warnings": ["..."], "notices": ["..."], "pii_masked": [], "engine": "hybrid", "copy_allowed": true,
  "dialect_words": ["할망덜"]
}
```

## 제출 창구에 맞춘 글 (`submission`)

국민신문고와 제주도 누리집 '도지사에게 바란다'는 글 칸이 **제목**과 **내용** 두 개입니다
(이름·연락처·주소·처리기관은 창구 화면에서 본인이 직접 넣고 고릅니다). 그래서 제출용 글은 두 덩어리로 만듭니다.

| 칸 | 만드는 법 |
| --- | --- |
| 제목 (`submission.title`) | 국민신문고 작성 요령대로 "장소 + 요청" 한 문장. 예: `구좌읍 버스 증차 요청`. 요구사항이 없으면 뜻 고르기에서 고른 주장으로 `구좌읍 불편 개선 요청: 어르신 수에 비해 의원이 적다`(여럿이면 `외 n건`), 고른 뜻이 없으면 `노형동 클린하우스·CCTV 관련 불편 개선 요청` |
| 내용 (`submission.body`) | `[발생 장소]` → `[불편 사항]` → `[요청 사항]` → `[근거 자료]` 순서로 한 덩어리 (사실 → 요청 → 근거). 넣은 근거가 없으면 `[근거 자료]` 칸은 빼고, 초안 화면·인쇄에서도 보이지 않음 |
| 처리기관 참고 (`submission.agency`) | 읍면동으로 제주시·서귀포시를 찾아 `제주특별자치도 제주시`처럼 안내. 추천일 뿐이고 고르는 것은 사용자 |

- 화면의 복사 버튼은 `① 제목 복사하기`, `② 내용 복사하기` 두 개입니다.
- 국민신문고 글자 수 제한(제목 200자, 내용 40,000자)을 화면에 "제목 n / 200자 · 내용 칸 n / 40,000자"로 보여 주고, 넘으면 경고합니다. 만든 제목은 읽기 좋게 60자를 넘으면 줄입니다.
- 이름·연락처·주소 같은 개인정보는 민원서에 넣지 않습니다.

## 직접 고치기 (`apply_edits`)

화면의 "민원서 고치기"에서 표준어본의 **제목·민원 내용·요청 사항**을 고칠 수 있습니다.

- 근거 자료는 숫자·출처가 바뀌지 않게 잠겨 있습니다(고친 값이 들어와도 무시).
- 고친 글도 개인정보를 가립니다. 빈 칸으로 두면 원래 글을 씁니다.
- 제주어 화면의 원문("하신 말씀 그대로")은 바뀌지 않습니다.
- 고친 섹션에는 "직접 고친 글" 표시가 붙고, 복사되는 제목·내용에도 바로 반영됩니다. "처음 글로 되돌리기"로 되돌립니다.

## 규칙

| 항목 | 처리 |
| --- | --- |
| 문장 | LLM 없이 `templates.py` 틀에 handoff 필드를 채움. 문장을 바꾸려면 이 파일만 고침 |
| 문체 | 표준어본은 격식체(합니다체). `formal.py`가 문장 끝만 규칙으로 바꿈(힘들어요→힘듭니다, 줄었다→줄었습니다, 주세요→주시기 바랍니다). 애매한 끝(-네요, -거든요, 물음)은 그대로 두고 경고를 띄움. 방언 원문과 직접 고친 글은 바꾸지 않음 |
| 제주어 화면 | 방언을 새로 만들지 않음. `민원 내용`만 원문 그대로(`original`), 나머지는 표준어(`standard_fallback`) |
| 숫자·출처 | `package.evidence[].value`·`sources`만 옮김. 표시만 소수점 둘째 자리까지 |
| 근거 자료 | `supported`·`partial`만 넣음. `partial`은 "일부만 확인된 근거" 표시 |
| 뺀 근거 | `contradicted`·`unverifiable`·`gaps`와 근거 고르기에서 체크를 푼 근거는 `excluded`로 돌려 화면에서 안내 (민원 내용에는 말씀이 그대로 남음) |
| 오래된 자료 | 출처 기준일(`as_of`)이 2년(24개월)보다 오래되면 근거 카드에 "오래된 자료" 표시 (`stale`) |
| 남은 제주어 | 표준어본(제목·민원 내용·요청 사항)에 남은 제주어 낱말을 방언 사전(`dialect_normalizer/processed/dialect_dict.json`, 제주어 비율 0.9 이상·20번 이상 나온 낱말)으로 찾아 경고하고 초안에서 밑줄 표시 (`dialect_words`). 사전이 없으면 건너뜀. 고쳐 쓰면 다시 검사 |
| mock 엔진 | 경고를 띄우고 복사·글로 받기를 막음 (`copy_allowed: false`) |
| 규칙 구조화 | `structured_by: "rules"`면 내용을 확인하라는 경고 |
| 개인정보 | 원문도 `evidence_engine/package/privacy.py`로 한 번 더 가림 (자리표시 `○○`) |

## 화면

시안(제주 민원 도우미 화면 디자인)을 옮긴 것입니다. 색·글자 크기·감귤 캐릭터는 `style.py`, 초안 화면은 `view.html`, 전체 색은 저장소 맨 위 `.streamlit/config.toml`에 있습니다.

위쪽 단계 표시는 큰 틀 세 개(불편 쓰기 / 내용 확인 / 민원서 만들기)로 묶었습니다.

| 큰 틀 | 화면 |
| --- | --- |
| 1. 불편 쓰기 | 글 칸(가로 전체) + 지역 고르기(단추를 누르면 선택창: 시·읍면/동으로 묶은 목록, 제주 전체, 최대 장소 수) + 주제 고르기(여러 개, 건너뛰기 가능) |
| 2. 내용 확인 | 되묻기(제주어 뜻 확인·읍면동·바라는 점 등 질문 하나씩) → 뜻 고르기(말씀마다 체크, 최대 개수 안내) → 최종 확인(쓰신 글 / 이렇게 이해했어요, "아니요 / 맞아요") |
| 3. 민원서 만들기 | 근거 고르기(판정·값·비교·출처·기준일 카드, 오래된 자료 표시) → 민원서 초안(제목·내용 글자 수, 남은 제주어 밑줄, 인쇄·제목/내용 복사) |

- 머리: 글자 크기(작게·보통·크게)와 "처음으로". 글자 크기는 초안 화면에도 같이 적용됩니다.
- 초안 화면은 표준어 민원서만 보여 줍니다. 예전에 있던 "표준어 / 방언" 전환 단추는 뺐습니다(방언 원문은 초안 데이터의 `sections[].dialect`에 그대로 남아 있어 필요하면 다시 쓸 수 있음).
- 인쇄·제목/내용 복사는 근거 DB가 있을 때만 동작합니다(예시·mock 결과는 막음).
- 주제 목록은 근거 엔진이 실제로 자료를 가진 주제(`structure.topic_names()`)입니다.
- 음성 입력("말로 하기")은 이번 제출물에서 뺐습니다.
- 글꼴(Jua, Noto Sans KR)은 Google Fonts에서 받습니다. 인터넷이 없으면 컴퓨터 기본 글꼴로 보입니다.

## 파일

| 파일 | 내용 |
| --- | --- |
| `draft.py` | `build_draft`, `apply_edits`, `plain_text`, `validate_draft` |
| `templates.py` | 문장 틀, 판정 표시, 경고 문구 |
| `formal.py` | 격식체 변환 (`formalize`, `has_informal`) |
| `view.py`, `view.html` | 초안 화면 HTML (표준어 민원서, 인쇄·제목/내용 복사) |
| `flow.py` | 대화 흐름 (불편 쓰기 → 되묻기 → 뜻 고르기 → 최종 확인 → 근거 고르기 → 초안) |
| `app.py` | Streamlit 화면 (기본 화면 / 예시·파일로 보기) |
| `style.py` | 화면 꾸밈: 색·글자·감귤 캐릭터·단계 표시·카드 |
| `samples/*.json` | 예시 handoff 3개 (수치·기준일은 실제 계산값 아님) |
| `tests/` | pytest 108개 (`test_draft.py` 28, `test_flow.py` 13, `test_formal.py` 67) |
