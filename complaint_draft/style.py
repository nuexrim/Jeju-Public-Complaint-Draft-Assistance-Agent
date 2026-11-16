"""화면 꾸밈(색·글자·감귤 캐릭터·단계 표시). 시안의 색과 크기를 그대로 옮겼다.

여기서 만드는 HTML 은 고정 글과 단계 번호뿐이다. 사용자가 쓴 글을 넣을 때는 반드시 esc() 를 거친다.
"""
from __future__ import annotations

import base64
import html

INK, MUTED, LINE, GREY = "#1F2326", "#4A5157", "#B9BEC2", "#ECEAE6"
BG, ORANGE, SOFT, BROWN, GREEN = "#FFF8EE", "#F7941D", "#FFE9CC", "#9A4100", "#2F6B2C"
ROOT_PX = {"보통": 17, "크게": 20}

esc = html.escape

_MASCOT_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64" viewBox="0 0 64 64" fill="none">'
    '<path d="M32 18V11" stroke="#5A3B1A" stroke-width="3" stroke-linecap="round"/>'
    '<path d="M32 15C32 9 36 5 43 4C43 11 39 15 32 15Z" fill="#2F6B2C"/>'
    '<ellipse cx="32" cy="38" rx="25" ry="22" fill="#F7941D"/><ellipse cx="22" cy="27" rx="7" ry="4" fill="#FFD08A"/>'
    '<circle cx="23" cy="37" r="3.2" fill="#1F2326"/><circle cx="41" cy="37" r="3.2" fill="#1F2326"/>'
    '<path d="M26 44Q32 50 38 44" stroke="#1F2326" stroke-width="2.8" stroke-linecap="round"/>'
    '<circle cx="17" cy="44" r="3.5" fill="#F26B4E" fill-opacity="0.6"/><circle cx="47" cy="44" r="3.5" fill="#F26B4E" fill-opacity="0.6"/></svg>'
)
_MIC_SVG = ('<svg xmlns="http://www.w3.org/2000/svg" width="68" height="68" viewBox="0 0 24 24" fill="none"><rect x="9" y="3" width="6" height="11" rx="3" fill="#1F2326"/>'
            '<path d="M5.5 11a6.5 6.5 0 0 0 13 0M12 17.5V21M8.5 21h7" stroke="#1F2326" stroke-width="2" stroke-linecap="round"/></svg>')
_CHECK_SVG = ('<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none">'
              '<path d="M5 12.5l4.5 4.5L19 7.5" stroke="#FFFFFF" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/></svg>')


def _img(svg: str, size: int, alt: str) -> str:
    """st.html 은 <svg> 를 걸러 내므로 그림은 data URI <img> 로 넣는다. alt 가 비면 꾸밈 그림."""
    uri = "data:image/svg+xml;base64," + base64.b64encode(svg.encode("utf-8")).decode("ascii")
    return f'<img src="{uri}" width="{size}" height="{size}" alt="{esc(alt)}" style="flex:none;display:block">'


def mascot(size: int, alt: str = "감귤 캐릭터") -> str:
    return _img(_MASCOT_SVG, size, alt)


MIC = _img(_MIC_SVG, 68, "")
CHECK = _img(_CHECK_SVG, 24, "완료")


def css(size: str) -> str:
    px = ROOT_PX.get(size, ROOT_PX["크게"])
    return f"""<style>
@import url('https://fonts.googleapis.com/css2?family=Jua&family=Noto+Sans+KR:wght@400;500;700&display=swap');
html {{ font-size: {px}px; }}
html, body, .stApp, .stApp p, .stApp label, .stApp input, .stApp textarea, .stApp button, .stApp li {{
  font-family: 'Noto Sans KR', 'Apple SD Gothic Neo', 'Malgun Gothic', sans-serif; }}
.stApp {{ background: {BG}; color: {INK}; }}
[data-testid="stHeader"] {{ background: transparent; }}
.block-container {{ max-width: 100%; padding: 1.2rem clamp(20px, 4vw, 64px) 3rem; }}
.stApp h1 {{ font-size: 1.8rem; line-height: 1.3; padding: 0; }}
.stApp h2 {{ font-size: 1.4rem; line-height: 1.3; padding: 0; }}
.stApp h3 {{ font-size: 1.3rem; line-height: 1.3; padding: 0; }}
.stApp p, .stApp label p, .stApp li {{ font-size: 1.05rem; }}

/* 단추: 주황(주요) / 흰 바탕 검은 테두리(보조) */
.stButton > button, .stFormSubmitButton > button, .stDownloadButton > button {{
  min-height: 60px; border-radius: 16px; border: 2px solid {INK}; background: #FFFFFF; color: {INK}; padding: 0.6rem 1.6rem; }}
.stButton > button p, .stFormSubmitButton > button p, .stDownloadButton > button p {{ font-size: 1.2rem; font-weight: 700; }}
.stButton > button[kind="primary"], .stFormSubmitButton > button[kind="primaryFormSubmit"],
button[data-testid="stBaseButton-primary"], button[data-testid="stBaseButton-primaryFormSubmit"] {{
  background: {ORANGE}; border-color: {ORANGE}; color: {INK}; }}
.stButton > button:hover, .stFormSubmitButton > button:hover, .stDownloadButton > button:hover {{ border-color: {BROWN}; color: {INK}; }}
.stButton > button:focus-visible, .stFormSubmitButton > button:focus-visible {{ outline: 3px solid {INK}; outline-offset: 2px; }}
.stButton > button:disabled, .stDownloadButton > button:disabled {{ background: {GREY}; border-color: {GREY}; color: #6B7177; }}

/* 글 칸 */
.stTextInput [data-testid="stTextInputRootElement"], .stTextArea [data-testid="stTextAreaRootElement"] {{
  border: 2px solid {INK}; border-radius: 16px; background: {BG}; height: auto; min-height: 56px; }}
.stTextInput [data-testid="stTextInputRootElement"] > div, .stTextArea [data-testid="stTextAreaRootElement"] > div {{ background: transparent; }}
.stTextArea textarea, .stTextInput input {{ font-size: 1.1rem; line-height: 1.6; color: {INK}; background: transparent;
  border: 0; padding: 0.7rem 1rem; }}
.stRadio label p {{ font-size: 1.1rem; font-weight: 500; }}
[data-testid="stForm"] {{ border: 0; padding: 0; }}

/* 카드: st.container(key=...) 에 붙는 st-key-* 로 꾸민다 */
.st-key-hdr {{ background: #FFFFFF; border-bottom: 2px solid {GREY}; border-radius: 16px; padding: 0.6rem 1.2rem; }}
.st-key-hdr .stButton > button {{ min-height: 44px; border-radius: 999px; border: 2px solid {LINE}; padding: 0.3rem 0.6rem; white-space: nowrap; }}
.st-key-hdr .stButton > button p {{ font-size: 1rem; }}
.st-key-hdr .stButton > button[kind="primary"], .st-key-hdr button[data-testid="stBaseButton-primary"] {{
  background: {SOFT}; border-color: {BROWN}; }}
.st-key-card_write, .st-key-card_said, .st-key-card_edit {{ background: #FFFFFF; border: 2px solid {LINE}; border-radius: 24px; padding: 1.3rem; }}
.st-key-card_ask {{ background: {SOFT}; border: 2px solid {BROWN}; border-radius: 20px; padding: 1.1rem 1.3rem; }}
.st-key-card_ask .stTextInput [data-testid="stTextInputRootElement"] {{ background: #FFFFFF; }}
/* 1단계: '말로 하기' 카드와 '글로 쓰기' 카드의 높이를 맞춘다 */
.st-key-row_input [data-testid="stHorizontalBlock"] {{ align-items: stretch; }}
.st-key-row_input [data-testid="stColumn"] {{ display: flex; flex-direction: column; }}
.st-key-row_input [data-testid="stColumn"] > [data-testid="stVerticalBlock"] {{ flex: 1; }}
.st-key-row_input [data-testid="stColumn"]:first-child [data-testid="stElementContainer"],
.st-key-row_input [data-testid="stColumn"]:first-child [data-testid="stHtml"] {{ flex: 1; display: flex; flex-direction: column; height: 100%; }}
.st-key-row_input [data-testid="stColumn"]:first-child section {{ flex: 1; }}
.st-key-row_input .st-key-card_write {{ flex: 1; }}
[data-testid="stExpander"] details {{ background: #FFFFFF; border: 2px solid {LINE}; border-radius: 16px; }}
[data-testid="stExpander"] summary p {{ font-size: 1.1rem; font-weight: 700; }}
</style>"""


def header() -> str:
    face = mascot(52)
    return (f'<div style="display:flex;align-items:center;gap:14px">{face}'
            f'<div style="display:flex;flex-direction:column;gap:2px">'
            f'<div style="font-family:\'Jua\',sans-serif;font-size:1.4rem;line-height:1.2;color:{INK}">제주 민원 도우미</div>'
            f'<div style="font-size:0.78rem;font-weight:500;color:{MUTED}">제주말로 말해도 알아듣는 민원서 작성 도우미</div></div></div>')


STEPS = ["말하기", "뜻 확인", "민원서 초안"]


def stepper(now: int) -> str:
    """now: 0·1·2. 지난 단계는 초록 체크, 지금 단계는 주황, 남은 단계는 회색."""
    items = []
    for i, name in enumerate(STEPS):
        if i:
            items.append(f'<li aria-hidden="true" style="width:64px;height:4px;border-radius:2px;background:{ORANGE if i <= now else LINE}"></li>')
        if i < now:
            dot = f'<span style="width:44px;height:44px;border-radius:50%;background:{GREEN};display:flex;align-items:center;justify-content:center">{CHECK}</span>'
            label = f'<span style="font-size:1.1rem;font-weight:500;color:{MUTED}">{name}</span>'
        elif i == now:
            dot = (f'<span style="width:44px;height:44px;border-radius:50%;background:{ORANGE};color:{INK};display:flex;align-items:center;'
                   f'justify-content:center;font-size:1.1rem;font-weight:700">{i + 1}</span>')
            label = f'<span style="font-size:1.1rem;font-weight:700;color:{INK}">{name}</span>'
        else:
            dot = (f'<span style="width:44px;height:44px;border-radius:50%;background:{GREY};color:{MUTED};display:flex;align-items:center;'
                   f'justify-content:center;font-size:1.1rem;font-weight:700">{i + 1}</span>')
            label = f'<span style="font-size:1.1rem;font-weight:500;color:{MUTED}">{name}</span>'
        current = ' aria-current="step"' if i == now else ""
        items.append(f'<li{current} style="display:flex;align-items:center;gap:10px">{dot}{label}</li>')
    return f'<ol style="list-style:none;margin:0;padding:0;display:flex;flex-wrap:wrap;align-items:center;gap:14px">{"".join(items)}</ol>'


def title(text: str, sub: str = "") -> str:
    sub_html = f'<p style="margin:0;font-size:1.1rem;font-weight:500;color:{MUTED}">{esc(sub)}</p>' if sub else ""
    return (f'<div style="display:flex;flex-direction:column;gap:6px">'
            f'<h1 style="margin:0;font-size:1.8rem;font-weight:700;line-height:1.3;color:{INK}">{esc(text)}</h1>{sub_html}</div>')


def bubble(text: str) -> str:
    """감귤 캐릭터가 말하는 말풍선."""
    face = mascot(64, "")          # 꾸밈 그림(말풍선 글이 내용이다)
    return (f'<div style="display:flex;align-items:center;gap:16px">{face}'
            f'<h1 style="margin:0;font-size:1.3rem;font-weight:700;line-height:1.35;color:{INK};background:#FFFFFF;border:2px solid {LINE};'
            f'border-radius:20px;padding:12px 24px">{esc(text)}</h1></div>')


def voice_card() -> str:
    """말로 하기: 아직 음성 인식이 연결되지 않아 자리만 보여 준다(누를 수 없음)."""
    return (f'<section style="box-sizing:border-box;height:100%;background:{SOFT};border:2px solid {ORANGE};border-radius:24px;padding:26px;'
            f'display:flex;flex-direction:column;align-items:center;justify-content:center;gap:14px;opacity:0.75">'
            f'<div style="width:140px;height:140px;border-radius:50%;background:{ORANGE};border:6px solid #FFFFFF;display:flex;align-items:center;'
            f'justify-content:center">{MIC}</div>'
            f'<h2 style="margin:0;font-size:1.5rem;font-weight:700;color:{INK}">말로 하기</h2>'
            f'<span style="font-size:0.9rem;font-weight:700;color:{BROWN};background:#FFFFFF;border:2px solid {BROWN};border-radius:999px;padding:2px 14px">준비 중</span>'
            f'<p style="margin:0;font-size:1rem;font-weight:500;line-height:1.5;color:{MUTED};text-align:center">지금은 오른쪽에 글로 써 주세요</p></section>')


def chips(label: str, names: list[str]) -> str:
    spans = "".join(f'<span style="font-size:0.8rem;font-weight:500;background:#FFFFFF;border:1.5px solid {LINE};border-radius:999px;'
                    f'padding:2px 12px;color:{INK}">{esc(n)}</span>' for n in names)
    return (f'<div style="display:flex;flex-wrap:wrap;align-items:center;gap:8px">'
            f'<span style="font-size:0.85rem;font-weight:700;margin-right:2px;color:{INK}">{esc(label)}</span>{spans}</div>')


def said_card(text: str) -> str:
    return (f'<h2 style="margin:0 0 10px;font-size:1rem;font-weight:700;color:{BROWN}">하신 말씀</h2>'
            f'<p style="margin:0;font-size:1.05rem;line-height:1.6;color:{INK}">“{esc(text)}”</p>')


def understood_card(rows: list[tuple[str, str, bool]]) -> str:
    """rows: (이름, 값, 확인 필요 여부). 값은 사용자 글에서 온 것이므로 가린다(esc)."""
    out = []
    for name, value, check in rows:
        badge = (f'<span style="font-size:0.85rem;font-weight:700;color:{BROWN};background:{SOFT};border:2px solid {BROWN};'
                 f'border-radius:999px;padding:1px 12px">확인 필요</span>') if check else ""
        out.append(f'<div style="background:{BG};border-radius:12px;padding:7px 18px;display:flex;flex-wrap:wrap;align-items:center;gap:4px 18px">'
                   f'<span style="width:100px;font-size:0.95rem;font-weight:700;color:{MUTED}">{esc(name)}</span>'
                   f'<span style="font-size:1.1rem;font-weight:700;color:{INK};flex:1 1 200px">{esc(value) if value else "—"}</span>{badge}</div>')
    return (f'<section style="box-sizing:border-box;height:100%;background:#FFFFFF;border:2px solid {INK};border-radius:20px;padding:20px 24px;'
            f'display:flex;flex-direction:column;gap:10px">'
            f'<h2 style="margin:0;font-size:1rem;font-weight:700;color:{BROWN}">이렇게 이해했어요</h2>'
            f'<div style="display:flex;flex-direction:column;gap:8px">{"".join(out)}</div></section>')


def ask_head(text: str, kicker: str = "한 가지만 더 여쭤볼게요") -> str:
    return (f'<p style="margin:0 0 4px;font-size:0.95rem;font-weight:700;color:{BROWN}">{esc(kicker)}</p>'
            f'<h2 style="margin:0;font-size:1.3rem;font-weight:700;line-height:1.3;color:{INK}">{esc(text)}</h2>')


def footnote(text: str, small: bool = False) -> str:
    size, pad = ("0.85rem", "10px 18px") if small else ("1rem", "18px 24px")
    return (f'<p style="margin:0;font-size:{size};font-weight:500;line-height:1.5;color:{INK};background:{GREY};border-radius:14px;'
            f'padding:{pad}">{esc(text)}</p>')


def small_note(text: str) -> str:
    return f'<p style="margin:0;font-size:0.8rem;color:{MUTED}">{esc(text)}</p>'
