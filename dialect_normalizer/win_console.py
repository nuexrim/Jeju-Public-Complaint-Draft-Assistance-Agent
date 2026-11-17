"""한국어 Windows에서 출력이 멈추지 않게 한다.

Windows의 Python 3.11은 출력을 파일·파이프로 보내거나 다른 프로그램(UI 등)이 받아 갈 때 CP949로 쓴다.
CP949에 없는 글자(—, ✅, 🚨 등)를 print 하면 UnicodeEncodeError로 프로그램이 멈춘다(콘솔 창에 바로 쓸 때는 괜찮음).
명령 실행 진입점(main)에서 safe_output()을 부르면, 그런 글자만 '?'로 바꿔 쓰고 계속 진행한다.
UTF-8로 쓰고 있으면(맥, 콘솔 창, PYTHONUTF8=1) 아무것도 바꾸지 않는다.
"""
from __future__ import annotations

import sys


def safe_output() -> None:
    for stream in (sys.stdout, sys.stderr):
        enc = (getattr(stream, "encoding", "") or "").lower().replace("-", "")
        if enc and enc != "utf8" and hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
