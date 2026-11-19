# 화면과 민원서 작성 (`complaint_draft/`)

도민이 쓰는 Streamlit 화면, 단계별 대화 흐름, 근거 패키지를 표준어 민원서로 만드는 부분.

## 실행

저장소 맨 위에서 실행.

```bash
streamlit run complaint_draft/app.py           # http://localhost:8501
python -m pytest -q complaint_draft/tests      # 테스트 152개, 네트워크와 유료 호출 없이
```

| 주소 | 화면 |
| --- | --- |
| `http://localhost:8501` | 기본 화면. 불편 쓰기부터 민원서까지 한 흐름으로 이어짐 |
| `http://localhost:8501/?mode=dev` | 기본 화면에 엔진 알림과 `handoff.json` 받기 단추를 추가로 표시 |
| `http://localhost:8501/?mode=file` | 예시 3개나 `handoff.json` 파일로 초안만 보는 시연용 화면 |

- Claude를 쓰려면 실행하는 셸에서 `ALLOW_PAID_CALLS=1` 설정
- 설정이 없으면 규칙 기반으로 이해하고 기본 문장 틀로 민원서 생성
- 근거 DB가 없으면 엔진이 형식 확인 모드로 동작하고 복사와 인쇄를 막음

## 화면 구성

단계 표시는 세 묶음으로 구성.

| 묶음 | 화면 |
| --- | --- |
| 1. 불편 쓰기 | 글 칸, 지역 고르기(시와 읍면동으로 묶은 선택창, 제주 전체), 주제 고르기(여러 개, 건너뛰기 가능) |
| 2. 내용 확인 | 되묻기(질문 하나씩) → 뜻 고르기(주장마다 체크, 최대 3개) → 최종 확인 |
| 3. 민원서 만들기 | 근거 고르기(판정, 값, 비교, 출처, 기준일 카드) → 민원서 초안 |

- 머리: 로고를 누르면 처음 화면으로 이동. 오른쪽에서 글자 크기(작게, 보통, 크게) 변경. 바꾼 크기는 초안 화면에도 적용됨
- 고른 지역은 태그로 표시. 마우스를 올리면 오른쪽 위에 나오는 작은 x로 삭제 가능
- 초안 화면은 넓게 펼쳐짐. 왼쪽에 민원서, 오른쪽에 글자 수와 복사, 인쇄, 텍스트 받기 단추 배치
- 초안 위의 "수정하기"를 누르면 제목, 내용, 요청 사항 수정 가능. 저장, 취소, 처음 초안으로 되돌리기 제공. 근거 자료는 잠금
- "근거에서 뺀 내용"은 접힌 상태. 펼치면 뺀 근거와 이유 표시
- 사용자 글은 마크다운이나 HTML로 해석되지 않게 변환해서 넣음. 초안 화면(`view.html`)은 글을 JSON으로만 받아 `textContent`로 그림
- 색과 글자, 감귤 캐릭터는 `style.py`, 전체 테마는 저장소 맨 위 `.streamlit/config.toml`에 있음
- 글꼴(Jua, Noto Sans KR)은 Google Fonts에서 받음. 인터넷이 없으면 기본 글꼴로 표시

## 대화 흐름 (`flow.py`)

- 화면과 터미널이 함께 쓰는 흐름
- Streamlit을 쓰지 않는 함수라서 다른 화면에도 연결 가능
- 상태는 dict 하나라서 `st.session_state`에 그대로 넣음

| 단계 (`state["stage"]`) | 보여 줄 것 | 답 넣기 |
| --- | --- | --- |
| `input` | 글 칸, 지역(`agent.place_choices()`), 주제(`structure.topic_names()`) | `start(agent, text, places=[...], topics=[...])` |
| `questions` | `question_view(state)` 질문 하나 | `answer_question(agent, state, text=..., yes=..., places=[...])` |
| `meanings` | `meaning_view(state)` 주장별 뜻, `max_picks` | `submit_meanings(agent, state, {"c1": ["c1-o1", "c1-o3"]})` |
| `confirm` | `confirm_rows(state)` | `confirm(agent, state, yes)` |
| `evidence` | `state["evidence_items"]` 근거 카드 | `choose_evidence(agent, state, [index, ...])` |
| `draft` | `state["draft"]`, `state["handoff"]` | `back_to_evidence(state)`로 근거 다시 고르기 |
| `stopped` | `state["stop_reason"]` | 처음부터 다시 |

- 주제를 골랐으면 "더 자세히 말해 주세요" 질문 대신 고른 주제로 채움. 그래도 불편한 점을 찾지 못하면 그때 질문
- 제주어 뜻 질문은 낱말만 다르고 묻는 말이 같으면 한 문장으로 묶음
- 최종 확인에서 "아니요"를 누르면 고른 지역과 주제는 유지하고 처음 이해부터 다시 진행. 두 번째 "아니요"면 중단
- 근거 고르기에서는 넣을 수 있는 근거(자료로 확인됨이나 일부만 확인됨이고 값이 있는 근거)만 체크 가능. 처음에는 모두 체크된 상태

## 민원서 만들기 (`draft.py`, `writer.py`)

`build_draft(handoff, source_text=None, exclude=None, write=None)`가 초안 생성.

1. `writer.write_sections`가 Claude에 제목, 민원 내용, 요청 사항을 한 번에 요청. 넘기는 것은 가린 사용자 글, 지역, 고른 주장, 근거 이름이며 숫자는 넘기지 않음
2. 받은 글을 코드가 검사
   - 숫자는 사용자 글, 고른 주장, 지역 이름에 있는 것만 허용
   - 빈 칸 없음
   - 제목에 장소 포함, 45자 이하
   - 개인정보 재가림
3. 검사를 통과하지 못하거나 Claude를 쓸 수 없으면 `templates.py`의 기본 문장 틀로 생성. 결과에 `written_by: "template"`과 안내 문구를 남김
4. 근거 자료 칸은 엔진의 값과 출처를 그대로 옮김. 표시만 소수점 둘째 자리까지 줄임

| 항목 | 처리 |
| --- | --- |
| 문체 | 표준어 합니다체. 기본 문장 틀일 때는 `formal.py`가 문장 끝만 규칙으로 변환(힘들어요 → 힘듭니다). 애매한 끝은 그대로 두고 경고 |
| 근거 자료 | 자료로 확인됨, 일부만 확인됨만 넣음. 일부만 확인된 근거는 그렇게 표시 |
| 뺀 근거 | 자료와 다름, 자료로 확인 불가, 빈칸, 사용자가 체크를 푼 근거는 `excluded`로 돌려 화면에서 안내 |
| 오래된 자료 | 기준일이 2년을 넘으면 "오래된 자료"로 표시 |
| 남은 제주어 | 표준어 글에 남은 제주어 낱말을 사전으로 찾아 밑줄로 표시하고 경고 |
| 개인정보 | `evidence_engine/package/privacy.py`로 한 번 더 가림 (`○○`) |
| 형식 확인 모드 | 경고를 띄우고 복사와 텍스트 받기를 막음 (`copy_allowed: false`) |

## 제출용 글 (`submission`)

- 국민신문고와 제주도 누리집 "도지사에게 바란다"의 글 칸은 제목과 내용 두 개
- 따라서 제출용 글도 두 덩어리로 생성
- 이름, 연락처, 주소, 처리기관은 창구 화면에서 본인이 입력하고 선택

| 칸 | 만드는 법 |
| --- | --- |
| 제목 | "장소 + 핵심 불편 + 요청" 한 줄. 예: `구좌읍 버스 증차 요청` |
| 내용 | `[발생 장소]` → `[불편 사항]` → `[요청 사항]` → `[근거 자료]` 순서. 넣은 근거가 없으면 `[근거 자료]` 생략 |
| 처리기관 참고 | 읍면동으로 제주시와 서귀포시를 찾아 안내. 선택은 사용자 몫 |

국민신문고 글자 수 제한(제목 200자, 내용 40,000자)을 화면에 표시하고, 넘으면 경고.

## 출력 예시

```json
{
  "request_id": "...",
  "written_by": "claude",
  "sections": [
    {"id": "title",    "title": "제목",      "standard": "..."},
    {"id": "content",  "title": "민원 내용", "standard": "..."},
    {"id": "request",  "title": "요청 사항", "standard": "..."},
    {"id": "evidence", "title": "근거 자료", "standard": "..."}
  ],
  "submission": {"title": "...", "body": "[발생 장소]\n...", "title_chars": 12, "body_chars": 551,
                 "title_limit": 200, "body_limit": 40000, "agency": "제주특별자치도 제주시"},
  "evidence": [{"claim_id": "c1", "verdict": "partial", "indicator": "...", "value": -11.32, "unit": "%",
                "sources": [], "as_of": "...", "stale": false}],
  "excluded": [{"kind": "evidence", "claim_id": "c2", "reason": "자료와 다름"}],
  "warnings": [], "notices": [], "copy_allowed": true, "dialect_words": []
}
```

## 파일

| 파일 | 내용 |
| --- | --- |
| `app.py` | Streamlit 화면 |
| `flow.py` | 대화 흐름 |
| `draft.py` | `build_draft`, `apply_edits`, `plain_text`, `validate_draft` |
| `writer.py` | Claude 민원서 작성과 결과 검사 (프롬프트 `SYSTEM`) |
| `templates.py` | 기본 문장 틀, 판정 표시, 경고 문구 |
| `formal.py` | 합니다체 변환 |
| `view.py`, `view.html` | 초안 화면 (인쇄, 복사, 텍스트 받기) |
| `style.py` | 색, 글자, 감귤 캐릭터, 단계 표시, 카드 |
| `samples/*.json` | 시연용 예시 3개 (수치와 기준일은 실제 계산값이 아님) |
