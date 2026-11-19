"""데이터 활용 명세 만들기: 데이터명 · 제공기관 · 출처 · 기준일 · 활용 방법 (대회 제출용).

    python data/data_spec.py                 # 저장소 맨 위 데이터_활용_명세.md

근거 DB(data/jeju.duckdb)의 dataset·prefetch_profile 표와 지표 등록부(indicators/registry.yaml)에서 만든다.
손으로 쓰지 않으므로 엔진이 실제로 쓰는 자료와 어긋나지 않는다. 원자료 값은 담지 않는다(이름·기관·주소·기준일·쓰임만).
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import duckdb

ENGINE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ENGINE_ROOT))

from indicators.library import load_registry  # noqa: E402
from package.assemble import BOUNDARY_SOURCE, DERIVED_SOURCES  # noqa: E402

DB = ENGINE_ROOT / "data" / "jeju.duckdb"
OUT = ENGINE_ROOT.parent / "데이터_활용_명세.md"
KIND = {"hub_file": "제주데이터허브 파일", "hub_api": "제주데이터허브 Open API", "datagokr_file": "공공데이터포털 파일",
        "external_api": "공공데이터포털 Open API", "external_file": "외부 공개 파일"}

# DB 표가 아닌 출처(실행에 쓰이거나 개발에만 쓰임)
OTHER_SOURCES = [
    (BOUNDARY_SOURCE["dataset"], BOUNDARY_SOURCE["publisher"], BOUNDARY_SOURCE["url"], BOUNDARY_SOURCE["as_of"],
     "좌표가 있는 표의 행정동 찾기(공간 결합), 거리 지표"),
    ("국가데이터처_법정동 연계정보", "국가데이터처(공공데이터포털)", "https://www.data.go.kr/data/15136368/fileData.do", "2026-10-01",
     "법정동·리 이름을 행정동으로 바꾸기(예: 종달리 → 구좌읍), 인구 분모 계산"),
    ("한국어 방언 발화 데이터(제주도), 중·노년층 한국어 방언 데이터(제주도 부분)", "AI Hub(한국지능정보사회진흥원)",
     "https://aihub.or.kr", "-",
     "제주어 사전·예문 색인 생성(dialect_normalizer/preprocess.py). 저장소에 넣지 않음(AI Hub 이용 조건)"),
    ("제주빅데이터센터 데이터 명세서", "제주빅데이터센터(대회 제공)", "-", "-",
     "데이터셋 카드(카탈로그 검색) 생성. 외부 공개 금지 자료라 저장소에 넣지 않음"),
    ("제주데이터허브 데이터 목록(카탈로그)", "제주데이터허브", "https://www.jejudatahub.net", "2026-11-11",
     "데이터셋 카드(카탈로그 검색), 범용 틀이 쓸 데이터 찾기"),
]


def _uses(registry: dict) -> dict[str, list[str]]:
    """DB 표 → 그 표를 쓰는 지표 이름들(파생 표는 원래 표로 풀어서)."""
    out: dict[str, list[str]] = {}
    for ind in registry["indicators"]:
        label = ind["name"] + (" (맥락)" if ind.get("role") == "context" else "")
        for t in ind["tables"]:
            for src in DERIVED_SOURCES.get(t, [t]):
                out.setdefault(src, []).append(label)
    return out


def _cell(text) -> str:
    return str(text if text not in (None, "") else "-").replace("|", "/").replace("\n", " ")


def build(con, registry: dict) -> str:
    uses = _uses(registry)
    lines = [
        "# 데이터 활용 명세",
        "",
        f"만든 날: {date.today().isoformat()} · `python evidence_engine/data/data_spec.py`로 근거 DB와 지표 등록부에서 자동으로 만든다.",
        "",
        "원자료는 이 저장소(GitHub)에 넣지 않는다. 최종 제출 ZIP에는 근거 DB 스냅샷(가공한 parquet)으로 들어가 심사와 "
        "제주빅데이터센터 내 운영에만 쓰인다.",
        "",
        "## 1. 근거 계산에 쓰는 데이터 (근거 DB 적재 표)",
        "",
        "| 데이터명 | 제공기관 | 출처 | 기준일 | 받은 방식 | 활용 방법 (쓰는 지표) | DB 표 |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    rows = con.execute("SELECT table_name, dataset, publisher, url, period_end, updated_at, source_kind FROM dataset "
                       "ORDER BY source_kind, dataset, table_name").fetchall()
    for table, name, pub, url, pe, upd, kind in rows:
        use = ", ".join(dict.fromkeys(uses.get(table, []))) or "참고용 적재(지표에서는 아직 쓰지 않음)"
        lines.append(f"| {_cell(name)} | {_cell(pub)} | {_cell(url)} | {_cell(pe or upd)} | {_cell(KIND.get(kind, kind))} "
                     f"| {_cell(use)} | `{table}` |")
    lines += ["", "## 2. 그 밖의 출처", "", "| 데이터명 | 제공기관 | 출처 | 기준일 | 활용 방법 |", "| --- | --- | --- | --- | --- |"]
    lines += [f"| {' | '.join(_cell(x) for x in row)} |" for row in OTHER_SOURCES]
    tables = {r[0] for r in con.execute("SELECT table_name FROM information_schema.tables").fetchall()}
    if "prefetch_profile" in tables:
        pf = con.execute(
            "SELECT title, url, updated_at, bool_or(place_list AND emd_ratio >= 0.7) AS usable, count(*) AS files "
            "FROM prefetch_profile GROUP BY title, url, updated_at ORDER BY title").fetchall()
        lines += ["", f"## 3. 미리 받은 제주데이터허브 표 ({len(pf)}종)", "",
                  "등록 지표가 없는 주장을 위한 범용 틀(참고용 자동 집계)용으로 10MB 이하 표(csv·xlsx)를 미리 받아 둔 것이다. "
                  "'범용 틀 후보'는 시설 목록으로 읽혀 인구 대비 개수를 셀 수 있는 표, 나머지는 적재만 하고 판정에 쓰지 않는다.",
                  "", "| 데이터명 | 제공기관 | 출처 | 기준일 | 활용 방법 |", "| --- | --- | --- | --- | --- |"]
        for title, url, upd, usable, files in pf:
            use = "범용 틀 후보(참고용 자동 집계)" if usable else "적재만(판정에 쓰지 않음)"
            lines.append(f"| {_cell(title)}{f' (파일 {files}개)' if files > 1 else ''} | 제주데이터허브 | {_cell(url)} "
                         f"| {_cell(upd)} | {use} |")
    return "\n".join(lines) + "\n"


def main() -> int:
    if not DB.exists():
        print(f"근거 DB가 없다: {DB} (build_db.py 또는 snapshot.py --restore 먼저)")
        return 2
    con = duckdb.connect(str(DB), read_only=True)
    try:
        OUT.write_text(build(con, load_registry()), encoding="utf-8")
    finally:
        con.close()
    print(f"저장: {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
