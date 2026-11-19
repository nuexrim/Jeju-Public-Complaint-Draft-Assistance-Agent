"""결과 설명자료 PDF 만들기: 결과_설명자료.md → 결과_설명자료.pdf (A4, 여백 2.54cm)

실행 프로그램과 따로 쓰는 문서 도구. 필요한 것은 Google Chrome과 아래 두 패키지.

    python3 -m venv .venv-docs
    .venv-docs/bin/pip install playwright==1.48.0 markdown==3.7
    .venv-docs/bin/python docs/report_pdf.py

- 그림은 docs/report_images/ 의 파일을 그대로 씀
- 흐름도(mermaid)와 글꼴은 인터넷에서 받아 그리므로 인터넷 연결 필요
"""
from __future__ import annotations

import asyncio
import re
import tempfile
from pathlib import Path

import markdown
from markdown.extensions.toc import TocExtension, slugify_unicode
from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "결과_설명자료.md"
OUT = ROOT / "결과_설명자료.pdf"
MARGIN = "25.4mm"                      # 상하좌우 2.54cm (1인치)

CSS = """
* { box-sizing: border-box; }
body { font-family: 'Noto Sans KR', 'Apple SD Gothic Neo', sans-serif; color: #1F2326; font-size: 10pt; line-height: 1.65; margin: 0; }
h1 { font-size: 22pt; margin: 0 0 6mm; padding: 9mm 8mm; color: #1F2326;
  background: linear-gradient(115deg, #FFC45C 0%, #FFAA33 45%, #F7941D 100%); }
h1 + p { font-size: 11pt; font-weight: 700; margin-top: 0; }
h2 { font-size: 15pt; margin: 9mm 0 3mm; padding-bottom: 2mm; border-bottom: 2.5px solid #F7941D; break-after: avoid; }
h3 { font-size: 11.5pt; margin: 6mm 0 2mm; color: #9A4100; break-after: avoid; }
h2[id^="5-"], h2[id^="7-"] { break-before: page; }
p { margin: 1.5mm 0; }
ul, ol { margin: 1.5mm 0; padding-left: 5mm; } li { margin: 0.6mm 0; }
table { width: 100%; border-collapse: collapse; margin: 3mm 0; font-size: 8.8pt; break-inside: avoid; }
th { background: #FFF1DE; color: #1F2326; font-weight: 700; text-align: left; }
th, td { border: 1px solid #E3E6EA; padding: 1.8mm 2.4mm; vertical-align: top; }
tr:nth-child(even) td { background: #FAFBFC; }
code { font-family: 'SF Mono', Menlo, monospace; font-size: 8.5pt; background: #F4F5F7; padding: 0 1mm; }
img { display: block; max-width: 100%; max-height: 88mm; margin: 4mm auto 1mm; break-inside: avoid; }
p:has(> img) { break-inside: avoid; break-after: avoid; }
.cap { text-align: center; font-size: 8.5pt; color: #4A5157; margin: 0 0 4mm; }
.mermaid { display: flex; justify-content: center; margin: 4mm 0; break-inside: avoid; }
.mermaid svg { max-height: 140mm; width: auto !important; }
a { color: #9A4100; text-decoration: none; }
h2#목차 + ol a { color: #1F2326; }
"""

FOOTER = ('<div style="width:100%;font-size:8px;color:#888;text-align:center;font-family:sans-serif">'
          '민원똑딱 결과 설명자료 &nbsp;|&nbsp; <span class="pageNumber"></span> / <span class="totalPages"></span></div>')


def build_html() -> str:
    src = SRC.read_text(encoding="utf-8")
    src = re.sub(r"```mermaid\n(.*?)```", lambda m: '<div class="mermaid">\n' + m.group(1) + "</div>", src, flags=re.S)
    body = markdown.markdown(src, extensions=["tables", "fenced_code", TocExtension(slugify=slugify_unicode)])
    body = body.replace('src="docs/', f'src="{(ROOT / "docs").as_uri()}/')
    body = re.sub(r"<p>(그림 \d+\..*?)</p>", r'<p class="cap">\1</p>', body)      # 그림 설명
    return f"""<!doctype html><html lang="ko"><head><meta charset="utf-8">
<link href="https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@400;500;700;800&display=swap" rel="stylesheet">
<style>{CSS}</style>
<script src="https://cdn.jsdelivr.net/npm/mermaid@10.9.1/dist/mermaid.min.js"></script>
</head><body>{body}
<script>mermaid.initialize({{startOnLoad:true, theme:'base', themeVariables:{{primaryColor:'#FFF1DE',
  primaryBorderColor:'#F7941D', lineColor:'#9A4100', fontFamily:'Noto Sans KR'}}, flowchart:{{useMaxWidth:true}}}});</script>
</body></html>"""


async def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        html = Path(tmp) / "report.html"
        html.write_text(build_html(), encoding="utf-8")
        async with async_playwright() as p:
            browser = await p.chromium.launch(channel="chrome", headless=True)
            page = await browser.new_page()
            await page.goto(html.as_uri())
            await page.wait_for_timeout(4000)                     # 글꼴과 흐름도 그리기 대기
            broken = await page.evaluate("[...document.querySelectorAll('a[href^=\"#\"]')]"
                                         ".filter(a => !document.getElementById(decodeURIComponent(a.getAttribute('href').slice(1))))"
                                         ".map(a => a.getAttribute('href'))")
            if broken:
                print("목차 링크가 제목과 맞지 않음:", broken)
            await page.pdf(path=str(OUT), format="A4", print_background=True,
                           margin={"top": MARGIN, "bottom": MARGIN, "left": MARGIN, "right": MARGIN},
                           display_header_footer=True, header_template="<div></div>", footer_template=FOOTER)
            await browser.close()
    print("저장:", OUT)


if __name__ == "__main__":
    asyncio.run(main())
