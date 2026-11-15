"""python -m complaint_agent "민원 문장" [--out handoff.json] [--mock-engine]"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from complaint_agent.conversation import run_conversation
from complaint_agent.pipeline import Agent


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="민원 말해봅서: 되묻기 → 뜻 선택 → 근거 패키지")
    ap.add_argument("text", nargs="?")
    ap.add_argument("--out", type=Path, help="민원 초안 모델에 넘길 JSON(가린 질의 + 근거 패키지) 저장 경로")
    ap.add_argument("--mock-engine", action="store_true", help="근거 DB 없이 형식만(계산값 아님)")
    args = ap.parse_args(argv)
    text = args.text or input("불편한 점을 말씀해 주세요: ")
    agent = Agent(mock_engine=True if args.mock_engine else None)
    try:
        out = run_conversation(text, agent=agent)
    finally:
        agent.close()
    if out is None:
        return 1
    if args.out:
        args.out.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"저장: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
