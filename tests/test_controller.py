"""Control-layer tests using a stub page (no Flutter host required).

Guards against the Flet 1.0 event-name trap: ``Dropdown`` fires ``on_select``,
not ``on_change`` - wiring the wrong name silently leaves the old resolution.
"""

from io import BytesIO
from datetime import datetime
from types import SimpleNamespace

import flet as ft
from PIL import Image

from controllers.fax_controller import FaxController, camera_supported, is_compact
from models.fax import ReceivedFax
from services.fax_net import FaxPage


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
        self.on_disconnect = None
        self.updates = 0
        # Background tasks and dialogs requested by the controller.
        self.tasks: list = []
        self.dialogs: list = []

    def update(self) -> None:
        self.updates += 1

    def run_task(self, handler, *args, **kwargs):
        # In a real Flet app this schedules `handler` on the page's event loop.
        # For tests we just remember it, so nothing runs unexpectedly.
        self.tasks.append(handler)
        return None

    def show_dialog(self, dialog) -> None:
        self.dialogs.append(dialog)

    def pop_dialog(self):
        return self.dialogs.pop() if self.dialogs else None


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
    assert controller.view.send_button.on_click is not None
    assert controller.view.save_button.on_click is not None
    assert controller.view.settings_button.on_click is not None
    assert controller.view.peer_dropdown.on_select is not None
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


# --------------------------------------------------------------- transceiver
def test_images_start_hidden_until_they_have_data():
    # Flet 1.0 rejects an Image with src=None unless it is hidden, so every
    # empty pane must start invisible (this regressed once in the inbox pane).
    controller = FaxController(StubPage())
    assert controller.view.original_image.visible is False
    assert controller.view.scanned_image.visible is False
    assert controller.view.received_image.visible is False


def test_build_schedules_the_network_listener():
    page = StubPage()
    controller = FaxController(page)
    controller.build()
    # The listener and discovery are started as background tasks, not inline.
    assert controller._start_server in page.tasks
    assert controller._start_discovery in page.tasks
    assert page.on_disconnect is not None


def test_peer_selection_fills_the_recipient_field():
    controller = FaxController(StubPage())
    controller.view.peer_dropdown.value = "192.168.1.9:9100"
    controller.on_peer_select(None)
    assert controller.view.recipient.value == "192.168.1.9:9100"


def test_discovered_peers_appear_in_and_leave_the_dropdown():
    from p2p import PeerInfo

    controller = FaxController(StubPage())
    info = PeerInfo(host="192.168.1.9", port=9100, name="FAX-B")

    controller._on_peer_found(info)
    values = [option.key for option in controller.view.peer_dropdown.options]
    assert values == ["192.168.1.9:9100"]

    controller._on_peer_lost(info)
    assert controller.view.peer_dropdown.options == []


def test_file_picker_is_held_by_two_references():
    # Flet 1.0 prunes page services whose refcount is too low after each event;
    # with a single reference the FilePicker gets dropped and later Load/Save
    # calls fail with "Timeout waiting for invoke method listener".
    controller = FaxController(StubPage())
    assert controller._service_refs[0] is controller.picker


def test_picker_is_re_registered_if_flet_removed_it():
    page = StubPage()
    controller = FaxController(page)
    page.services.clear()  # simulate Flet dropping the service
    controller._ensure_picker_registered()
    assert controller.picker in page.services


async def test_send_requires_a_scan_first():
    controller = FaxController(StubPage())
    controller.view.recipient.value = "192.168.1.20:9100"
    await controller.on_send(None)
    assert "Scan" in (controller.view.status.value or "")


async def test_send_with_a_bad_port_reports_a_friendly_error():
    controller = FaxController(StubPage())
    controller.state.load(make_image())
    await controller.on_scan(None)
    controller.view.recipient.value = "192.168.1.20:notaport"
    await controller.on_send(None)
    assert "Invalid address" in (controller.view.status.value or "")


async def test_receive_complete_populates_the_inbox():
    controller = FaxController(StubPage())
    controller.build()

    page_obj = FaxPage(
        station="555-0100",
        sent_at="2026-10-04 12:00",
        width=64,
        height=64,
        resolution=8,
        image=Image.new("L", (64, 64), 200),
    )
    peer = SimpleNamespace(host_port="192.168.1.9:9100")

    await controller._on_receive_complete(peer, page_obj)

    assert controller.state.received is not None
    assert controller.state.received.station == "555-0100"
    assert controller.view.received_image.src is not None
    assert controller.view.save_button.disabled is False
    assert controller.view.received_progress.visible is False  # hidden when done


async def test_save_received_passes_its_bytes_to_the_picker():
    controller = FaxController(StubPage())
    captured: dict = {}

    async def fake_save(**kwargs):
        captured.update(kwargs)
        return "C:/tmp/fax.png"

    controller.picker.save_file = fake_save  # monkeypatch the service call
    controller.state.received = ReceivedFax(
        station="555-0100",
        resolution=8,
        width=4,
        height=4,
        image_bytes=b"PNGDATA",
        received_at=datetime(2026, 1, 1, 12, 0, 0),
    )

    await controller.on_save_received(None)

    assert captured["src_bytes"] == b"PNGDATA"
    assert captured["file_name"].startswith("fax-from-555-0100-")
    assert "Saved" in (controller.view.status.value or "")


async def test_open_settings_shows_the_dialog():
    page = StubPage()
    controller = FaxController(page)
    await controller.on_open_settings(None)
    assert controller.view.settings_dialog in page.dialogs


async def test_apply_settings_updates_the_controller():
    page = StubPage()
    controller = FaxController(page)
    controller.build()

    controller.view.station_field.value = "555-0199"
    controller.view.port_field.value = "9200"
    controller.view.speed_field.value = "25"
    controller.view.receive_switch.value = False  # avoid touching real ports

    await controller.on_apply_settings(None)

    assert controller.station == "555-0199"
    assert controller.listen_port == 9200
    assert controller.line_delay == 0.025
    assert controller.receive_enabled is False