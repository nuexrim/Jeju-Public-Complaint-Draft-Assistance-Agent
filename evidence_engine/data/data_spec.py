"""데이터 활용 명세 만들기: 데이터명, 제공기관, 출처, 기준일, 이용조건, 활용 방법, 오픈소스 (대회 제출용).

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
        "external_api": "공공데이터포털 Open API", "external_file": "공공데이터포털 파일(외부)"}
HUB_TERMS = "제주데이터허브 이용약관. 대회 안내에 따라 제출 ZIP에서 심사와 센터 운영에만 쓰고 외부에 재배포하지 않음"
TERMS = {"hub_file": HUB_TERMS, "hub_api": HUB_TERMS,
         "datagokr_file": "공공데이터포털에 표기된 데이터별 이용허락범위 (허브 등록 데이터의 원본 파일)",
         "external_api": "공공데이터포털 표기 이용허락범위 제한 없음 (2026-11-14 확인)",
         "external_file": "공공데이터포털 표기 이용허락범위 제한 없음 (2026-11-14 확인)"}

# DB 표가 아닌 출처(실행에 쓰이거나 개발에만 쓰임): (데이터명, 제공기관, 출처, 기준일, 이용조건, 활용 방법)
OTHER_SOURCES = [
    (BOUNDARY_SOURCE["dataset"], BOUNDARY_SOURCE["publisher"], BOUNDARY_SOURCE["url"], BOUNDARY_SOURCE["as_of"],
     "CC BY 4.0 (원자료 공공누리 제1유형)", "좌표가 있는 표의 행정동 찾기(공간 결합), 거리 지표"),
    ("국가데이터처_법정동 연계정보", "국가데이터처(공공데이터포털)", "https://www.data.go.kr/data/15136368/fileData.do",
     "2026-10-01", "공공누리 제3유형 (출처표시, 변경금지)",
     "법정동과 리 이름을 행정동으로 바꾸기(예: 종달리 → 구좌읍), 인구 분모 계산"),
    ("한국어 방언 발화 데이터(제주도), 중·노년층 한국어 방언 데이터(제주도 부분)", "AI Hub(한국지능정보사회진흥원)",
     "https://aihub.or.kr", "-", "AI Hub 이용 약관 (재배포 금지)",
     "제주어 사전과 예문 색인 만들기(dialect_normalizer/preprocess.py). 저장소에 넣지 않고 가공 결과만 제출 ZIP에 포함"),
    ("제주빅데이터센터 데이터 명세서", "제주빅데이터센터(대회 제공)", "https://jejudatahub.net/center/data", "-",
     "대회 안내 (외부 공개 금지)", "데이터셋 카드(카탈로그 검색) 만들기. 원본 파일은 저장소와 ZIP에 넣지 않음"),
    ("제주데이터허브 데이터 목록(카탈로그)", "제주데이터허브", "https://www.jejudatahub.net", "2026-11-11",
     "제주데이터허브 이용약관", "데이터셋 카드(카탈로그 검색), 범용 틀이 쓸 데이터 찾기"),
]

# 실행에 쓰는 오픈소스 (이름, 버전, 라이선스, 쓰는 곳). 버전은 requirements.txt 와 같아야 한다(테스트가 확인).
OPEN_SOURCE = [
    ("streamlit", "1.65.0", "Apache-2.0", "화면"),
    ("anthropic", "2.8.0", "Apache-2.0", "Claude API 호출 (공식 SDK)"),
    ("duckdb", "1.1.3", "MIT", "근거 DB, 읽기 전용 SQL, 스냅샷"),
    ("scikit-learn", "1.9.1", "BSD-3-Clause", "제주어 예문 TF-IDF 검색"),
    ("numpy", "2.4.4", "BSD-3-Clause", "수치 계산"),
    ("rdflib", "7.1.4", "BSD-3-Clause", "온톨로지 읽기와 경로 탐색"),
    ("matplotlib", "3.9.2", "PSF 기반 matplotlib 라이선스", "비교 막대그래프"),
    ("jsonschema", "4.23.0", "MIT", "입출력 계약 검증"),
    ("PyYAML", "6.0.2", "MIT", "설정과 지표 등록부 읽기"),
    ("openpyxl", "3.1.5", "MIT", "xlsx 원자료 읽기"),
    ("pyparsing", "3.1.4", "MIT", "rdflib 의존성 고정"),
    ("python-dotenv", "1.2.4", "BSD-3-Clause", ".env 읽기"),
    ("sacrebleu", "2.6.0", "Apache-2.0", "제주어 변환 평가 (BLEU, chrF)"),
    ("pytest", "8.3.4", "MIT", "테스트"),
]
# 개발 단계에만 쓰고 제출본 실행에는 쓰지 않는 것
DEV_ONLY = [
    ("RIGOR", "https://github.com/NadeenAhmad/RIGOR", "MIT", "온톨로지 후보 생성 (직접 매핑만 사용, 코드는 저장소에 넣지 않음)"),
    ("owlready2 + HermiT", "https://owlready2.readthedocs.io", "LGPL", "온톨로지 일관성 검사"),
]
FONTS = [("Jua", "SIL Open Font License 1.1"), ("Noto Sans KR", "SIL Open Font License 1.1")]

FLOW = """\
1. 수집: 제주데이터허브 파일은 누리집 내려받기 주소로, Open API는 프로젝트 키로 수집. 허브 등록 데이터의 원본 파일과 인구 자료는 공공데이터포털에서 수집 (`data/ingest/`)
2. 정리: 파일마다 다른 기간 형식(연, 연월, 연월일, 연과 월이 나뉜 컬럼)과 깨진 CSV를 `datasets.yaml` 설정에 따라 통일
3. 공간 키: 읍면동 이름, 주소 문자열, 법정동과 리 이름(법정동 연계정보), 좌표(행정동 경계 공간 결합) 순서로 행정동 8자리 코드 부여. 서귀포 7개 동은 통반 단위 인구를 합산해 인구 보완
4. 적재: DuckDB 하나에 모으고, 표마다 출처, 기준일, 행 수, 읍면동 연결 비율을 `dataset` 표에 기록. 인구, 면적, 정류소 이용 집계 등 파생 표 생성
5. 카드와 개념: 허브 목록과 센터 명세로 데이터셋 카드를 만들고 BM25 색인과 규칙 개념 태그 부여. 온톨로지로 표와 개념 연결
6. 분석: 주장마다 온톨로지 경로와 카탈로그 검색으로 데이터를 찾고, 등록 지표를 읽기 전용 SQL로 계산해 같은 유형 지역(읍면끼리, 동끼리)과 비교 후 규칙으로 판정
7. 재현: DB 전체를 스냅샷(parquet)으로 고정하고, 설치할 때 해시와 행 수를 대조해 재구성
"""

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
    rows = con.execute("SELECT table_name, dataset, publisher, url, period_end, updated_at, source_kind FROM dataset "
                       "ORDER BY source_kind, dataset, table_name").fetchall()
    kinds: dict[str, set] = {}
    for _t, name, *_rest, kind in rows:
        kinds.setdefault(kind, set()).add(name)
    tables = {r[0] for r in con.execute("SELECT table_name FROM information_schema.tables").fetchall()}
    pf = []
    if "prefetch_profile" in tables:
        pf = con.execute(
            "SELECT title, url, updated_at, bool_or(place_list AND emd_ratio >= 0.7) AS usable, count(*) AS files "
            "FROM prefetch_profile GROUP BY title, url, updated_at ORDER BY title").fetchall()
    judge = sum(1 for i in registry["indicators"] if i.get("role") != "context")
    lines = [
        "# 데이터 활용 명세",
        "",
        f"제주 민원 도우미가 쓰는 데이터와 오픈소스 목록. `python evidence_engine/data/data_spec.py`로 근거 DB와 "
        f"지표 등록부에서 자동 생성하므로 엔진이 실제로 쓰는 자료와 일치함 (생성일 {date.today().isoformat()})",
        "",
        "원자료는 GitHub 저장소에 넣지 않음. 제출 ZIP에는 근거 DB 스냅샷(가공한 parquet)으로만 포함하며 심사와 "
        "제주빅데이터센터 내 운영에만 사용함.",
        "",
        "## 1. 요약",
        "",
        "| 구분 | 수 |",
        "| --- | --- |",
        f"| 제주데이터허브 파일 (필수 데이터) | {len(kinds.get('hub_file', ()))}종 |",
        f"| 제주데이터허브 Open API (필수 데이터) | {len(kinds.get('hub_api', ()))}종 |",
        f"| 공공데이터포털 파일 (허브 등록 데이터의 원본) | {len(kinds.get('datagokr_file', ()))}종 |",
        f"| 외부 공개 데이터 (행정안전부 인구) | {len(kinds.get('external_api', set()) | kinds.get('external_file', set()))}종 |",
        f"| 미리 받은 제주데이터허브 표 (범용 틀용) | {len(pf)}종 |",
        f"| 등록 지표 | 판정 {judge}개, 맥락 {len(registry['indicators']) - judge}개 |",
        "",
        "## 2. 근거 계산에 쓰는 데이터",
        "",
        "| 데이터명 | 제공기관 | 출처 | 기준일 | 받은 방식 | 이용조건 | 활용 방법 (쓰는 지표) | DB 표 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for table, name, pub, url, pe, upd, kind in rows:
        use = ", ".join(dict.fromkeys(uses.get(table, []))) or "참고용 적재 (지표에서는 아직 쓰지 않음)"
        lines.append(f"| {_cell(name)} | {_cell(pub)} | {_cell(url)} | {_cell(pe or upd)} | {_cell(KIND.get(kind, kind))} "
                     f"| {_cell(TERMS.get(kind))} | {_cell(use)} | `{table}` |")
    lines += ["", "## 3. 그 밖의 출처", "",
              "| 데이터명 | 제공기관 | 출처 | 기준일 | 이용조건 | 활용 방법 |", "| --- | --- | --- | --- | --- | --- |"]
    lines += [f"| {' | '.join(_cell(x) for x in row)} |" for row in OTHER_SOURCES]
    lines += ["", "행정동 경계 출처 표기: 통계청 통계지리정보서비스(SGIS, https://sgis.kostat.go.kr)에서 공공누리 제1유형으로 "
              "개방한 행정동 경계를 vuski/admdongkor(https://github.com/vuski/admdongkor)가 가공해 CC BY 4.0으로 배포한 자료."]
    if pf:
        lines += ["", f"## 4. 미리 받은 제주데이터허브 표 ({len(pf)}종)", "",
                  "등록 지표가 없는 주장을 위해 10MB 이하 표(csv, xlsx)를 미리 받아 둠. \"범용 틀 후보\"는 시설 목록으로 "
                  "읽혀 인구 대비 개수 집계가 가능한 표(참고용 자동 집계). 나머지는 적재만 하고 판정에 쓰지 않음. "
                  f"이용조건은 모두 {HUB_TERMS}.",
                  "", "| 데이터명 | 출처 | 기준일 | 활용 방법 |", "| --- | --- | --- | --- |"]
        for title, url, upd, usable, files in pf:
            use = "범용 틀 후보 (참고용 자동 집계)" if usable else "적재만 (판정에 쓰지 않음)"
            lines.append(f"| {_cell(title)}{f' (파일 {files}개)' if files > 1 else ''} | {_cell(url)} | {_cell(upd)} | {use} |")
    lines += ["", "## 5. 오픈소스", "", "실행에 쓰는 패키지. 버전은 `requirements.txt`에 고정.", "",
              "| 패키지 | 버전 | 라이선스 | 쓰는 곳 |", "| --- | --- | --- | --- |"]
    lines += [f"| {n} | {v} | {lic} | {use} |" for n, v, lic, use in OPEN_SOURCE]
    lines += ["", "개발 단계에만 쓴 도구. 제출본 실행에는 불필요.", "",
              "| 이름 | 출처 | 라이선스 | 쓰는 곳 |", "| --- | --- | --- | --- |"]
    lines += [f"| {n} | {u} | {lic} | {use} |" for n, u, lic, use in DEV_ONLY]
    lines += ["", "화면 글꼴: " + ", ".join(f"{n} ({lic})" for n, lic in FONTS) + ", Google Fonts에서 수신"]
    lines += ["", "## 6. 전처리와 분석 흐름", "", FLOW.rstrip(),
              "", "자세한 명령과 순서는 [데이터 파이프라인](docs/data-pipeline.md) 참고"]
    lines += ["", "## 7. 외부 서비스와 AI", "",
              "| 서비스 | 쓰는 곳 | 필요한 때 |", "| --- | --- | --- |",
              "| Claude API (Anthropic), 모델 Claude Haiku 5.5 | 제주어 이해, 구조화, 민원서 다듬기 | 실행 중 (없으면 규칙으로 동작) |",
              "| 제주데이터허브 Open API | 데이터 수집 | 데이터를 새로 받을 때만 |",
              "| 공공데이터포털 Open API | 인구 자료 수집 | 데이터를 새로 받을 때만 |",
              "| Google Fonts | 화면 글꼴 | 실행 중 (없으면 기본 글꼴) |",
              "", "개발 과정에서는 Claude Code(Anthropic)를 코드 작성과 검토에, Claude Haiku 5.5를 제주어 변환 평가에 사용. "
              "근거의 숫자와 판정은 LLM이 아닌 지표 함수와 규칙이 생성함."]
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
