"""민원서 작성 도우미 화면 (Streamlit).

    저장소 맨 위에서:  streamlit run complaint_draft/app.py

말로 시작하기 : 불편한 점을 쓰면 되묻기 → 뜻 고르기 → 최종 확인을 거쳐 민원서 초안까지 한 화면에서 이어진다.
               (complaint_agent 의 Agent 를 그대로 부른다. 흐름은 complaint_draft/flow.py)
예시·파일로 보기 : 예시나 handoff JSON(python -m complaint_agent --out handoff.json "...")으로 초안만 본다.
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

from complaint_draft import flow                            # noqa: E402
from complaint_draft.draft import apply_edits, build_draft, plain_text   # noqa: E402
from complaint_draft.view import render_html                # noqa: E402

SAMPLES = {
    "예시 1 · 근거가 있는 민원 (버스·병원)": "supported.json",
    "예시 2 · 일부 근거가 빠진 민원 (원문 없음)": "mixed.json",
    "예시 3 · 근거 DB 없이 만든 결과 (mock)": "mock.json",
}
MODE_TALK, MODE_FILE = "말로 시작하기", "예시·파일로 보기"

st.set_page_config(page_title="민원서 작성 도우미", page_icon="📝", layout="centered")
st.markdown("""
<style>
  html, body, [class*="css"], .stMarkdown, .stRadio, .stSelectbox, .stTextArea, .stTextInput { font-size: 20px; }
  .stRadio label p, .stTextArea textarea, .stTextInput input { font-size: 20px; }
  .stButton button, .stDownloadButton button, .stFormSubmitButton button { font-size: 20px; padding: 0.7rem 1.4rem; }
</style>
""", unsafe_allow_html=True)


# ---------------------------------------------------------------- 공통: 초안 보여 주기
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
    with st.expander("민원서 고치기" + (" (고친 글 있음)" if edited else ""), expanded=False):
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
    for n in draft["notices"]:
        st.info(n)
    if draft["pii_masked"]:
        st.info("개인정보를 ○○로 가렸어요: " + ", ".join(draft["pii_masked"]))

    # 화면 HTML은 사용자 글을 JSON으로만 넣고 textContent 로 그린다(view.html 참고).
    st.iframe(render_html(draft), height=760)

    st.download_button("표준어 민원서 글로 받기 (.txt)", plain_text(draft, "standard"),
                       file_name=f"민원서_{draft['request_id'] or '초안'}.txt", mime="text/plain",
                       disabled=not draft["copy_allowed"])

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


# ---------------------------------------------------------------- 예시·파일로 보기
def page_file() -> None:
    st.caption("표준어 민원서는 제출용, 제주어 화면은 하신 말씀을 확인하는 용도입니다.")
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


# ---------------------------------------------------------------- 말로 시작하기
@st.cache_resource(show_spinner="준비하고 있어요...")
def get_agent():
    """Agent 는 앱 전체에서 하나만 만든다(근거 DB 연결을 여러 번 열지 않게). 호출은 잠금으로 한 번에 하나씩."""
    from complaint_agent.pipeline import Agent
    return Agent(), threading.Lock()


def call(fn, *args, **kwargs):
    """Agent 를 부르는 flow 함수를 실행하고 state 를 바꾼다. 실패하면 화면에 알리고 state 는 그대로 둔다."""
    agent, lock = get_agent()
    try:
        with lock, st.spinner("확인하고 있어요..."):
            st.session_state.flow = fn(agent, *args, **kwargs)
    except Exception as e:                      # noqa: BLE001  화면이 죽지 않게 모든 오류를 알림으로 바꾼다
        st.session_state.flow_error = f"처리하지 못했어요 ({type(e).__name__}). 다시 눌러 보시거나 처음부터 다시 해 주세요."
    st.rerun()


def restart() -> None:
    st.session_state.flow = flow.new_state()
    st.session_state.pop("flow_error", None)


def show_steps(stage: str) -> None:
    now = flow.STAGES.index(stage) if stage in flow.STAGES else 0
    parts = [f"**{i + 1}. {flow.STAGE_LABELS[s]}**" if i == now else f"{i + 1}. {flow.STAGE_LABELS[s]}"
             for i, s in enumerate(flow.STAGES)]
    st.markdown(" → ".join(parts))
    st.progress((now + 1) / len(flow.STAGES))


def ask(text: str) -> None:
    st.subheader(text)


def page_talk() -> None:
    try:
        agent, _ = get_agent()
    except Exception as e:                      # noqa: BLE001
        st.error("대화 기능을 준비하지 못했어요. 저장소 맨 위에서 `pip install -r requirements.txt` 를 먼저 실행해 주세요. "
                 f"({type(e).__name__}: {e})")
        return

    s = st.session_state.setdefault("flow", flow.new_state())
    stage = s["stage"]
    show_steps(stage)
    if st.session_state.get("flow_error"):
        st.error(st.session_state.pop("flow_error"))
    if stage not in ("input", "draft"):
        st.text("하신 말씀")
        st.info(" ".join(s["said"]))
        for m in s["messages"]:
            st.caption(m)

    if stage == "input":
        ask("어떤 점이 불편하신가요?")
        st.caption("제주어로 편하게 쓰셔도 됩니다. 어느 읍·면·동인지 함께 적어 주시면 좋아요.")
        if getattr(agent, "mock_engine", False):
            st.caption("※ 이 컴퓨터에는 근거 DB가 없어 근거 수치는 형식 예시로만 나옵니다.")
        with st.form("f_input"):
            text = st.text_area("불편한 점", height=140, label_visibility="collapsed",
                                placeholder="예: 우리 동네 버스가 확 줄어부난 할망덜 병원 가기가 막 힘들어마씸. 구좌읍이우다.")
            if st.form_submit_button("다음", type="primary", use_container_width=True):
                if text.strip():
                    call(flow.start, text)
                else:
                    st.warning("불편한 점을 먼저 써 주세요.")

    elif stage == "questions":
        v = flow.question_view(s)
        ask(v["question"])
        with st.form("f_question"):
            if v["kind"] == "dialect":
                st.info(v["understood"])
                for a in v["asks"]:
                    st.write(f"· {a}")
                text = st.text_input("다르면 바른 문장을 써 주세요 (맞으면 비워 두세요)")
                if st.form_submit_button("다음", type="primary", use_container_width=True):
                    call(flow.answer_question, s, text=text)
            elif v["kind"] == "confirm_emd":
                c1, c2 = st.columns(2)
                if c1.form_submit_button("네, 맞아요", type="primary", use_container_width=True):
                    call(flow.answer_question, s, yes=True)
                if c2.form_submit_button("아니요", use_container_width=True):
                    call(flow.answer_question, s, yes=False)
            elif v["kind"] == "request_suggest":
                text = st.text_input("다른 요청이 있으면 써 주세요 (없으면 비워 두세요)")
                c1, c2 = st.columns(2)
                if c1.form_submit_button(f"네, '{v['suggestion']}' 맞아요", type="primary", use_container_width=True):
                    call(flow.answer_question, s, yes=True)
                if c2.form_submit_button("쓴 내용으로 / 없음", use_container_width=True):
                    call(flow.answer_question, s, text=text, yes=False)
            else:
                optional = v["kind"] == "request"
                text = st.text_input("답", label_visibility="collapsed",
                                     placeholder="없으면 비워 두고 '다음'을 누르세요" if optional else "")
                if st.form_submit_button("다음", type="primary", use_container_width=True):
                    if text.strip() or optional:
                        call(flow.answer_question, s, text=text)
                    else:
                        st.warning("답을 써 주세요.")

    elif stage == "meanings":
        ask("어떤 뜻에 가까운가요?")
        st.caption("고르신 뜻으로 제주 데이터를 확인해요.")
        views = flow.meaning_view(s)
        with st.form("f_meanings"):
            chosen, others = {}, {}
            for v in views:
                st.write(f"“{v['text']}”")
                ids = [o["option_id"] for o in v["options"]]
                by_id = {o["option_id"]: o for o in v["options"]}

                def label(oid, by_id=by_id, rec=v["recommended"]):
                    o = by_id[oid]
                    mark = " (추천)" if oid == rec else ""
                    note = " · 확인할 자료 없음" if o["kind"] == "no_data" else ""
                    return f"{o['text']}{mark}{note}"

                chosen[v["claim_id"]] = st.radio("뜻", ids, format_func=label, key=f"m_{v['claim_id']}_{s['other_rounds']}",
                                                 index=ids.index(v["recommended"]) if v["recommended"] in ids else 0,
                                                 label_visibility="collapsed")
                if any(o["kind"] == "other" for o in v["options"]):
                    others[v["claim_id"]] = st.text_input("'기타'를 고르셨다면 여기에 직접 써 주세요",
                                                          key=f"o_{v['claim_id']}_{s['other_rounds']}")
            if st.form_submit_button("다음", type="primary", use_container_width=True):
                picks = {}
                for v in views:
                    cid = v["claim_id"]
                    kind = next(o["kind"] for o in v["options"] if o["option_id"] == chosen[cid])
                    picks[cid] = {"other": others.get(cid, "")} if kind == "other" else chosen[cid]
                call(flow.submit_meanings, s, picks)

    elif stage == "confirm":
        ask("이렇게 이해했어요. 맞나요?")
        st.info(s["summary"].replace(" 이렇게 이해했어요. 맞나요?", ""))
        c1, c2 = st.columns(2)
        if c1.button("네, 민원서 만들어 주세요", type="primary", use_container_width=True):
            call(flow.confirm, s, True)
        if c2.button("아니요, 다시 고를게요", use_container_width=True):
            call(flow.confirm, s, False)

    elif stage == "draft":
        st.caption("표준어 민원서는 제출용, 제주어 화면은 하신 말씀을 확인하는 용도입니다.")
        show_draft(s["draft"])
        st.download_button("handoff.json 받기 (개발·확인용)", json.dumps(s["handoff"], ensure_ascii=False, indent=2),
                           file_name="handoff.json", mime="application/json")

    else:   # stopped
        st.warning(s["stop_reason"])

    if stage != "input":
        st.button("처음부터 다시", on_click=restart)


# ---------------------------------------------------------------- 화면
st.title("민원서 작성 도우미")
mode = st.sidebar.radio("시작 방법", [MODE_TALK, MODE_FILE])
if mode == MODE_TALK:
    page_talk()
else:
    page_file()
