"""읽기 전용 SQL 실행.

- DuckDB를 read_only로 연다(쓰기는 DB 단에서도 막힌다).
- SELECT / WITH 로 시작하는 단일 문장만 받는다. DDL·DML·PRAGMA·파일 읽기 함수는 거부한다.
- 결과는 최대 sql_max_rows 행, 실행은 sql_timeout_sec 초를 넘으면 중단한다.
"""
from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from pathlib import Path

import duckdb

ENGINE_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = ENGINE_ROOT / "data" / "jeju.duckdb"

_FORBIDDEN = re.compile(
    r"\b(insert|update|delete|create|drop|alter|attach|detach|copy|export|import|install|load|pragma|set|"
    r"call|checkpoint|vacuum|read_csv\w*|read_parquet|read_json\w*|glob|httpfs)\b",
    re.IGNORECASE,
)


class SQLRejected(ValueError):
    """읽기 전용 규칙 위반."""


class SQLTimeout(RuntimeError):
    """제한 시간 초과."""


@dataclass
class Result:
    columns: list[str]
    rows: list[tuple]
    truncated: bool


def check_sql(sql: str) -> str:
    body = re.sub(r"--[^\n]*|/\*.*?\*/", " ", sql, flags=re.DOTALL).strip().rstrip(";").strip()
    if ";" in body:
        raise SQLRejected("여러 문장은 실행하지 않는다")
    if not re.match(r"(?is)^(select|with)\b", body):
        raise SQLRejected("SELECT 또는 WITH 로 시작하는 조회만 허용")
    stripped = re.sub(r"'(?:[^']|'')*'|\"(?:[^\"]|\"\")*\"", "''", body)   # 문자열·식별자 안의 단어는 무시
    hit = _FORBIDDEN.search(stripped)
    if hit:
        raise SQLRejected(f"허용하지 않는 키워드: {hit.group(0)}")
    return body


class SQLRunner:
    def __init__(self, db_path: Path = DEFAULT_DB, max_rows: int = 1000, timeout_sec: float = 10):
        self.con = duckdb.connect(str(db_path), read_only=True)
        self.max_rows, self.timeout_sec = max_rows, timeout_sec

    def run(self, sql: str, params: list | dict | None = None) -> Result:
        body = check_sql(sql)
        timer = threading.Timer(self.timeout_sec, self.con.interrupt)
        timer.start()
        try:
            cur = self.con.execute(body, params or [])
            rows = cur.fetchmany(self.max_rows + 1)
            columns = [d[0] for d in cur.description]
        except duckdb.InterruptException as exc:
            raise SQLTimeout(f"{self.timeout_sec}초 초과로 중단") from exc
        finally:
            timer.cancel()
        return Result(columns, rows[: self.max_rows], len(rows) > self.max_rows)

    def close(self) -> None:
        self.con.close()
