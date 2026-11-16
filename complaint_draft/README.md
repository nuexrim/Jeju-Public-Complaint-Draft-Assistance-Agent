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

화면 왼쪽에서 시작 방법을 고릅니다.

- **말로 시작하기** (기본): 불편한 점을 쓰면 되묻기 → 뜻 고르기 → 최종 확인 → 민원서 초안까지 한 화면에서 이어집니다.
  `complaint_agent` 의 `Agent` 를 그대로 부르므로 루트 `requirements.txt` 설치가 필요합니다.
  Claude 를 쓰려면 실행하는 셸에서 `ALLOW_PAID_CALLS=1` 을 켭니다(없으면 규칙 구조화). 근거 DB가 없으면 엔진 mock 으로 돌고 복사가 막힙니다.
- **예시·파일로 보기**: 예시 3개나 handoff 파일(`python -m complaint_agent --out handoff.json "..."`)로 초안만 봅니다.

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

## 규칙

| 항목 | 처리 |
| --- | --- |
| 문장 | LLM 없이 `templates.py` 틀에 handoff 필드를 채움. 문장을 바꾸려면 이 파일만 고침 |
| 제주어 화면 | 방언을 새로 만들지 않음. `민원 내용`만 원문 그대로(`original`), 나머지는 표준어(`standard_fallback`) |
| 숫자·출처 | `package.evidence[].value`·`sources`만 옮김. 표시만 소수점 둘째 자리까지 |
| 근거 자료 | `supported`·`partial`만 넣음. `partial`은 "일부만 확인된 근거" 표시 |
| 뺀 근거 | `contradicted`·`unverifiable`·`gaps`는 `excluded`로 돌려 화면에서 안내 (민원 내용에는 말씀이 그대로 남음) |
| mock 엔진 | 경고를 띄우고 복사·글로 받기를 막음 (`copy_allowed: false`) |
| 규칙 구조화 | `structured_by: "rules"`면 내용을 확인하라는 경고 |
| 개인정보 | 원문도 `evidence_engine/package/privacy.py`로 한 번 더 가림 (자리표시 `○○`) |

## 화면

- 두 버전을 한 페이지에 모두 그려 두고 CSS 클래스만 바꿉니다. 전환할 때 보던 섹션이 같은 자리에 남습니다.
- 처음은 표준어. 복사 버튼(제목·내용)은 표준어 화면에서만 동작합니다.
- 제주어 화면에서는 섹션마다 "하신 말씀 그대로" / "표준어로 표시" 표시가 붙습니다.
- 글자 20~22px, 버튼은 전환과 복사(제목·내용)뿐.

## 파일

| 파일 | 내용 |
| --- | --- |
| `draft.py` | `build_draft`, `plain_text`, `validate_draft` |
| `templates.py` | 문장 틀, 판정 표시, 경고 문구 |
| `view.py`, `view.html` | 토글 화면 HTML |
| `flow.py` | 대화 흐름 (되묻기 → 뜻 고르기 → 최종 확인 → 초안) |
| `app.py` | Streamlit 화면 (말로 시작하기 / 예시·파일로 보기) |
| `samples/*.json` | 예시 handoff 3개 (수치·기준일은 실제 계산값 아님) |
| `tests/` | pytest 33개 (`test_draft.py` 20, `test_flow.py` 13) |
