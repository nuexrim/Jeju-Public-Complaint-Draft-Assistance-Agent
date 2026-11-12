import os
import sys
from pathlib import Path

import pytest

ENGINE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ENGINE_ROOT))


def pytest_collection_modifyitems(config, items):
    if os.environ.get("ALLOW_PAID_CALLS") == "1":
        return
    skip = pytest.mark.skip(reason="유료 호출 테스트: ALLOW_PAID_CALLS=1 필요 (사람이 직접 설정)")
    for item in items:
        if "paid" in item.keywords:
            item.add_marker(skip)
