import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
TESTS = ROOT / "tests"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(TESTS) not in sys.path:
    sys.path.insert(0, str(TESTS))

from helpers import MINIMAL_OPENAPI_30, MINIMAL_SWAGGER_20


@pytest.fixture
def minimal_openapi_30():
    return dict(MINIMAL_OPENAPI_30)


@pytest.fixture
def minimal_swagger_20():
    return dict(MINIMAL_SWAGGER_20)


@pytest.fixture
def minimal_openapi_30_json():
    return json.dumps(MINIMAL_OPENAPI_30)
