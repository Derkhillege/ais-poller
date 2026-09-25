from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def sample_ndjson() -> Path:
    """First 3 poll ticks of the trimmed data.json test set (727 features)."""
    return FIXTURES / "sample.ndjson"
