"""민원서 초안 화면 (Streamlit).

    저장소 맨 위에서:  streamlit run complaint_draft/app.py

handoff JSON(python -m complaint_agent --out handoff.json "..." 결과)을 올리거나 예시를 골라 초안을 봅니다.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from complaint_draft.draft import build_draft, plain_text   # noqa: E402
from complaint_draft.view import render_html                # noqa: E402

SAMPLES = {
    "예시 1 · 근거가 있는 민원 (버스·병원)": "supported.json",
    "예시 2 · 일부 근거가 빠진 민원 (원문 없음)": "mixed.json",
    "예시 3 · 근거 DB 없이 만든 결과 (mock)": "mock.json",
}

st.set_page_config(page_title="민원서 초안", page_icon="📝", layout="centered")
st.markdown("""
<style>
  html, body, [class*="css"], .stMarkdown, .stRadio, .stSelectbox, .stTextArea { font-size: 19px; }
  .stButton button, .stDownloadButton button { font-size: 19px; padding: 0.6rem 1.2rem; }
</style>
""", unsafe_allow_html=True)

st.title("민원서 초안")
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
    st.stop()
if not isinstance(handoff, dict) or "query" not in handoff or "package" not in handoff:
    st.error("handoff 형식이 아니에요. query 와 package 가 있어야 합니다.")
    st.stop()

draft = build_draft(handoff, source_text=source)

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
