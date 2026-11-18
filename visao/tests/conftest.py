import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from plasmavision.config import carregar_config  # noqa: E402


@pytest.fixture
def cfg(tmp_path):
    return carregar_config(None, {"dados": str(tmp_path / "dados")})
