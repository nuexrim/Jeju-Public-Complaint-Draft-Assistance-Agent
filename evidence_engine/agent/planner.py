"""LLM 보조 단계: 지표 레지스트리로 풀 수 없는 claim에만 'SQL 초안'을 LLM에게 받는다.

원칙(CLAUDE.md 5장)
- LLM은 SQL 초안·지표 이름·방향(값이 클수록 불리한지)만 낸다. 숫자는 DuckDB 읽기 전용 실행 결과, 판정은 verdict/rules.py.
- 초안 SQL은 check_sql(읽기 전용)을 통과하고 (emd_code, value) 두 컬럼을 돌려줄 때만 쓴다.
- provider=mock 이거나 LLM 호출이 실패하면(키 없음·ALLOW_PAID_CALLS 없음·API 오류) 아무것도 하지 않는다
  → 지표 라이브러리와 규칙만으로 결과를 낸다(비상 모드).
- 결과에는 "LLM이 작성한 SQL" caveat 와 SQL 원문을 남겨 사람이 검토할 수 있게 한다.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

from agent.llm_client import CallBudgetExceeded, LLMCallFailed, LLMClient, PaidCallNotAllowed
from agent.tools import ToolError, Tools

SYSTEM = ("너는 제주 공공데이터 DuckDB에서 민원 주장을 확인할 읽기 전용 SQL을 쓰는 분석가다. "
          "숫자를 지어내지 말고 SQL만 쓴다. 출력은 JSON 하나다.")


@dataclass
class SQLDraft:
    name: str
    unit: str
    worse: str
    sql: str
    values: dict[str, float]


class Planner:
    def __init__(self, client: LLMClient, tools: Tools, max_tables: int = 3):
        self.client, self.tools, self.max_tables = client, tools, max_tables

    @property
    def active(self) -> bool:
        return self.client.provider != "mock"

    def _prompt(self, claim_text: str, tables: list[dict]) -> str:
        return (
            f"[주장] {claim_text}\n\n[쓸 수 있는 테이블]\n{json.dumps(tables, ensure_ascii=False, default=str)}\n\n"
            "[요구]\n- 제주 43개 행정동(emd_code) 각각의 값을 돌려주는 SELECT 한 문장: 컬럼은 정확히 emd_code, value.\n"
            "- 인구 대비가 필요하면 pop_emd(total_pop, pop_65plus) 또는 elderly_pop_emd 를 emd_code로 조인.\n"
            "- 주장이 '부족·감소·멀다'면 값이 작을수록/클수록 불리한지 worse 로 표시(low|high).\n"
            '[출력] {"name": "지표 이름", "unit": "단위", "worse": "low|high", "sql": "SELECT ..."}'
        )

    def sql_fallback(self, claim_text: str, candidate_tables: list[str]) -> SQLDraft | None:
        if not self.active or not candidate_tables:
            return None
        described = []
        for t in candidate_tables[: self.max_tables]:
            try:
                d = self.tools.describe_table(t)
                described.append({"table": t, "dataset": d["dataset"], "columns": [c["name"] for c in d["columns"]]})
            except ToolError:
                continue
        if not described:
            return None
        try:
            r = self.client.complete(self._prompt(claim_text, described), system=SYSTEM, purpose="sql_fallback")
        except (PaidCallNotAllowed, LLMCallFailed, CallBudgetExceeded):
            return None                                   # 비상 모드: LLM 없이 진행
        draft = parse_draft(r.text)
        if not draft:
            return None
        try:
            res = self.tools.run_sql(draft["sql"])
        except ToolError:
            return None
        if [c.lower() for c in res["columns"]] != ["emd_code", "value"]:
            return None
        values = {str(code): float(v) for code, v in res["rows"] if code is not None and v is not None}
        if not values:
            return None
        return SQLDraft(draft["name"], draft["unit"], draft["worse"], draft["sql"], values)


def parse_draft(text: str) -> dict | None:
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return None
    try:
        d = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    if not all(isinstance(d.get(k), str) and d[k].strip() for k in ("name", "unit", "worse", "sql")):
        return None
    if d["worse"] not in ("low", "high"):
        return None
    return d
