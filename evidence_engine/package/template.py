"""범용 틀: 등록 지표가 없는 주장을, 미리 받은 데이터허브 시설 목록으로 '인구 1만 명당 개수'를 세어 본다.

언제 쓰나 (모두 맞을 때만)
  1) 등록 지표(registry.yaml)로 고를 것이 없다
  2) 주장이 '부족하다·없다·멀다·어렵다'처럼 시설 수·접근에 관한 것이다 (측면 supply·access)
  3) 엔진의 카탈로그 검색 상위 3개 데이터셋 중, 미리 받은 표(data/prefetch.py)가 '시설 목록'이고
     (한 행 = 시설 하나: 주소·좌표로 읍면동을 찾았고 기간이 하나뿐) 읍면동을 찾은 행 비율이
     엔진의 결측 기준(thresholds.max_missing_ratio)을 넘지 않는다
  4) 데이터셋 이름과 주장이 낱말 하나(두 글자 이상)를 함께 쓴다 (검색 점수만으로 엉뚱한 표를 쓰지 않게)

계산과 판정
  값 = 10000 × 그 읍면동의 행 수 / 그 읍면동 인구 (pop_emd). 등록 지표의 시설 수 지표와 같은 식.
  판정 = 같은 유형 지역(읍·면끼리, 동끼리) 안의 순위(rank, 적을수록 나쁨). 등록 지표와 같은 규칙.
  지표로 검수하지 않은 표라 '자료로 확인됨·자료와 다름'은 '일부만 확인됨'으로 낮춘다(약한 자료와 같은 처리).
  사용자에게는 '자동 집계·참고용'으로 보인다(package/labels.py TEMPLATE_MARK).
숫자는 SQL(읽기 전용 실행기)로만 만들고, 표 이름은 진단표의 pf_<숫자>_<숫자> 꼴만 받는다.
"""
from __future__ import annotations

import re

from indicators.library import claim_aspect, comparison_group, describe
from package.labels import INDIRECT, TEMPLATE_MARK, VERDICT_LABEL, downgraded

TEMPLATE_ID = "template:place_per_10k_pop"
TOP_CARDS = 3                        # 빈칸 사유에 보여 주는 검색 상위 데이터셋 수와 같다
_TABLE = re.compile(r"^pf_\d+_\d+$")
_PARTICLE = re.compile(r"(이랑|에서|까지|부터|으로|에는|에도|이나|이가|은|는|이|가|을|를|에|의|도|만|과|와|로|랑)$")
# 데이터셋 이름에 흔히 들어가 관련성을 말해 주지 않는 낱말
_STOP = {"제주", "제주도", "제주시", "서귀포", "서귀포시", "제주특별자치도", "우리", "동네", "마을", "근처", "주변", "지역",
         "현황", "정보", "목록", "데이터", "너무", "정말", "많이", "부족", "부족해요", "부족합니다", "없어요", "없습니다"}


def claim_words(text: str) -> set[str]:
    words = set()
    for w in re.findall(r"[가-힣]{2,}", text):
        w = _PARTICLE.sub("", w)
        if len(w) >= 2 and w not in _STOP:
            words.add(w)
    return words


def load_place_tables(con, tables: set[str], max_missing: float) -> dict[str, dict]:
    """card_id(hub-<id>) → 쓸 수 있는 시설 목록 표 하나 (읍면동 찾은 비율이 가장 높은 것)."""
    if "prefetch_profile" not in tables or "pop_emd" not in tables:
        return {}
    rows = con.execute(
        "SELECT table_name, hub_id, title, url, updated_at, fetched_at, rows, emd_mode, emd_ratio FROM prefetch_profile "
        "WHERE place_list AND error IS NULL AND emd_ratio >= ? ORDER BY emd_ratio DESC, rows DESC",
        [1 - max_missing]).fetchall()
    out: dict[str, dict] = {}
    for t, hub, title, url, upd, fetched, n, mode, ratio in rows:
        if _TABLE.match(t) and t in tables and f"hub-{hub}" not in out:
            out[f"hub-{hub}"] = {"table": t, "title": (title or "").strip(), "url": url, "as_of": upd or (fetched or "")[:10],
                                 "rows": n, "emd_mode": mode, "emd_ratio": ratio}
    return out


def pick(claim_text: str, cards: list, place_tables: dict[str, dict]) -> dict | None:
    """카탈로그 검색 상위 카드 중 범용 틀을 쓸 수 있는 첫 표."""
    if claim_aspect(claim_text) not in ("supply", "access"):
        return None
    words = claim_words(claim_text)
    for c in cards[:TOP_CARDS]:
        info = place_tables.get(c.card_id)
        title = re.sub(r"\s", "", info["title"]) if info else ""
        if info and any(w in title for w in words):          # 띄어쓰기 차이(치매안심센터 / 치매 안심 센터)는 무시
            return info
    return None


MODE_KO = {"address": "주소", "point": "좌표", "name": "읍면동 이름"}
SEOGWIPO_PREFIX = "5013"           # 행정동 코드 앞 4자리: 5011 제주시, 5013 서귀포시


def city_scope(title: str) -> str | None:
    """데이터셋 이름이 한 행정시만 다루면 그 시('제주시'/'서귀포시'). 예: '제주시 관내 종자업 등록 현황'.
    그런 표는 다른 시의 읍면동이 0곳으로 나와 비교가 왜곡되므로 같은 시 안에서만 비교한다."""
    jeju = re.search(r"제주시|제주 시", title) is not None
    sgp = "서귀포" in title
    if jeju and not sgp:
        return "제주시"
    if sgp and not jeju:
        return "서귀포시"
    return None


def _in_city(code: str, city: str | None) -> bool:
    return city is None or code.startswith(SEOGWIPO_PREFIX) == (city == "서귀포시")


def evaluate(info: dict, emd_code: str | None, emd_label: str, runner, emd_names: dict, verdict_params: dict | None,
             claim_text: str) -> dict:
    """근거 한 건 (assemble._claim 의 반환 형태와 같은 키)."""
    from verdict.rules import judge
    sql = (f'SELECT p.emd_code, 10000.0 * count(t.emd_code) / p.total_pop AS value FROM pop_emd p '
           f'LEFT JOIN "{info["table"]}" t ON t.emd_code = p.emd_code WHERE p.total_pop IS NOT NULL '
           f'GROUP BY p.emd_code, p.total_pop')
    values = {code: float(v) for code, v in runner.run(sql).rows if v is not None}
    name = f"인구 1만 명당 '{info['title']}' 수 {TEMPLATE_MARK}"
    caveats = [
        "자동 집계(참고용): 등록 지표가 없어, 미리 받은 데이터허브 표를 '한 행 = 시설·지점 하나'로 보고 인구 대비 "
        f"개수를 셈. 검수한 지표가 아니라 '{VERDICT_LABEL['partial']}'까지만 판정",
        f"읍면동은 {MODE_KO.get(info['emd_mode'], info['emd_mode'])}로 찾음 (전체 {info['rows']}행 중 {info['emd_ratio']:.0%})",
    ]
    if claim_aspect(claim_text) == "access":
        caveats.append(f"{INDIRECT}주장은 거리·이용 어려움에 관한 것인데 이 자료는 시설 수를 잼")
    city = city_scope(info["title"])
    if city:
        caveats.append(f"데이터셋 이름상 {city}만 다루는 자료라 {city} 안에서만 비교")
    value = values.get(emd_code) if emd_code and _in_city(emd_code, city) else None
    region_compare = None
    if value is None:
        caveats.append("읍면동을 특정하지 못했거나 이 읍면동이 자료 범위 밖이라 판정하지 않음")
        verdict = "unverifiable"
    else:
        label, group = comparison_group(emd_code, emd_names)
        group = [c for c in group if _in_city(c, city)]
        if city:
            label = f"{city} {label}"
        peers = [values[c] for c in group if c != emd_code and c in values]
        vals = sorted([value, *peers])
        lower, same = sum(v < value for v in peers), sum(v == value for v in peers)
        region_compare = (f"{label} {len(vals)}곳 중 이곳보다 적은 곳 {lower}곳, 같은 곳 {same}곳 "
                          f"(이곳 {describe(value)}, 가운데 값 {describe(vals[len(vals) // 2])})")
        j = judge("rank", value, peers=peers, worse="low", params=verdict_params)
        caveats.append(f"판정 근거: {j.detail}")
        verdict = j.verdict
        if verdict in ("supported", "contradicted"):
            caveats.append(downgraded(verdict, "참고용 자동 집계라"))
            verdict = "partial"
    return {
        "verdict": verdict,
        "indicator": f"{name} · {emd_label}",
        "value": None if value is None else round(value, 3),
        "unit": "곳/인구 1만 명" if value is not None else None,
        "region_compare": region_compare,
        "sources": [{"dataset": info["title"], "publisher": "제주데이터허브", "as_of": info["as_of"], "url": info["url"]}]
        if value is not None else [],
        "caveats": caveats,
    }
