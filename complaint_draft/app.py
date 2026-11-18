"""민원서 작성 도우미 화면 (Streamlit).

    저장소 맨 위에서:  streamlit run complaint_draft/app.py

기본 화면      : 큰 틀 3단계 — 1 불편 쓰기 / 2 내용 확인(되묻기 → 뜻 고르기 → 최종 확인) / 3 민원서 만들기(근거 고르기 → 초안).
               complaint_agent 의 Agent 를 그대로 부른다. 흐름은 complaint_draft/flow.py, 꾸밈은 style.py.
예시·파일로 보기 : 주소 끝에 ?mode=file 을 붙이면 열린다(예: http://localhost:8501/?mode=file). 개발·시연용.
               예시나 handoff JSON(python -m complaint_agent --out handoff.json "...")으로 초안만 본다.
개발용 정보     : 주소 끝에 ?mode=dev 를 붙이면 엔진 알림·handoff.json 받기가 보인다. 기본 화면(어르신용)에서는 숨긴다.
"""
from __future__ import annotations

import json
import sys
import threading
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from complaint_draft import flow, style                                  # noqa: E402
from complaint_draft.draft import apply_edits, build_draft, plain_text   # noqa: E402
from complaint_draft.view import render_html                             # noqa: E402

SAMPLES = {
    "예시 1 · 근거가 있는 민원 (버스·병원)": "supported.json",
    "예시 2 · 일부 근거가 빠진 민원 (원문 없음)": "mixed.json",
    "예시 3 · 근거 DB 없이 만든 결과 (mock)": "mock.json",
}
# structure.topic_names() 를 못 부를 때만 쓰는 주제 목록(근거 엔진 registry.yaml 의 topics 이름과 같음)
TOPIC_FALLBACK = ["버스·대중교통", "병원·약국", "쓰레기·재활용", "교통사고·보행 안전", "재난 대피시설", "방범·CCTV", "경로당"]
STEP_OF = {"input": 0, "questions": 1, "meanings": 1, "confirm": 1, "stopped": 1, "evidence": 2, "draft": 2}
ORDINAL = ["첫째", "둘째", "셋째", "넷째", "다섯째", "여섯째"]
FOOTNOTE = "이 도우미는 민원서 작성만 돕습니다. 접수는 국민신문고나 읍·면·동 주민센터에서 직접 하셔야 합니다."
ALL_NOTE = "제주 전체를 고르면 지역끼리 비교하는 근거는 나오지 않습니다."

DEV = st.query_params.get("mode") == "dev"   # 개발용 정보(엔진 알림, handoff.json)는 ?mode=dev 에서만 보인다

st.set_page_config(page_title="제주 민원 도우미", page_icon="🍊", layout="wide", initial_sidebar_state="collapsed")


def font_size() -> str:
    return st.session_state.get("font_size", "크게")


def restart() -> None:
    st.session_state.flow = flow.new_state()
    st.session_state.pop("flow_error", None)
    for k in ("in_text", "in_places", "in_topics"):
        st.session_state.pop(k, None)


def set_size(size: str) -> None:
    st.session_state.font_size = size


# ---------------------------------------------------------------- 머리: 캐릭터·글자 크기·처음으로
def show_header() -> None:
    with st.container(key="hdr"):
        left, right = st.columns([5, 4], vertical_alignment="center")
        left.html(style.header())
        c0, c1, c2, c3, c4 = right.columns([1.1, 1, 1, 1, 1.4], vertical_alignment="center")
        c0.markdown("글자 크기")
        for col, (size, key) in zip((c1, c2, c3), (("작게", "size_small"), ("보통", "size_normal"), ("크게", "size_big"))):
            col.button(size, key=key, type="primary" if font_size() == size else "secondary",
                       on_click=set_size, args=(size,), use_container_width=True)
        c4.button("처음으로", key="go_home", on_click=restart, use_container_width=True)


# ---------------------------------------------------------------- 초안 (3단계 / 예시 보기 공통)
def edited_draft(base: dict) -> dict:
    """사용자가 고친 내용(민원 request_id 마다 따로 기억)을 반영한 초안."""
    store = st.session_state.setdefault("edits", {})
    return apply_edits(base, store.get(base["request_id"] or "draft"))


def edit_panel(draft: dict) -> None:
    """표준어본을 직접 고치는 칸."""
    rid = draft["request_id"] or "draft"
    store = st.session_state.setdefault("edits", {})
    now = {sec["id"]: sec for sec in draft["sections"]}
    edited = any(sec.get("edited") for sec in draft["sections"])
    with st.expander("고쳐 쓰기" + (" (고친 글 있음)" if edited else ""), expanded=False):
        st.caption("표준어 민원서의 제목·민원 내용·요청 사항을 고칠 수 있어요. 근거 자료는 숫자와 출처가 바뀌지 않게 잠겨 있어요.")
        with st.form(f"f_edit_{rid}"):
            title = st.text_input("제목", value=now["title"]["standard"], key=f"e_title_{rid}")
            content = st.text_area("민원 내용", value=now["content"]["standard"], height=220, key=f"e_content_{rid}")
            request = st.text_area("요청 사항", value=now["request"]["standard"], height=100, key=f"e_request_{rid}")
            st.text_area("근거 자료 (고칠 수 없음)", value=now["evidence"]["standard"], height=140, disabled=True,
                         key=f"e_evidence_{rid}")
            c1, c2 = st.columns(2)
            save = c1.form_submit_button("고친 내용 반영하기", type="primary", use_container_width=True)
            reset = c2.form_submit_button("처음 글로 되돌리기", use_container_width=True)
        if save or reset:
            if save:
                store[rid] = {"title": title, "content": content, "request": request}
            else:
                store.pop(rid, None)
            for k in ("title", "content", "request"):      # 칸을 반영된 글(개인정보 가린 뒤)로 다시 채운다
                st.session_state.pop(f"e_{k}_{rid}", None)
            st.rerun()


def show_draft(base: dict) -> None:
    draft = edited_draft(base)
    for w in draft["warnings"]:
        st.warning(w)
    if DEV:
        for n in draft["notices"]:
            st.caption(f"알림: {n}")
    if draft["pii_masked"]:
        st.caption("개인정보를 ○○로 가렸어요: " + ", ".join(draft["pii_masked"]))

    # 화면 HTML은 사용자 글을 JSON으로만 넣고 textContent 로 그린다(view.html 참고). 높이는 내용에 맞춘다.
    st.iframe(render_html(draft, font_size()), height="content")

    edit_panel(draft)

    sub = draft["submission"]
    with st.expander("제출하는 방법", expanded=False):
        for i, step in enumerate(sub["steps"], 1):
            st.markdown(f"{i}. {step}")
        if sub["agency"]:
            st.markdown(f"**처리기관 고를 때 참고:** {sub['agency']} · 분야: {sub['complaint_type']}")
        st.caption(f"제목 {sub['title_chars']}자 · 내용 {sub['body_chars']}자")
        for portal in sub["portals"]:
            st.markdown(f"- [{portal['name']}]({portal['url']}) — {portal['note']}")

    if draft["excluded"]:
        with st.expander(f"근거에서 뺀 내용 {len(draft['excluded'])}건", expanded=True):
            st.write("자료와 다르거나 자료로 확인할 수 없어 근거 자료에 넣지 않았어요. 민원 내용에는 하신 말씀이 그대로 남아 있어요.")
            for x in draft["excluded"]:
                who = f"'{x['claim']}' — " if x["claim"] else ""
                st.markdown(f"- {who}**{x['reason']}** · {x['label']}")

    if draft["evidence"]:
        with st.expander("근거 자세히 보기 (주의할 점)"):
            for e in draft["evidence"]:
                st.markdown(f"**{e['indicator']}** · {e['verdict_ko']} · {e['value_text']}")
                for c in e["caveats"]:
                    st.markdown(f"  - {c}")

    st.download_button("표준어 민원서 글로 받기 (.txt)", plain_text(draft, "standard"),
                       file_name=f"민원서_{draft['request_id'] or '초안'}.txt", mime="text/plain",
                       disabled=not draft["copy_allowed"])
    st.html(style.footnote(FOOTNOTE))


# ---------------------------------------------------------------- 예시·파일로 보기
def page_file() -> None:
    st.html(style.stepper(2))
    with st.expander("불러올 내용 고르기", expanded=True):
        how = st.radio("어디서 불러올까요?", ["예시", "파일 올리기 (handoff.json)"], horizontal=True)
        handoff = None
        if how == "예시":
            name = st.selectbox("예시", list(SAMPLES))
            handoff = json.loads((Path(__file__).parent / "samples" / SAMPLES[name]).read_text(encoding="utf-8"))
        else:
            up = st.file_uploader("handoff.json", type=["json"])
            if up is not None:
                try:
                    handoff = json.loads(up.getvalue().decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    st.error("JSON 파일을 읽지 못했어요.")
        source = st.text_area("원래 하신 말씀 (제주어 원문, 없으면 비워 두세요)",
                              value=(handoff or {}).get("source_text", ""), height=90)
    if handoff is None:
        st.info("예시를 고르거나 handoff.json 파일을 올려 주세요.")
        return
    if not isinstance(handoff, dict) or "query" not in handoff or "package" not in handoff:
        st.error("handoff 형식이 아니에요. query 와 package 가 있어야 합니다.")
        return
    show_draft(build_draft(handoff, source_text=source))


# ---------------------------------------------------------------- 대화 화면 공통
@st.cache_resource(show_spinner="준비하고 있어요...")
def get_agent():
    """Agent 는 앱 전체에서 하나만 만든다(근거 DB 연결을 여러 번 열지 않게). 호출은 잠금으로 한 번에 하나씩."""
    from complaint_agent.pipeline import Agent
    return Agent(), threading.Lock()


@st.cache_resource
def topic_names() -> list[str]:
    """주제 이름 목록(structure.topic_names() = 근거 엔진 registry 의 topics 이름). 고른 주제는 엔진이 유형까지 정한다."""
    try:
        get_agent()                                   # Agent 를 불러오면 dialect_normalizer·evidence_engine 경로가 잡힌다
        import structure                              # noqa: E402  (dialect_normalizer)
        return structure.topic_names()
    except Exception:                                 # noqa: BLE001  목록을 못 받아도 화면은 열린다
        return TOPIC_FALLBACK


def place_info(agent) -> dict | None:
    """agent.place_choices() — {"all": "제주 전체", "max_picks": 3, "groups": [{"label", "names"}]}. 없으면 None."""
    fn = getattr(agent, "place_choices", None)
    try:
        return fn() if fn else None
    except Exception:                                 # noqa: BLE001
        return None


def call(fn, *args, **kwargs):
    """Agent 를 부르는 flow 함수를 실행하고 state 를 바꾼다. 실패하면 화면에 알리고 state 는 그대로 둔다."""
    agent, lock = get_agent()
    try:
        with lock, st.spinner("확인하고 있어요..."):
            st.session_state.flow = fn(agent, *args, **kwargs)
    except Exception as e:                      # noqa: BLE001  화면이 죽지 않게 모든 오류를 알림으로 바꾼다
        st.session_state.flow_error = f"처리하지 못했어요 ({type(e).__name__}). 다시 눌러 보시거나 처음부터 다시 해 주세요."
    st.rerun()


def go(fn) -> None:
    """Agent 를 부르지 않는 이동(이전 단계로)."""
    st.session_state.flow = fn(st.session_state.flow)


def show_messages(s: dict) -> None:
    """엔진 알림('알림: ...')은 개발용이라 ?mode=dev 에서만 보인다. 되묻기와 관련된 안내는 늘 보인다."""
    for m in s["messages"]:
        if DEV or not m.startswith("알림:"):
            st.caption(m)


def place_picker(choices: dict, current: list[str], key: str) -> tuple[list[str], bool]:
    """시·읍면/동으로 묶은 알약 단추 + '제주 전체'. (고른 장소, 개수 초과 여부)"""
    all_name, max_n = choices["all"], choices["max_picks"]
    whole = st.checkbox(f"{all_name}  ({ALL_NOTE})", value=all_name in current, key=f"{key}_all")
    picked: list[str] = []
    for i, g in enumerate(choices["groups"]):
        sel = st.pills(g["label"], g["names"], selection_mode="multi", key=f"{key}_g{i}", disabled=whole,
                       default=[n for n in current if n in g["names"]])
        picked += list(sel or [])
    if whole:
        return [all_name], False
    over = len(picked) > max_n
    if over:
        st.warning(f"{max_n}곳까지 고를 수 있어요. 지금 {len(picked)}곳을 골랐어요.")
    else:
        st.caption(f"{len(picked)} / {max_n}곳 고름")
    return picked, over


@st.dialog("지역 고르기", width="large")
def place_dialog(choices: dict) -> None:
    st.caption(f"최대 {choices['max_picks']}곳까지 고를 수 있습니다. 안 고르셔도 됩니다.")
    picked, over = place_picker(choices, st.session_state.get("in_places", []), "dlg")
    c1, c2 = st.columns(2)
    if c1.button("고른 것 지우기", use_container_width=True):
        st.session_state.in_places = []
        st.rerun()
    if c2.button("다 골랐어요", type="primary", disabled=over, use_container_width=True):
        st.session_state.in_places = picked
        st.rerun()


# ---------------------------------------------------------------- 1. 불편 쓰기
def step_input(agent) -> None:
    st.html(style.title("무엇이 불편하셨나요?", "편하게 써 주세요. 제주말로 쓰셔도 알아듣습니다."))
    with st.container(key="card_write"):
        st.text_area("불편한 점 쓰기", key="in_text", height=130,
                     placeholder="예: 우리 동네 버스가 확 줄어부난 할망덜 병원 가기가 막 힘들어마씸.")
        st.caption("평소 말하듯이 쓰시면 됩니다. 어려운 말로 바꾸지 않으셔도 돼요.")

    choices = place_info(agent)
    names = topic_names()
    left, right = st.container(key="row_topic").columns([1, 1.3], gap="medium")
    with left.container(key="card_place"):
        if choices:
            st.html(style.card_head("어느 지역 이야기인가요?", f"안 골라도 됩니다 · 최대 {choices['max_picks']}곳"))
            places = st.session_state.get("in_places", [])
            st.html(style.place_tags(places))
            c1, c2 = st.columns(2)
            if c1.button("지역 고르기", key="open_places", use_container_width=True):
                place_dialog(choices)
            if places and c2.button("고른 지역 지우기", key="clear_places", use_container_width=True):
                st.session_state.in_places = []
                st.rerun()
            st.html(style.small_note(ALL_NOTE))
        else:
            st.html(style.card_head("어느 지역 이야기인가요?"))
            st.html(style.small_note("글에 읍·면·동 이름을 함께 써 주세요."))
    with right.container(key="card_topic"):
        st.html(style.card_head("어떤 종류의 불편인가요?", "안 골라도 됩니다 · 여러 개 가능"))
        st.pills("주제", names, selection_mode="multi", key="in_topics", label_visibility="collapsed")

    if getattr(agent, "mock_engine", False):
        st.html(style.small_note("※ 이 컴퓨터에는 근거 DB가 없어 근거 수치는 형식 예시로만 나옵니다."))
    note, btn = st.columns([3, 1], vertical_alignment="center")
    note.html(style.footnote("이 도우미는 민원서를 써 드리는 일만 합니다. 접수는 직접 하셔야 합니다.", small=True))
    if btn.button("다음으로 →", type="primary", key="go_next", use_container_width=True):
        text = st.session_state.get("in_text", "")
        if not text.strip():
            st.warning("불편한 점을 먼저 써 주세요.")
        else:
            topics = list(st.session_state.get("in_topics") or [])
            call(flow.start, text, places=st.session_state.get("in_places", []), topics=topics)


# ---------------------------------------------------------------- 2. 되묻기
def step_questions(s: dict) -> None:
    st.html(style.bubble("몇 가지만 여쭤볼게요.", "아래 물음에 답해 주시면 더 정확하게 써 드립니다."))
    show_messages(s)
    v = flow.question_view(s)
    with st.container(key="card_ask"):
        st.html(style.ask_head(v["question"], "확인" if v["kind"] in ("dialect", "confirm_emd") else "한 가지만 더 여쭤볼게요"))
        with st.form("f_question"):
            if v["kind"] == "dialect":
                st.info(v["understood"])
                for a in v["asks"]:
                    st.write(f"· {a}")
                text = st.text_input("다르면 바른 문장을 써 주세요 (맞으면 비워 두세요)")
                if st.form_submit_button("다음으로 →", type="primary", use_container_width=True):
                    call(flow.answer_question, s, text=text)
            elif v["kind"] == "confirm_emd":
                c1, c2 = st.columns(2)
                if c1.form_submit_button("예, 맞아요", type="primary", use_container_width=True):
                    call(flow.answer_question, s, yes=True)
                if c2.form_submit_button("아니요", use_container_width=True):
                    call(flow.answer_question, s, yes=False)
            elif v["kind"] == "emd" and v.get("choices"):
                picked, over = place_picker(v["choices"], s["places"], f"q_emd_{s['q_rounds']}")
                if st.form_submit_button("다 골랐어요 →", type="primary", use_container_width=True):
                    if over:
                        st.warning(f"{v['choices']['max_picks']}곳까지 골라 주세요.")
                    elif not picked:
                        st.warning("지역을 하나 이상 골라 주세요.")
                    else:
                        call(flow.answer_question, s, places=picked)
            elif v["kind"] == "request_suggest":
                text = st.text_input("다른 바람이 있으면 써 주세요 (없으면 비워 두세요)")
                c1, c2 = st.columns(2)
                if c1.form_submit_button(f"예, '{v['suggestion']}' 맞아요", type="primary", use_container_width=True):
                    call(flow.answer_question, s, yes=True)
                if c2.form_submit_button("쓴 내용으로 / 없음", use_container_width=True):
                    call(flow.answer_question, s, text=text, yes=False)
            else:
                optional = v["kind"] == "request"
                text = st.text_input("답", label_visibility="collapsed",
                                     placeholder="없으면 비워 두고 '다음으로'를 누르세요" if optional else "")
                if st.form_submit_button("다음으로 →", type="primary", use_container_width=True):
                    if text.strip() or optional:
                        call(flow.answer_question, s, text=text)
                    else:
                        st.warning("답을 써 주세요.")
    st.button("← 처음부터 다시 쓰기", on_click=restart)


# ---------------------------------------------------------------- 3. 뜻 고르기
def step_meanings(s: dict) -> None:
    views = flow.meaning_view(s)
    most = max(v["max_picks"] for v in views)
    st.html(style.bubble("말씀하신 내용이 어떤 뜻에 가까운지 골라 주세요.", f"말씀마다 최대 {most}개까지 고를 수 있습니다."))
    show_messages(s)
    picks, ok = {}, True
    cols = st.columns(2 if len(views) > 1 else 1, gap="medium")
    for i, v in enumerate(views):
        with cols[i % len(cols)].container(key=f"card_claim_{i}"):
            st.html(style.claim_head(f"{ORDINAL[i] if i < len(ORDINAL) else i + 1} 말씀", v["text"]))
            chosen = []
            for o in v["options"]:
                is_other = o["kind"] == "other"
                label = "기타 (여기에 없는 뜻)" if is_other else o["text"] + (" · 확인할 자료 없음" if o["kind"] == "no_data" else "")
                help_ = "고르면 이 말씀은 근거 자료 없이 그대로 적습니다." if is_other else None
                if st.checkbox(label, value=o["option_id"] == v["recommended"], help=help_,
                               key=f"m_{s['confirm_tries']}_{v['claim_id']}_{o['option_id']}"):
                    chosen.append(o["option_id"])
            n = len([c for c in chosen if c != v["other_id"]])
            if not chosen:
                st.warning("하나 이상 골라 주세요. 맞는 뜻이 없으면 '기타'를 고르세요.")
                ok = False
            elif n > v["max_picks"]:
                st.warning(f"이 말씀은 {v['max_picks']}개까지 고를 수 있어요.")
                ok = False
            else:
                st.caption(f"{n} / {v['max_picks']}개 고름")
            picks[v["claim_id"]] = chosen
    c1, c2 = st.columns(2)
    c1.button("← 처음부터 다시 쓰기", on_click=restart, use_container_width=True)
    if c2.button("다음으로 →", type="primary", disabled=not ok, use_container_width=True):
        call(flow.submit_meanings, s, picks)


# ---------------------------------------------------------------- 4. 최종 확인
def step_confirm(s: dict) -> None:
    st.html(style.bubble("제가 이렇게 알아들었어요. 맞나요?"))
    left, right = st.container(key="row_confirm").columns([4, 6], gap="medium")
    with left.container(key="card_said"):
        st.html(style.said_card(" ".join(s["said"])))
        show_messages(s)
    right.html(style.understood_card([(k, v, False) for k, v in flow.confirm_rows(s)]))
    c1, c2 = st.columns([4, 6], gap="medium")          # 단추 넓이를 위 두 카드(쓰신 글 / 이렇게 이해했어요)와 맞춘다
    if c1.button("아니요, 다시 답할게요", use_container_width=True):
        call(flow.confirm, s, False)
    if c2.button("맞아요", type="primary", use_container_width=True):
        call(flow.confirm, s, True)
    st.caption("'아니요'를 누르면 되묻기 화면으로 돌아갑니다.")


# ---------------------------------------------------------------- 5. 근거 고르기
def step_evidence(s: dict) -> None:
    items = s["evidence_items"]
    usable = [it for it in items if it["usable"]]
    if usable:
        st.html(style.bubble("민원서에 넣을 근거를 골라 주세요.", "필요 없는 것은 빼셔도 됩니다. 자료와 맞지 않는 근거는 넣을 수 없어요."))
        box = st.container()
    else:                                       # 넣을 수 있는 근거가 없으면: 안내를 먼저, 찾아본 자료는 접어 둔다
        st.html(style.bubble("자료로 뒷받침되는 근거를 찾지 못했어요."))
        st.html(style.no_evidence_note(items))
        box = st.expander(f"찾아본 자료 {len(items)}건 보기")
    picked = []
    per_row = 3
    with box:
        for start in range(0, len(items), per_row):
            cols = st.container(key=f"row_ev_{start}").columns(per_row, gap="medium")
            for col, it in zip(cols, items[start:start + per_row]):
                on_now = it["usable"] and it["index"] in s["picked"]
                with col.container(key=f"card_ev_{'on_' if on_now else ''}{it['index']}"):
                    if it["usable"]:
                        if st.checkbox("민원서에 넣기", value=on_now, key=f"ev_{s['confirm_tries']}_{it['index']}"):
                            picked.append(it["index"])
                    else:
                        st.checkbox("넣을 수 없음", value=False, disabled=True, key=f"ev_off_{it['index']}")
                    st.html(style.evidence_card(it))
        if any(it["stale"] for it in items):
            st.caption("기준일이 2년 넘은 자료는 '오래된 자료'로 표시됩니다. 넣을지 말지는 직접 정하실 수 있습니다.")
    label = f"고른 근거 {len(picked)}건으로 민원서 만들기 →" if picked else "근거 없이 민원서 만들기 →"
    c1, c2 = st.columns(2)
    c1.button("← 이전", on_click=go, args=(flow.back_to_confirm,), use_container_width=True)
    if c2.button(label, type="primary", use_container_width=True):
        call(flow.choose_evidence, s, picked)


# ---------------------------------------------------------------- 화면 고르기
def page_talk() -> None:
    try:
        agent, _ = get_agent()
    except Exception as e:                      # noqa: BLE001
        st.error("대화 기능을 준비하지 못했어요. 저장소 맨 위에서 `pip install -r requirements.txt` 를 먼저 실행해 주세요. "
                 f"({type(e).__name__}: {e})")
        return

    s = st.session_state.setdefault("flow", flow.new_state())
    stage = s["stage"]
    st.html(style.stepper(STEP_OF[stage]))
    if st.session_state.get("flow_error"):
        st.error(st.session_state.pop("flow_error"))

    if stage == "input":
        step_input(agent)
    elif stage == "questions":
        step_questions(s)
    elif stage == "meanings":
        step_meanings(s)
    elif stage == "confirm":
        step_confirm(s)
    elif stage == "evidence":
        step_evidence(s)
    elif stage == "draft":
        if s["evidence_items"]:
            st.button("← 근거 다시 고르기", on_click=go, args=(flow.back_to_evidence,))
        show_draft(s["draft"])
        if DEV:
            st.download_button("handoff.json 받기 (개발·확인용)", json.dumps(s["handoff"], ensure_ascii=False, indent=2),
                               file_name="handoff.json", mime="application/json")
    else:                                       # stopped
        st.html(style.bubble("여기서 멈출게요."))
        st.warning(s["stop_reason"])
        st.button("← 처음부터 다시 쓰기", on_click=restart)


# ---------------------------------------------------------------- 화면
st.html(style.css(font_size()))
show_header()
if st.query_params.get("mode") == "file":      # 시연·개발용: 주소 끝에 ?mode=file 을 붙이면 예시·파일로 보기
    page_file()
else:
    page_talk()
