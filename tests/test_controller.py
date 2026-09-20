"""Control-layer tests using a stub page (no Flutter host required).

Guards against the Flet 1.0 event-name trap: ``Dropdown`` fires ``on_select``,
not ``on_change`` - wiring the wrong name silently leaves the old resolution.
"""

from io import BytesIO
from types import SimpleNamespace

import flet as ft
from PIL import Image

from controllers.fax_controller import FaxController, camera_supported, is_compact


class StubPage:
    """Just enough of ft.Page for controller wiring, without a session."""

    def __init__(
        self,
        width: float | None = 1200,
        platform=ft.PagePlatform.WINDOWS,
        web: bool = False,
    ):
        self.services: list = []
        self.controls: list = []
        self.width = width
        self.platform = platform
        self.web = web
        self.on_resize = None
        self.updates = 0

    def update(self) -> None:
        self.updates += 1


def make_image(width: int = 64, height: int = 64) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (width, height), (10, 120, 240)).save(buffer, format="PNG")
    return buffer.getvalue()


def test_controller_wires_the_real_flet_events():
    controller = FaxController(StubPage())
    # the dropdown bug: on_change is never fired by Flet 1.0
    assert controller.view.resolution.on_select is not None
    assert controller.view.load_button.on_click is not None
    assert controller.view.scan_button.on_click is not None
    assert controller.view.clear_button.on_click is not None
    assert controller.picker in controller.page.services


def test_build_sets_page_controls():
    page = StubPage()
    controller = FaxController(page)
    controller.build()
    assert len(page.controls) == 1
    assert page.on_resize is not None


async def test_selecting_a_resolution_updates_the_model():
    controller = FaxController(StubPage())
    assert controller.state.resolution == 32

    controller.view.resolution.value = "8"
    await controller.on_resolution_select(None)
    assert controller.state.resolution == 8

    controller.state.load(make_image())
    await controller.on_scan(None)
    assert controller.state.result is not None
    assert controller.state.result.resolution == 8
    assert controller.state.result.cell_count == 64


async def test_changing_resolution_after_scan_rescans():
    controller = FaxController(StubPage())
    controller.state.load(make_image())
    await controller.on_scan(None)
    assert controller.state.result.resolution == 32

    controller.view.resolution.value = "16"
    await controller.on_resolution_select(None)
    assert controller.state.result.resolution == 16


async def test_clear_resets_everything():
    controller = FaxController(StubPage())
    controller.state.load(make_image())
    await controller.on_scan(None)

    await controller.on_clear(None)
    assert not controller.state.has_source
    assert controller.state.result is None


def test_is_compact_platform_and_width():
    assert is_compact(StubPage(width=1400, platform=ft.PagePlatform.WINDOWS)) is False
    assert is_compact(StubPage(width=500, platform=ft.PagePlatform.WINDOWS)) is True
    assert is_compact(StubPage(width=None, platform=ft.PagePlatform.ANDROID)) is True
    assert is_compact(StubPage(width=1000, platform=ft.PagePlatform.ANDROID)) is False
    assert is_compact(StubPage(width=None, platform=None)) is False


def test_camera_supported_platforms_only():
    assert camera_supported(StubPage(platform=ft.PagePlatform.ANDROID)) is True
    assert camera_supported(StubPage(platform=ft.PagePlatform.IOS)) is True
    assert camera_supported(StubPage(platform=ft.PagePlatform.WINDOWS, web=True)) is True
    # desktop is not supported: mounting the control there would raise
    assert camera_supported(StubPage(platform=ft.PagePlatform.WINDOWS)) is False
    assert camera_supported(StubPage(platform=ft.PagePlatform.MACOS)) is False
    assert camera_supported(StubPage(platform=ft.PagePlatform.LINUX)) is False


def test_camera_not_mounted_on_desktop():
    controller = FaxController(StubPage())
    assert controller.camera is None
    assert controller.view.camera_panel is None
    assert controller.view.camera_button.disabled is True
    assert controller.view.camera_button.tooltip  # explains why


def test_camera_created_on_supported_platforms():
    for page in (
        StubPage(web=True),
        StubPage(platform=ft.PagePlatform.ANDROID),
        StubPage(platform=ft.PagePlatform.IOS),
    ):
        controller = FaxController(page)
        assert controller.camera is not None
        assert controller.view.camera_panel is not None
        assert controller.view.camera_button.disabled is False


async def test_camera_panel_stays_mounted_while_panes_toggle():
    controller = FaxController(StubPage(web=True))
    controller.build()
    assert controller.view.body_container is not None
    # The preview must never be unmounted: flet-camera disposes its controller
    # when the widget leaves the tree, which killed the second "Take photo".
    assert controller.view.camera_panel.visible is True

    controller.view.set_camera_open(True)
    assert controller.view.camera_panel.visible is True
    assert controller.view.body_container.visible is False

    await controller.on_close_camera(None)
    assert controller.view.camera_panel.visible is True
    assert controller.view.body_container.visible is True


def test_camera_state_updates_readiness_and_reports_errors():
    controller = FaxController(StubPage(web=True))

    controller.on_camera_state(
        SimpleNamespace(is_initialized=True, has_error=False, error_description=None)
    )
    assert controller._camera_ready is True

    controller.on_camera_state(
        SimpleNamespace(is_initialized=True, has_error=True, error_description="boom")
    )
    assert "boom" in (controller.view.status.value or "")


async def test_capture_without_ready_camera_reports_status():
    controller = FaxController(StubPage(web=True))
    await controller.on_capture(None)
    assert "not ready" in (controller.view.status.value or "")


async def test_show_captured_loads_and_selects_original():
    controller = FaxController(StubPage())
    await controller._show_captured(make_image(), "Loaded test")
    assert controller.state.has_source
    assert "standardised" in controller.view.status.value