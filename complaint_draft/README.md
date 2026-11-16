# 민원서 초안 (`complaint_draft/`)

근거 패키지까지 받은 결과(`handoff`)로 **표준어 민원서(제출용)**와 **제주어 화면(본인 확인용)**을 만들고,
다크·라이트 모드처럼 버튼 하나로 두 화면을 오가게 합니다.

## 실행

저장소 맨 위에서 실행합니다.

```bash
pip install -r complaint_draft/requirements.txt     # streamlit
streamlit run complaint_draft/app.py                 # 브라우저가 열림 (예시 3개 내장)
python -m pytest -q complaint_draft/tests            # 테스트 (네트워크·유료 호출 없음)
```

화면은 두 가지입니다. 옆 칸(사이드바)은 쓰지 않습니다.

- **기본 화면** (`http://localhost:8501`): 불편한 점을 쓰면 되묻기 → 뜻 고르기 → 최종 확인 → 민원서 초안까지 한 화면에서 이어집니다.
  `complaint_agent` 의 `Agent` 를 그대로 부르므로 루트 `requirements.txt` 설치가 필요합니다.
  Claude 를 쓰려면 실행하는 셸에서 `ALLOW_PAID_CALLS=1` 을 켭니다(없으면 규칙 구조화). 근거 DB가 없으면 엔진 mock 으로 돌고 복사가 막힙니다.
- **예시·파일로 보기** (`http://localhost:8501/?mode=file`): 개발·시연용. 예시 3개나 handoff 파일(`python -m complaint_agent --out handoff.json "..."`)로 초안만 봅니다.

## 대화 흐름 (`flow.py`)

`complaint_agent/conversation.py` 의 터미널 흐름과 같은 순서·같은 횟수 제한을, 화면이 한 단계씩 부를 수 있게 나눈 것입니다.
Streamlit 과 무관한 함수라 다른 화면에서도 쓸 수 있습니다.

| 단계(`state["stage"]`) | 보여 줄 것 | 답 넣기 |
| --- | --- | --- |
| `questions` | `question_view(state)` 질문 하나 | `answer_question(agent, state, text=..., yes=...)` |
| `meanings` | `meaning_view(state)` 주장별 선택지 | `submit_meanings(agent, state, picks)` |
| `confirm` | `state["summary"]` | `confirm(agent, state, yes)` |
| `draft` | `state["draft"]`, `state["handoff"]` | — |
| `stopped` | `state["stop_reason"]` | 처음부터 다시 |

제주어 화면의 원문은 처음 쓴 말과 "더 자세히" 질문에 덧붙인 말을 그대로 씁니다(`state["said"]`).

## 입력

`complaint_agent` 의 `Agent.handoff()` 결과 그대로입니다 (`query`, `package`, `structured_by`, `notices`, `pii_masked`).

제주어 원문은 handoff 에 들어 있지 않습니다. 다음 중 하나로 넘깁니다.

- `build_draft(handoff, source_text="...")`
- handoff 에 `"source_text"` 키를 추가 (연결 모듈의 `result["source_text"]`, 이미 가린 원문)
- 화면의 "원래 하신 말씀" 칸에 입력

원문이 없으면 제주어 화면도 표준어로 보여 주고 경고를 띄웁니다.

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
                 "title_chars": 12, "body_chars": 551, "complaint_type": "교통", "agency": "제주특별자치도 제주시",
                 "portals": [], "steps": []},
  "evidence": [{"claim_id": "c1", "verdict": "supported", "indicator": "...", "value": -12.43, "unit": "%",
                "value_text": "-12.43%", "region_compare": "...", "sources": [], "caveats": []}],
  "excluded": [{"kind": "evidence", "claim_id": "c2", "claim": "...", "label": "...", "reason": "자료와 다름"}],
  "warnings": ["..."], "notices": ["..."], "pii_masked": [], "engine": "hybrid", "copy_allowed": true
}
```

## 제출 창구에 맞춘 글 (`submission`)

국민신문고와 제주도 누리집 '도지사에게 바란다'는 글 칸이 **제목**과 **내용** 두 개입니다
(이름·연락처·주소·처리기관은 창구 화면에서 본인이 직접 넣고 고릅니다). 그래서 제출용 글은 두 덩어리로 만듭니다.

| 칸 | 만드는 법 |
| --- | --- |
| 제목 (`submission.title`) | 국민신문고 작성 요령대로 "장소 + 요청" 한 문장. 예: `구좌읍 버스 증차 요청`. 요구사항이 없으면 `노형동 클린하우스·CCTV 관련 불편 개선 요청` |
| 내용 (`submission.body`) | `[발생 장소]` → `[불편 사항]` → `[요청 사항]` → `[근거 자료]` 순서로 한 덩어리 (사실 → 요청 → 근거) |
| 처리기관 참고 (`submission.agency`) | 읍면동으로 제주시·서귀포시를 찾아 `제주특별자치도 제주시`처럼 안내. 추천일 뿐이고 고르는 것은 사용자 |

- 화면의 복사 버튼은 `① 제목 복사하기`, `② 내용 복사하기` 두 개입니다.
- 글자 수 제한은 창구의 공식 수치를 확인하지 못해 강제하지 않고, 글자 수만 보여 줍니다(`title_chars`, `body_chars`). 제목은 60자를 넘으면 줄입니다.
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
| 뺀 근거 | `contradicted`·`unverifiable`·`gaps`는 `excluded`로 돌려 화면에서 안내 (민원 내용에는 말씀이 그대로 남음) |
| mock 엔진 | 경고를 띄우고 복사·글로 받기를 막음 (`copy_allowed: false`) |
| 규칙 구조화 | `structured_by: "rules"`면 내용을 확인하라는 경고 |
| 개인정보 | 원문도 `evidence_engine/package/privacy.py`로 한 번 더 가림 (자리표시 `○○`) |

## 화면

시안(제주 민원 도우미 화면 디자인)을 옮긴 것입니다. 색·글자 크기·감귤 캐릭터는 `style.py`, 초안 화면은 `view.html`, 전체 색은 저장소 맨 위 `.streamlit/config.toml`에 있습니다.

| 단계 | 화면 |
| --- | --- |
| 1. 말하기 | "무엇이 불편하셨나요?" — 글로 쓰기. "말로 하기"는 음성 인식이 아직 없어 "준비 중"으로만 보입니다 |
| 2. 뜻 확인 | 하신 말씀 / 이렇게 이해했어요(장소·문제·요청·민원 종류, 확인이 남은 칸은 "확인 필요") / 질문 카드(되묻기 → 뜻 고르기 → 마지막 확인) |
| 3. 민원서 초안 | 왼쪽 민원서(제목, 1. 민원 내용, 2. 요청 사항, 3. 근거 자료), 오른쪽 근거 카드·알아두세요·인쇄하기·제목/내용 복사하기 |

- 머리: 글자 크기(보통·크게)와 "처음으로". 글자 크기는 초안 화면에도 같이 적용됩니다.
- 초안은 두 버전을 한 페이지에 모두 그려 두고 CSS 클래스만 바꿉니다("표준어" / "방언" 단추).
- 처음은 표준어본. 인쇄·복사는 표준어본에서만 동작하고, 인쇄는 항상 표준어본이 나옵니다.
- "방언"에서는 섹션마다 "하신 말씀 그대로" / "표준어로 표시" 표시가 붙습니다.
- "이런 민원을 도와드려요"에는 근거 엔진이 실제로 확인할 수 있는 유형만 적었습니다.
- 글꼴(Jua, Noto Sans KR)은 Google Fonts에서 받습니다. 인터넷이 없으면 컴퓨터 기본 글꼴로 보입니다.

## 파일

| 파일 | 내용 |
| --- | --- |
| `draft.py` | `build_draft`, `apply_edits`, `plain_text`, `validate_draft` |
| `templates.py` | 문장 틀, 판정 표시, 경고 문구 |
| `formal.py` | 격식체 변환 (`formalize`, `has_informal`) |
| `view.py`, `view.html` | 토글 화면 HTML |
| `flow.py` | 대화 흐름 (되묻기 → 뜻 고르기 → 최종 확인 → 초안) |
| `app.py` | Streamlit 화면 (말로 시작하기 / 예시·파일로 보기) |
| `style.py` | 화면 꾸밈: 색·글자·감귤 캐릭터·단계 표시·카드 |
| `samples/*.json` | 예시 handoff 3개 (수치·기준일은 실제 계산값 아님) |
| `tests/` | pytest 108개 (`test_draft.py` 28, `test_flow.py` 13, `test_formal.py` 67) |
