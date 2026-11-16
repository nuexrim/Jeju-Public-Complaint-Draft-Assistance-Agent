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

실제 민원으로 보려면 먼저 연결 실행으로 handoff 파일을 만든 뒤 화면에서 올립니다.

```bash
python -m complaint_agent --out handoff.json "우리 동네 버스가 확 줄어부난 ..."
```

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
  "evidence": [{"claim_id": "c1", "verdict": "supported", "indicator": "...", "value": -12.43, "unit": "%",
                "value_text": "-12.43%", "region_compare": "...", "sources": [], "caveats": []}],
  "excluded": [{"kind": "evidence", "claim_id": "c2", "claim": "...", "label": "...", "reason": "자료와 다름"}],
  "warnings": ["..."], "notices": ["..."], "pii_masked": [], "engine": "hybrid", "copy_allowed": true
}
```

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
- 처음은 표준어. 복사 버튼은 표준어 화면에서만 동작합니다.
- 제주어 화면에서는 섹션마다 "하신 말씀 그대로" / "표준어로 표시" 표시가 붙습니다.
- 글자 20~22px, 버튼은 전환·복사 두 개.

## 파일

| 파일 | 내용 |
| --- | --- |
| `draft.py` | `build_draft`, `plain_text`, `validate_draft` |
| `templates.py` | 문장 틀, 판정 표시, 경고 문구 |
| `view.py`, `view.html` | 토글 화면 HTML |
| `app.py` | Streamlit 화면 |
| `samples/*.json` | 예시 handoff 3개 (수치·기준일은 실제 계산값 아님) |
| `tests/test_draft.py` | pytest 16개 |
