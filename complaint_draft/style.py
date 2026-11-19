"""화면 꾸밈(색·글자·감귤 캐릭터·단계 표시). 시안의 색과 크기를 그대로 옮겼다.

여기서 만드는 HTML 은 고정 글과 단계 번호뿐이다. 사용자가 쓴 글을 넣을 때는 반드시 esc() 를 거친다.
"""
from __future__ import annotations

import base64
import html

INK, MUTED, LINE, GREY = "#1F2326", "#4A5157", "#B9BEC2", "#ECEAE6"
BG, ORANGE, SOFT, BROWN, GREEN = "#FFF8EE", "#F7941D", "#FFE9CC", "#9A4100", "#2F6B2C"
PAGE, SURF, SURF2 = "#F4F5F7", "#F4F5F7", "#ECEEF1"               # 바탕 옅은 회색, 칸 회색
BORDER, BORDER_STRONG = "#ECEEF1", "#D5D9DE"                     # 카드 선 / 칸·단추 선
SHADOW = "0 4px 22px rgba(17,24,39,0.06)"
VALUE = "#C25700"                                                 # 근거 값(큰 숫자) 색
ROOT_PX = {"작게": 15, "보통": 17, "크게": 20}
PAGE_DEFAULT_W = 1080             # 본문 폭(누리집처럼 가운데 한 줄)
WIDE_W = 1320                     # 민원서 화면 폭(초안을 넓게)

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
_CHECK_SVG = ('<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none">'
              '<path d="M5 12.5l4.5 4.5L19 7.5" stroke="#FFFFFF" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/></svg>')


def _img(svg: str, size: int, alt: str) -> str:
    """st.html 은 <svg> 를 걸러 내므로 그림은 data URI <img> 로 넣는다. alt 가 비면 꾸밈 그림."""
    uri = "data:image/svg+xml;base64," + base64.b64encode(svg.encode("utf-8")).decode("ascii")
    return f'<img src="{uri}" width="{size}" height="{size}" alt="{esc(alt)}" style="flex:none;display:block">'


def mascot(size: int, alt: str = "감귤 캐릭터") -> str:
    return _img(_MASCOT_SVG, size, alt)


CHECK = _img(_CHECK_SVG, 24, "완료")


def css(size: str, page_w: int | None = None) -> str:
    """page_w: 본문 폭. 민원서 화면은 넓게(WIDE_W)."""
    px = ROOT_PX.get(size, ROOT_PX["크게"])
    PAGE_W = page_w or PAGE_DEFAULT_W                     # noqa: N806  (아래 CSS 안에서 씀)
    return f"""<style>
@import url('https://fonts.googleapis.com/css2?family=Jua&family=Noto+Sans+KR:wght@400;500;700;800&display=swap');
html {{ font-size: {px}px; }}
html, body, .stApp, .stApp p, .stApp label, .stApp input, .stApp textarea, .stApp button, .stApp li {{
  font-family: 'Noto Sans KR', 'Apple SD Gothic Neo', 'Malgun Gothic', sans-serif; letter-spacing: -0.01em; }}
.stApp {{ background: {PAGE}; color: {INK}; overflow-x: hidden; }}
/* Streamlit 기본 머리·도구 막대·바닥 글은 숨긴다(누리집처럼 우리 머리·바닥만 보이게) */
[data-testid="stHeader"], [data-testid="stToolbar"], [data-testid="stDecoration"], #MainMenu, footer {{ display: none !important; }}
.block-container {{ max-width: {PAGE_W}px; padding: 0 24px 0; }}
[data-testid="stMainBlockContainer"] > div > [data-testid="stVerticalBlock"],
[data-testid="stMainBlockContainer"] > [data-testid="stVerticalBlock"] {{ gap: 1.25rem; min-height: 100vh; }}
/* 바닥 띠: 내용이 짧은 화면에서도 화면 맨 아래에 붙인다 */
.stElementContainer:has(> .stHtml .jj-foot), .stElementContainer:has(.jj-foot) {{ margin-top: auto; }}

/* 머리: 흰 띠, 얇은 선 */
.st-key-hdr {{ position: sticky; top: 0; z-index: 50; width: 100vw !important; max-width: none !important;
  margin-left: calc(50% - 50vw); background: rgba(255,255,255,0.96); backdrop-filter: blur(8px);
  border-bottom: 1px solid {BORDER}; padding: 10px max(24px, calc((100vw - {PAGE_W}px) / 2 + 24px)); }}
.st-key-hdr {{ margin-bottom: -1.25rem; }}
.jj-band {{ width: 100vw; margin-left: calc(50% - 50vw); box-sizing: border-box;
  padding-left: max(24px, calc((100vw - {PAGE_W}px) / 2 + 24px)); padding-right: max(24px, calc((100vw - {PAGE_W}px) / 2 + 24px)); }}

/* 제목 띠: 가로 꽉 찬 주황 띠 + 감귤 캐릭터. 아래 첫 카드가 띠 위로 겹쳐 올라온다 */
.jj-hero {{ position: relative; overflow: hidden; padding-top: 46px; padding-bottom: 104px;
  background: linear-gradient(115deg, #FFC45C 0%, #FFAA33 45%, {ORANGE} 100%); }}
.jj-hero::before {{ content: ""; position: absolute; width: 520px; height: 520px; right: calc(50% - {PAGE_W}px / 2 - 160px); top: -260px;
  border-radius: 50%; background: radial-gradient(circle, rgba(255,255,255,0.42) 0%, rgba(255,255,255,0) 68%); }}
.jj-hero::after {{ content: ""; position: absolute; width: 300px; height: 300px; left: calc(50% - {PAGE_W}px / 2 - 120px); bottom: -190px;
  border-radius: 50%; background: rgba(255,255,255,0.14); }}
.jj-hero-body {{ position: relative; z-index: 1; display: flex; align-items: center; gap: 28px; }}
.jj-hero-mascot {{ position: relative; flex: none; width: 168px; height: 168px; border-radius: 50%;
  background: radial-gradient(circle at 35% 30%, #FFFFFF 0%, #FFF3DF 72%); box-shadow: 0 16px 36px rgba(154,65,0,0.22);
  display: flex; align-items: center; justify-content: center; }}
.jj-spark {{ position: absolute; border-radius: 50%; background: #FFFFFF; }}
.jj-bubble {{ position: absolute; border-radius: 50%; background: linear-gradient(145deg, #FFFFFF 0%, #FFE2B8 100%);
  box-shadow: 0 6px 14px rgba(154,65,0,0.16); }}
.jj-progress {{ position: absolute; left: 0; bottom: 0; height: 6px; background: #FFE14D; z-index: 1; }}
.stElementContainer:has(.jj-hero) {{ margin-bottom: 0; }}
.stElementContainer:has(.jj-hero:not(.jj-flat)) + *:has([class*="st-key-card_"], [class*="st-key-row_"]) {{ margin-top: -80px; position: relative;
  z-index: 2; }}
.jj-hero.jj-flat {{ padding-bottom: 50px; }}
.stElementContainer:has(.jj-flat) {{ margin-bottom: 0.6rem; }}
.jj-foot {{ background: #E9EBEF; color: {MUTED}; border-top: 0; padding-top: 30px; padding-bottom: 36px; margin-top: 3rem; }}
.st-key-card_actions {{ padding-top: 0.4rem; margin-top: 0.2rem; }}
.stApp h1 {{ font-size: 1.8rem; line-height: 1.3; padding: 0; }}
.stApp h2 {{ font-size: 1.4rem; line-height: 1.3; padding: 0; }}
.stApp h3 {{ font-size: 1.3rem; line-height: 1.3; padding: 0; }}
.stApp p, .stApp label p, .stApp li {{ font-size: 1.05rem; }}

/* 단추: 둥근 알약. 주황(주요) / 흰 바탕 옅은 테두리(보조) */
.stButton > button, .stFormSubmitButton > button, .stDownloadButton > button {{
  min-height: 60px; border-radius: 999px; border: 1.5px solid {BORDER_STRONG}; background: #FFFFFF; color: {INK};
  box-shadow: 0 2px 8px rgba(17,24,39,0.04);
  padding: 0.6rem 1.8rem; transition: transform .12s ease, box-shadow .12s ease, border-color .12s ease; }}
.stButton > button p, .stFormSubmitButton > button p, .stDownloadButton > button p {{ font-size: 1.2rem; font-weight: 700; }}
.stButton > button[kind="primary"], .stFormSubmitButton > button[kind="primaryFormSubmit"],
button[data-testid="stBaseButton-primary"], button[data-testid="stBaseButton-primaryFormSubmit"] {{
  background: {ORANGE}; border-color: {ORANGE}; color: {INK}; box-shadow: 0 8px 20px rgba(247,148,29,0.32); }}
.stButton > button:hover, .stFormSubmitButton > button:hover, .stDownloadButton > button:hover {{ border-color: {INK}; color: {INK};
  transform: translateY(-1px); }}
.stButton > button[kind="primary"]:hover, button[data-testid="stBaseButton-primary"]:hover {{ background: #F08A10; border-color: #F08A10; }}
.stButton > button:focus-visible, .stFormSubmitButton > button:focus-visible {{ outline: 3px solid {INK}; outline-offset: 2px; }}
.stButton > button:disabled, .stDownloadButton > button:disabled {{ background: {GREY}; border-color: {GREY}; color: #6B7177;
  box-shadow: none; transform: none; }}

/* 글 칸: 옅은 회색 바탕, 누르면 주황 테두리 */
.stTextInput [data-testid="stTextInputRootElement"], .stTextArea [data-testid="stTextAreaRootElement"] {{
  border: 1.5px solid {BORDER_STRONG}; border-radius: 18px; background: {SURF}; height: auto; min-height: 56px;
  transition: border-color .12s ease, box-shadow .12s ease; }}
.stTextInput [data-testid="stTextInputRootElement"]:focus-within, .stTextArea [data-testid="stTextAreaRootElement"]:focus-within {{
  border-color: {ORANGE}; background: #FFFFFF; box-shadow: 0 0 0 4px rgba(247,148,29,0.18); }}
.stTextInput [data-testid="stTextInputRootElement"] > div, .stTextArea [data-testid="stTextAreaRootElement"] > div {{ background: transparent; }}
.stTextArea textarea, .stTextInput input {{ font-size: 1.1rem; line-height: 1.6; color: {INK}; background: transparent;
  border: 0; padding: 0.8rem 1.1rem; }}
.stTextArea textarea::placeholder, .stTextInput input::placeholder {{ color: #80878D; opacity: 1; }}
.stRadio label p {{ font-size: 1.1rem; font-weight: 500; }}
[data-testid="stForm"] {{ border: 0; padding: 0; }}

/* 머리 오른쪽: 글자 크기(회색 판 안의 알약, 고른 쪽 흰 알약) */
.st-key-hdr .stButton > button {{ min-height: 40px; border-radius: 999px; border: 1px solid {BORDER}; padding: 0.2rem 0.8rem;
  white-space: nowrap; box-shadow: none; }}
.st-key-hdr .stButton > button p {{ font-size: 0.95rem; }}
.st-key-hdr [data-testid="stButtonGroup"] div:has(> button) {{ background: {SURF2}; border-radius: 999px; padding: 4px; gap: 2px;
  display: inline-flex; }}
.st-key-hdr [data-testid="stButtonGroup"] button {{ min-height: 38px; border-radius: 999px; padding: 0.15rem 1rem;
  margin: 0 !important; border: 0 !important; background: transparent !important; box-shadow: none !important; outline: none !important; }}
.st-key-hdr [data-testid="stButtonGroup"] button[aria-checked="true"] {{ background: #FFFFFF !important;
  box-shadow: 0 2px 8px rgba(31,35,38,0.12) !important; }}
.st-key-hdr [data-testid="stButtonGroup"] button[aria-checked="true"] p {{ font-weight: 700; color: {INK}; }}
.st-key-hdr [data-testid="stButtonGroup"] button:focus-visible {{ outline: 2px solid {INK} !important; outline-offset: 1px; }}
.st-key-hdr a:hover div {{ text-decoration: underline; }}
.st-key-hdr [data-testid="stButtonGroup"] button p {{ font-size: 0.95rem; color: {MUTED}; }}
.st-key-hdr [data-testid="stMarkdownContainer"] p {{ font-size: 0.9rem; color: {MUTED}; margin: 0; text-align: right; }}
.st-key-hdr .stButton > button[kind="primary"], .st-key-hdr button[data-testid="stBaseButton-primary"] {{
  background: {SOFT}; border-color: {BROWN}; box-shadow: none; }}

/* 고른 지역 꼬리표: 마우스를 올리면 ✕ 가 보이고, 누르면 지운다 */
[class*="st-key-rm_place_"] button {{ position: relative; overflow: visible; min-height: 38px; border-radius: 999px;
  border: 1.5px solid {ORANGE}; background: {SOFT}; padding: 0.1rem 1.1rem; box-shadow: none; }}
[class*="st-key-rm_place_"] button p {{ font-size: 0.95rem; font-weight: 700; }}
[class*="st-key-rm_place_"] button::after {{ content: "✕"; position: absolute; top: -7px; right: -7px; width: 18px; height: 18px;
  border-radius: 50%; background: {INK}; color: #FFFFFF; font-size: 10px; font-weight: 700; line-height: 18px; text-align: center;
  display: none; }}
[class*="st-key-rm_place_"] button:hover::after, [class*="st-key-rm_place_"] button:focus-visible::after {{ display: block; }}
[class*="st-key-rm_place_"] button:hover {{ border-color: {INK}; }}
.st-key-draft_tools .stButton > button {{ min-height: 44px; border-radius: 999px; padding: 0.3rem 1.2rem; box-shadow: none; }}
.st-key-draft_tools .stButton > button p {{ font-size: 1rem; }}
.st-key-draft_tools .stButton > button[kind="primary"], .st-key-draft_tools button[data-testid="stBaseButton-primary"] {{
  box-shadow: 0 6px 14px rgba(247,148,29,0.28); }}
/* 표준어 | 방언 전환: 회색 판 안에 단추 둘(고른 쪽 주황) */
.st-key-draft_tools [data-testid="stButtonGroup"] div:has(> button) {{ background: {SURF2}; border-radius: 999px; padding: 4px;
  gap: 4px; display: inline-flex; }}
.st-key-draft_tools [data-testid="stButtonGroup"] button {{ min-height: 40px; margin: 0 !important; border: 0 !important;
  border-radius: 999px !important; background: transparent !important; box-shadow: none !important; outline: none !important;
  padding: 0.2rem 1.2rem; }}
.st-key-draft_tools [data-testid="stButtonGroup"] button p {{ font-size: 1rem; font-weight: 700; color: {INK}; }}
.st-key-draft_tools [data-testid="stButtonGroup"] button[aria-checked="true"] {{ background: {ORANGE} !important; }}
.st-key-draft_tools [data-testid="stButtonGroup"] button:focus-visible {{ outline: 2px solid {INK} !important; outline-offset: 1px; }}
.jj-note {{ background: {SURF}; border: 0; border-radius: 20px; padding: 18px 22px; }}

/* 카드: 흰 판, 옅은 선과 부드러운 그림자 */
.st-key-card_write, .st-key-card_said, .st-key-card_edit, .st-key-card_place, .st-key-card_topic, [class*="st-key-card_claim_"],
[class*="st-key-card_ev_"] {{ background: #FFFFFF; border: 1px solid transparent; border-radius: 24px; padding: 1.6rem 1.7rem;
  box-shadow: {SHADOW}; }}
.st-key-card_ask {{ background: #FFFFFF; border: 0; border-radius: 24px;
  padding: 1.4rem 1.6rem; box-shadow: {SHADOW}; }}
.st-key-card_ask .stTextInput [data-testid="stTextInputRootElement"] {{ background: {SURF}; }}
[class*="st-key-card_ev_"] {{ height: 100%; }}
[class*="st-key-card_ev_on_"] {{ border: 2px solid {ORANGE}; box-shadow: 0 10px 28px rgba(247,148,29,0.18); }}
/* 한 줄에 놓인 카드끼리 높이 맞추기: 열 안의 감싸는 칸들을 모두 늘려 카드가 열 높이를 채우게 한다 */
.st-key-row_topic [data-testid="stHorizontalBlock"], .st-key-row_confirm [data-testid="stHorizontalBlock"],
[class*="st-key-row_ev_"] [data-testid="stHorizontalBlock"] {{ align-items: stretch; }}
.st-key-row_topic [data-testid="stColumn"], .st-key-row_confirm [data-testid="stColumn"],
[class*="st-key-row_ev_"] [data-testid="stColumn"] {{ display: flex; flex-direction: column; }}
.st-key-row_topic [data-testid="stColumn"] div:has(.st-key-card_place, .st-key-card_topic),
.st-key-row_confirm [data-testid="stColumn"] div:has(.st-key-card_said, [data-testid="stHtml"]),
[class*="st-key-row_ev_"] [data-testid="stColumn"] div:has([class*="st-key-card_ev_"]) {{
  flex: 1 1 auto; display: flex; flex-direction: column; }}
.st-key-card_place, .st-key-card_topic, .st-key-card_said, [class*="st-key-card_ev_"] {{ flex: 1 1 auto; }}
.st-key-row_confirm [data-testid="stHtml"] {{ flex: 1 1 auto; display: flex; flex-direction: column; }}
.st-key-row_confirm [data-testid="stHtml"] > section {{ flex: 1 1 auto; }}
/* 알약 단추(지역·주제 고르기) */
[data-testid="stButtonGroup"] button {{ min-height: 46px; border-radius: 999px; border: 1.5px solid {SURF2}; background: {SURF2};
  color: {INK}; padding: 0.25rem 1.1rem; }}
[data-testid="stButtonGroup"] button:hover {{ border-color: {ORANGE}; }}
[data-testid="stButtonGroup"] button p {{ font-size: 1rem; font-weight: 500; }}
/* 고른 단추: Streamlit 은 aria-pressed="true" 로 표시한다(주황 바탕 + 검은 글씨) */
[data-testid="stButtonGroup"] button[aria-pressed="true"] {{ background: {ORANGE} !important; border-color: {ORANGE} !important; color: {INK};
  box-shadow: 0 4px 12px rgba(247,148,29,0.30); }}
[data-testid="stButtonGroup"] button[aria-pressed="true"] p {{ font-weight: 700; color: {INK}; }}
.stPills label p, .stCheckbox label p {{ font-size: 1.05rem; }}
.stCheckbox label {{ min-height: 44px; align-items: center; }}
.stCheckbox label p {{ font-weight: 500; }}
[data-testid="stDialog"] {{ background: transparent !important; }}   /* 창 뒤를 주황으로 덮지 않는다 */
[data-testid="stDialog"] [role="dialog"] {{ border: 0; border-radius: 28px; box-shadow: 0 24px 60px rgba(31,35,38,0.22); }}
/* 1단계 글 칸: 제목 굵게, 위아래 여백 같게 */
.st-key-card_write .stTextArea label p {{ font-size: 1.2rem; font-weight: 700; }}
.st-key-card_write .stTextArea {{ margin-bottom: 0; }}
[data-testid="stExpander"] details {{ background: #FFFFFF; border: 1px solid {BORDER}; border-radius: 20px; box-shadow: {SHADOW}; }}
[data-testid="stExpander"] summary p {{ font-size: 1.1rem; font-weight: 700; }}
[data-testid="stAlert"] {{ border-radius: 18px; }}
[data-testid="stAlertContainer"] {{ border-radius: 18px; }}
[data-testid="stAlertContentWarning"] {{ color: #7A4A00; }}
[data-testid="stAlert"]:has([data-testid="stAlertContentWarning"]) [data-testid="stAlertContainer"] {{ background: #FFF3D6 !important; }}
.jj-steps {{ border-radius: 999px; }}
@media (max-width: 720px) {{ .jj-hero {{ padding-top: 28px; padding-bottom: 92px; }} .jj-hero.jj-flat {{ padding-bottom: 30px; }} .jj-hero-mascot {{ display: none; }}
  .jj-steps {{ border-radius: 20px; display: flex !important; }} .jj-steps li[aria-hidden] {{ display: none !important; }}
  .jj-hero h1 {{ font-size: 1.4rem !important; }} }}
</style>"""


def header(home: str = "./") -> str:
    """머리 왼쪽: 감귤 로고 + 서비스 이름. 누르면 처음 화면으로(새로 시작). home: 돌아갈 주소(글자 크기 등 유지)."""
    face = mascot(44, "")
    return (f'<a href="{esc(home)}" target="_self" aria-label="민원똑딱 처음 화면으로" title="처음 화면으로" '
            f'style="display:flex;align-items:center;gap:12px;text-decoration:none;color:inherit">{face}'
            f'<div style="display:flex;flex-direction:column;gap:0">'
            f'<div style="font-family:\'Jua\',sans-serif;font-size:1.4rem;line-height:1.2;color:{INK}">민원똑딱</div>'
            f'<div style="font-size:0.78rem;font-weight:500;color:{MUTED}">제주말로 말해도 근거가 붙은 민원서를 만들어 드립니다</div>'
            f'</div></a>')


_SPARKS = [(14, 10, 14, 0.9), (146, 22, 10, 0.75), (156, 120, 16, 0.85), (4, 128, 8, 0.7)]
_BUBBLES = [(-38, 34, 34), (184, 70, 26), (150, -14, 20)]     # 캐릭터 주변 장식 방울 (x, y, 지름)


def hero(text: str, sub: str = "", step: int | None = None, flat: bool = False) -> str:
    """제목 띠: 지금 단계 이름·설명 + 단계 표시. step 이 None 이면 단계 표시 없이.
    flat: 아래 카드가 띠 위로 겹쳐 올라오지 않는 화면(안내 글이 먼저 나오는 민원서 화면 등)."""
    sub_html = (f'<p style="margin:10px 0 0;font-size:1.1rem;font-weight:500;line-height:1.55;color:#4A2E0C">{esc(sub)}</p>'
                if sub else "")
    steps = (f'<div class="jj-steps" style="margin-top:24px;background:#FFFFFF;padding:7px 10px;display:inline-flex;'
             f'box-shadow:0 6px 18px rgba(154,65,0,0.16)">{stepper(step)}</div>') if step is not None else ""
    sparks = "".join(f'<span class="jj-spark" style="left:{x}px;top:{y}px;width:{d}px;height:{d}px;opacity:{o}"></span>'
                     for x, y, d, o in _SPARKS)
    bubbles = "".join(f'<span class="jj-bubble" style="left:{x}px;top:{y}px;width:{d}px;height:{d}px"></span>'
                      for x, y, d in _BUBBLES)
    bar = (f'<div class="jj-progress" aria-hidden="true" style="width:{round((step + 1) / len(STEPS) * 100, 2)}%"></div>'
           if step is not None else "")
    return (f'<section class="jj-band jj-hero{" jj-flat" if flat else ""}"><div class="jj-hero-body">'
            f'<div style="flex:1 1 auto;min-width:0">'
            f'<h1 style="margin:0;font-size:2rem;font-weight:800;line-height:1.3;color:{INK}">{esc(text)}</h1>{sub_html}{steps}</div>'
            f'<div class="jj-hero-mascot">{sparks}{bubbles}{mascot(112, "")}</div></div>{bar}</section>')


def site_footer(note: str) -> str:
    """바닥 띠: 안내·자료 출처."""
    return (f'<div role="contentinfo" class="jj-band jj-foot"><div style="display:flex;flex-wrap:wrap;gap:20px 48px;justify-content:space-between">'
            f'<div style="display:flex;align-items:center;gap:10px">{mascot(32, "")}'
            f'<span style="font-family:\'Jua\',sans-serif;font-size:1.15rem;color:{INK}">민원똑딱</span></div>'
            f'<div style="flex:1 1 420px;display:flex;flex-direction:column;gap:6px;font-size:0.88rem;line-height:1.6">'
            f'<p style="margin:0;color:{INK};font-weight:500">{esc(note)}</p>'
            f'<p style="margin:0">근거 자료: 제주데이터허브 · 공공데이터포털 · 제주빅데이터센터 공개 자료로 계산합니다. '
            f'숫자는 자료의 기준일 현재 값입니다.</p>'
            f'<p style="margin:0">개인정보(이름·연락처·주소)는 민원서에 넣지 않고 ○○로 가립니다.</p>'
            f'</div></div></div>')


STEPS = ["불편 쓰기", "내용 확인", "민원서 만들기"]   # 큰 틀 3단계 (되묻기·뜻 고르기·최종 확인 = 내용 확인)


def stepper(now: int) -> str:
    """now: 0·1·2. 지난 단계는 초록 체크, 지금 단계는 검은 알약(주황 번호), 남은 단계는 회색."""
    items = []
    for i, name in enumerate(STEPS):
        if i:
            items.append(f'<li aria-hidden="true" style="width:22px;height:2px;border-radius:2px;background:{ORANGE if i <= now else BORDER_STRONG}"></li>')
        if i < now:
            dot = (f'<span style="width:30px;height:30px;border-radius:50%;background:{GREEN};display:flex;align-items:center;'
                   f'justify-content:center">{CHECK}</span>')
            items.append(f'<li style="display:flex;align-items:center;gap:8px;padding:4px 10px 4px 4px">{dot}'
                         f'<span style="font-size:1.05rem;font-weight:500;color:{MUTED}">{name}</span></li>')
        elif i == now:
            dot = (f'<span style="width:30px;height:30px;border-radius:50%;background:{ORANGE};color:{INK};display:flex;align-items:center;'
                   f'justify-content:center;font-size:1.05rem;font-weight:800">{i + 1}</span>')
            items.append(f'<li aria-current="step" style="display:flex;align-items:center;gap:8px;background:{INK};border-radius:999px;'
                         f'padding:4px 16px 4px 4px">{dot}<span style="font-size:1.05rem;font-weight:700;color:#FFFFFF">{name}</span></li>')
        else:
            dot = (f'<span style="width:30px;height:30px;border-radius:50%;background:{SURF2};color:{MUTED};display:flex;align-items:center;'
                   f'justify-content:center;font-size:1.05rem;font-weight:700">{i + 1}</span>')
            items.append(f'<li style="display:flex;align-items:center;gap:8px;padding:4px 10px 4px 4px">{dot}'
                         f'<span style="font-size:1.05rem;font-weight:500;color:{MUTED}">{name}</span></li>')
    return f'<ol style="list-style:none;margin:0;padding:0;display:flex;flex-wrap:wrap;align-items:center;gap:6px">{"".join(items)}</ol>'


def title(text: str, sub: str = "") -> str:
    sub_html = f'<p style="margin:0;font-size:1.1rem;font-weight:500;color:{MUTED}">{esc(sub)}</p>' if sub else ""
    return (f'<div style="display:flex;flex-direction:column;gap:6px">'
            f'<h1 style="margin:0;font-size:1.8rem;font-weight:700;line-height:1.3;color:{INK}">{esc(text)}</h1>{sub_html}</div>')


def bubble(text: str, sub: str = "") -> str:
    """감귤 캐릭터가 말하는 말풍선. sub: 말풍선 아래 설명 한 줄."""
    face = mascot(60, "")          # 꾸밈 그림(말풍선 글이 내용이다)
    sub_html = f'<p style="margin:0;font-size:0.95rem;font-weight:500;color:{MUTED}">{esc(sub)}</p>' if sub else ""
    return (f'<div style="display:flex;align-items:center;gap:16px">{face}'
            f'<div style="background:#FFFFFF;border:1px solid {BORDER};border-radius:22px;padding:12px 24px;display:flex;'
            f'flex-direction:column;gap:2px;box-shadow:{SHADOW}">'
            f'<h1 style="margin:0;font-size:1.3rem;font-weight:700;line-height:1.35;color:{INK}">{esc(text)}</h1>{sub_html}</div></div>')


def card_head(text: str, note: str = "") -> str:
    """카드 제목 + 옆에 작은 안내(예: '안 골라도 됩니다 · 최대 3곳')."""
    note_html = (f' <span style="font-size:0.88rem;font-weight:500;color:{MUTED};margin-left:4px;white-space:nowrap">{esc(note)}</span>'
                 if note else "")
    return f'<h2 style="margin:0 0 8px;font-size:1.15rem;font-weight:800;line-height:1.35;color:{INK}">{esc(text)}{note_html}</h2>'


def place_tags(names: list[str]) -> str:
    """고른 지역 표시(읽기 전용). 없으면 '고르지 않음'."""
    if not names:
        return f'<p style="margin:0;font-size:0.95rem;color:{MUTED}">아직 고르지 않았어요.</p>'
    spans = "".join(f'<span style="font-size:0.95rem;font-weight:700;color:{INK};background:{SOFT};border:1.5px solid {ORANGE};'
                    f'border-radius:999px;padding:5px 16px">{esc(n)}</span>' for n in names)
    return f'<div style="display:flex;flex-wrap:wrap;gap:8px">{spans}</div>'


def claim_head(kicker: str, text: str) -> str:
    """뜻 고르기 카드 머리: '첫째 말씀' + “주장 문장”."""
    return (f'<p style="margin:0;font-size:0.95rem;font-weight:700;color:{BROWN}">{esc(kicker)}</p>'
            f'<h2 style="margin:2px 0 6px;font-size:1.15rem;font-weight:700;line-height:1.4;color:{INK}">“{esc(text)}”</h2>')


_VERDICT_STYLE = {"supported": (GREEN, "#E4F2E1"), "partial": (BROWN, SOFT)}


def evidence_card(item: dict) -> str:
    """근거 고르기 카드: 판정 · 지표 · 값 · 비교 · 출처 · 기준일(2년 넘으면 '오래된 자료'). 모든 글은 가린다."""
    fg, bg = _VERDICT_STYLE.get(item.get("verdict"), (MUTED, GREY))
    stale = (f'<span style="font-size:0.8rem;font-weight:700;color:{INK};background:#FFFFFF;border:1.5px dashed {INK};'
             f'border-radius:999px;padding:1px 10px">오래된 자료 (2년 넘음)</span>') if item.get("stale") else ""
    srcs = list(dict.fromkeys(f"{x.get('publisher', '')} · {x.get('dataset', '')}".strip(" ·") for x in item.get("sources") or []))
    compare = (f'<p style="margin:0;font-size:0.95rem;line-height:1.5;color:{INK}">{esc(item["region_compare"])}</p>'
               if item.get("region_compare") else "")
    claim = (f'<p style="margin:0;font-size:0.85rem;color:{MUTED}">말씀: “{esc(item["claim"])}”</p>' if item.get("claim") else "")
    return (f'<div style="display:flex;flex-direction:column;gap:6px">'
            f'<span style="align-self:flex-start;font-size:0.85rem;font-weight:700;color:{fg};background:{bg};'
            f'border-radius:999px;padding:3px 12px">판정: {esc(item.get("verdict_ko", ""))}</span>'
            f'<h3 style="margin:0;font-size:1.05rem;font-weight:700;line-height:1.4;color:{INK}">{esc(item.get("indicator", ""))}</h3>'
            f'<p style="margin:2px 0;font-size:1.9rem;font-weight:800;line-height:1.2;color:{VALUE}">{esc(item.get("value_text", ""))}</p>'
            f'{compare}{claim}'
            f'<div style="border-top:1px solid {BORDER};margin-top:4px;padding-top:10px;display:flex;flex-direction:column;gap:3px;font-size:0.85rem;color:{MUTED}">'
            f'<span><b>출처</b> {esc(" / ".join(srcs) or "정보 없음")}</span>'
            f'<span style="display:flex;flex-wrap:wrap;align-items:center;gap:4px 8px"><b>기준일</b> {esc(item.get("as_of") or "정보 없음")}{stale}</span>'
            f'</div></div>')


_NO_EVIDENCE_REASON = {
    "contradicted": "자료와 다름: 자료로 보면 이 지역이 다른 곳보다 나쁜 편은 아니에요.",
    "unverifiable": "확인 불가: 확인할 자료가 부족해요.",
}


def no_evidence_note(items: list[dict]) -> str:
    """넣을 수 있는 근거가 없을 때: 근거 없이도 민원서를 낼 수 있다는 안내 + 찾아본 결과를 한 줄씩."""
    reasons = [r for r in dict.fromkeys(_NO_EVIDENCE_REASON.get(it.get("verdict"), "") for it in items) if r]
    lines = "".join(f'<li style="margin:0">{esc(r)}</li>' for r in reasons)
    found = (f'<ul style="margin:6px 0 0;padding-left:22px;font-size:0.95rem;line-height:1.6;color:{MUTED}">{lines}</ul>'
             if lines else "")
    return (f'<section style="background:#FFFFFF;border:1px solid {BORDER};border-radius:24px;padding:22px 26px;box-shadow:{SHADOW};'
            f'display:flex;flex-direction:column;gap:4px">'
            f'<p style="margin:0;font-size:1.15rem;font-weight:700;color:{INK}">근거 없이도 민원서는 만들 수 있어요.</p>'
            f'<p style="margin:0;font-size:1rem;line-height:1.55;color:{INK}">불편한 점과 바라는 점만으로도 접수됩니다. '
            f'근거 자료 칸에는 숫자를 넣지 않아요.</p>{found}</section>')


def said_card(text: str) -> str:
    return (f'<h2 style="margin:0 0 10px;font-size:1rem;font-weight:700;color:{BROWN}">쓰신 글</h2>'
            f'<p style="margin:0;font-size:1.05rem;line-height:1.6;color:{INK}">“{esc(text)}”</p>')


def understood_card(rows: list[tuple[str, str, bool]]) -> str:
    """rows: (이름, 값, 확인 필요 여부). 값은 사용자 글에서 온 것이므로 가린다(esc)."""
    out = []
    for name, value, check in rows:
        badge = (f'<span style="font-size:0.85rem;font-weight:700;color:{BROWN};background:{SOFT};'
                 f'border-radius:999px;padding:3px 12px">확인 필요</span>') if check else ""
        out.append(f'<div style="background:{SURF};border-radius:14px;padding:9px 18px;display:flex;flex-wrap:wrap;align-items:center;gap:4px 18px">'
                   f'<span style="width:100px;font-size:0.95rem;font-weight:700;color:{MUTED}">{esc(name)}</span>'
                   f'<span style="font-size:1.1rem;font-weight:700;line-height:1.45;color:{INK};flex:1 1 200px;white-space:pre-line">{esc(value) if value else "—"}</span>{badge}</div>')
    return (f'<section style="box-sizing:border-box;height:100%;background:#FFFFFF;border:1px solid {BORDER};border-radius:24px;padding:22px 26px;'
            f'box-shadow:{SHADOW};'
            f'display:flex;flex-direction:column;gap:10px">'
            f'<h2 style="margin:0;font-size:1rem;font-weight:700;color:{BROWN}">이렇게 이해했어요</h2>'
            f'<div style="display:flex;flex-direction:column;gap:8px">{"".join(out)}</div></section>')


def ask_head(text: str, kicker: str = "한 가지만 더 여쭤볼게요") -> str:
    return (f'<p style="margin:0 0 4px;font-size:0.95rem;font-weight:700;color:{BROWN}">{esc(kicker)}</p>'
            f'<h2 style="margin:0;font-size:1.3rem;font-weight:700;line-height:1.3;color:{INK}">{esc(text)}</h2>')


def footnote(text: str, small: bool = False) -> str:
    size, pad = ("0.85rem", "10px 18px") if small else ("1rem", "18px 24px")
    return (f'<p style="margin:0;font-size:{size};font-weight:500;line-height:1.5;color:{INK};background:{SURF};border-radius:16px;'
            f'padding:{pad}">{esc(text)}</p>')


def note_card(title: str, text: str) -> str:
    return (f'<section class="jj-note"><h2 style="margin:0 0 4px;font-size:0.95rem;font-weight:700;color:{INK}">{esc(title)}</h2>'
            f'<p style="margin:0;font-size:0.9rem;line-height:1.55;color:{MUTED}">{esc(text)}</p></section>')


def small_note(text: str) -> str:
    return f'<p style="margin:0;font-size:0.8rem;color:{MUTED}">{esc(text)}</p>'
