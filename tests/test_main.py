"""Integration smoke test for the real app.

The ``flet_app`` fixture is injected by the flet pytest plugin (see
``flet/pytest_plugin.py``); never define your own.

Marked ``integration``: it drives a provisioned Flutter test host and is skipped
unless run via ``flet test`` or with ``FLET_RUN_INTEGRATION=1`` (see
``conftest.py``). The fast suite is ``tests/test_model.py`` +
``tests/test_controller.py``.
"""

import flet.testing as ftt
import pytest

pytestmark = pytest.mark.integration


async def test_app_mounts_controls(flet_app: ftt.FletTestApp):
    tester = flet_app.tester
    await tester.pump_and_settle()

    assert (await tester.find_by_text("Fax Machine Simulator")).count == 1
    assert (await tester.find_by_key("load_button")).count == 1
    assert (await tester.find_by_key("scan_button")).count == 1
    assert (await tester.find_by_key("clear_button")).count == 1
    assert (await tester.find_by_key("resolution_dropdown")).count == 1
    # both panes are mounted (side by side on the wide test host)
    assert (await tester.find_by_key("original_image")).count == 1
    assert (await tester.find_by_key("scanned_image")).count == 1