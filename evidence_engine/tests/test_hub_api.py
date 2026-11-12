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
    assert "SECRETKEY" not in str(info.value) and "403" in str(info.value)


def test_write_csv_union_of_columns(tmp_path):
    path = tmp_path / "x.csv"
    cols = hub_api.write_csv([{"a": 1}, {"a": 2, "b": {"k": "v"}}], path)
    assert cols == ["a", "b"]
    assert path.read_text(encoding="utf-8").splitlines()[2] == '2,"{""k"": ""v""}"'
