import json

import pytest

from data.ingest import hub_api


@pytest.mark.parametrize("body, n", [
    ([{"a": 1}, {"a": 2}], 2),
    ({"result": "success", "data": [{"a": 1}]}, 1),
    ({"response": {"body": {"items": [{"a": 1}, {"a": 2}, {"a": 3}]}}}, 3),   # 중첩된 dict 안의 목록도 찾는다
    ({"response": {"items": [{"a": 1}]}}, 1),
    ({"result": "fail", "message": "x"}, 0),
])
def test_extract_rows(body, n):
    assert len(hub_api.extract_rows(body)) == n


def test_load_key_reads_env_file_without_quotes(tmp_path, monkeypatch):
    monkeypatch.delenv("JEJUDATAHUB_PROJECT_KEY", raising=False)
    env = tmp_path / ".env"
    env.write_text('# c\nJEJUDATAHUB_PROJECT_KEY="abc123"\n', encoding="utf-8")
    assert hub_api.load_key(env) == "abc123"
    env.write_text("JEJUDATAHUB_PROJECT_KEY=\n", encoding="utf-8")
    with pytest.raises(RuntimeError):
        hub_api.load_key(env)


def test_http_error_message_never_contains_key(monkeypatch):
    import urllib.error

    def boom(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 403, "Forbidden", {}, None)

    monkeypatch.setattr(hub_api.urllib.request, "urlopen", boom)
    with pytest.raises(RuntimeError) as info:
        hub_api.fetch("DATAKEY", "SECRETKEY")
    assert "SECRETKEY" not in str(info.value)
    assert "SECRETKEY" not in str(info.value) and "403" in str(info.value)


def test_write_csv_union_of_columns(tmp_path):
    path = tmp_path / "x.csv"
    cols = hub_api.write_csv([{"a": 1}, {"a": 2, "b": {"k": "v"}}], path)
    assert cols == ["a", "b"]
    assert path.read_text(encoding="utf-8").splitlines()[2] == '2,"{""k"": ""v""}"'


class _Resp:
    def __init__(self, body):
        self.body = json.dumps(body).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self.body


def _paged(pages, total=None):
    calls = []

    def fake(request, timeout):
        number = int(request.full_url.rsplit("number=", 1)[1])
        calls.append(number)
        data = pages[number - 1]
        return _Resp({"totCnt": total if total is not None else sum(map(len, pages)), "hasMore": number < len(pages), "data": data})

    return fake, calls


def test_fetch_follows_pages_until_has_more_false(monkeypatch):
    fake, calls = _paged([[{"a": 1}, {"a": 2}], [{"a": 3}]])
    monkeypatch.setattr(hub_api.urllib.request, "urlopen", fake)
    body = hub_api.fetch("D", "K")
    assert calls == [1, 2] and body["totCnt"] == 3 and [r["a"] for r in body["data"]] == [1, 2, 3]


def test_fetch_rejects_count_mismatch(monkeypatch):
    fake, _ = _paged([[{"a": 1}]], total=5)
    monkeypatch.setattr(hub_api.urllib.request, "urlopen", fake)
    with pytest.raises(RuntimeError, match="건수 불일치"):
        hub_api.fetch("D", "K")
