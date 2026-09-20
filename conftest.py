"""Test configuration.

The integration test (``tests/test_main.py``) drives a real Flutter test host,
which needs a desktop Flutter device toolchain (Visual Studio C++ on Windows).
It is opt-in so a plain ``uv run pytest`` stays fast and does not spend minutes
building a host that cannot run: use ``flet test``, or install the toolchain and
set ``FLET_RUN_INTEGRATION=1``.
"""

import os

import pytest

_INTEGRATION_ENABLED = (
    os.environ.get("FLET_RUN_INTEGRATION") == "1"
    or bool(os.environ.get("FLET_TEST_FLUTTER_APP_DIR"))
)


def pytest_collection_modifyitems(config, items):
    if _INTEGRATION_ENABLED:
        return
    skip = pytest.mark.skip(
        reason=(
            "integration test needs a Flutter device toolchain; "
            "run `flet test` or set FLET_RUN_INTEGRATION=1"
        )
    )
    for item in items:
        if item.get_closest_marker("integration") is not None:
            item.add_marker(skip)