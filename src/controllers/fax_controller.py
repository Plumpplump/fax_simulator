"""Control layer: wires the model and view together and handles all events."""

from __future__ import annotations

import asyncio
from pathlib import Path

import flet as ft
import flet_camera as fc

from models.fax import FaxState
from views.fax_view import COMPACT_WIDTH, FaxView

# Platform is only a fallback: a known width always wins so a narrow desktop
# window gets tabs and a wide tablet still gets the panes side by side.
MOBILE_PLATFORMS = {
    ft.PagePlatform.ANDROID,
    ft.PagePlatform.ANDROID_TV,
    ft.PagePlatform.IOS,
}

# flet-camera raises FletUnsupportedPlatformException anywhere else (see
# flet_camera.camera.Camera.before_update), so we never mount it on desktop.
CAMERA_PLATFORMS = {ft.PagePlatform.ANDROID, ft.PagePlatform.IOS}


def is_compact(page: ft.Page, width: float | None = None) -> bool:
    """``True`` -> tabbed layout, ``False`` -> two panes side by side."""
    measured = width if width is not None else page.width
    if measured is not None:
        return measured < COMPACT_WIDTH
    return page.platform in MOBILE_PLATFORMS


def camera_supported(page: ft.Page) -> bool:
    """flet-camera only works on Web, Android and iOS - never desktop."""
    return bool(getattr(page, "web", False)) or page.platform in CAMERA_PLATFORMS


class FaxController:
    """Owns the model, the view, the ``FilePicker`` service and the camera."""

    def __init__(self, page: ft.Page) -> None:
        self.page = page
        self.state = FaxState()

        self.camera = (
            fc.Camera(expand=True, preview_enabled=True)
            if camera_supported(page)
            else None
        )
        self.view = FaxView(camera=self.camera)
        self._camera_ready = False
        if self.camera is not None:
            self.camera.on_state_change = self.on_camera_state

        self.picker = ft.FilePicker()
        page.services.append(self.picker)

        self._compact: bool | None = None

        self.view.load_button.on_click = self.on_load
        self.view.scan_button.on_click = self.on_scan
        self.view.clear_button.on_click = self.on_clear
        self.view.camera_button.on_click = self.on_open_camera
        self.view.capture_button.on_click = self.on_capture
        self.view.close_camera_button.on_click = self.on_close_camera
        # Flet 1.0 renamed the dropdown event: it is `on_select`, NOT `on_change`.
        self.view.resolution.on_select = self.on_resolution_select

    # ------------------------------------------------------------- lifecycle
    def build(self) -> None:
        """Create the initial layout and start listening for resize events."""
        self._compact = is_compact(self.page)
        self.page.on_resize = self.on_resize
        self._render()

    def _render(self) -> None:
        assert self._compact is not None
        self.page.controls = [self.view.build(self._compact)]
        self._sync_view()
        self.page.update()

    def _sync_view(self) -> None:
        result_bytes = self.state.result.image_bytes if self.state.result else None
        self.view.resolution.value = str(self.state.resolution)
        self.view.set_source(self.state.source_bytes)
        self.view.set_result(result_bytes)
        self.view.set_enabled(self.state.has_source, self.state.result is not None)

    # ---------------------------------------------------------------- events
    async def on_load(self, e: ft.Event) -> None:
        files = await self.picker.pick_files(
            dialog_title="Choose an image",
            file_type=ft.FilePickerFileType.IMAGE,
            with_data=True,
        )
        if not files:
            return

        picked = files[0]
        data = picked.bytes
        if data is None and picked.path:
            data = Path(picked.path).read_bytes()

        if not data:
            self.view.set_status("Could not read that file.")
            self.page.update()
            return

        await self._show_captured(
            data, f"Loaded {picked.name} ({len(data) // 1024} KB)"
        )

    async def on_scan(self, e: ft.Event) -> None:
        if not self.state.has_source:
            return

        result = self.state.scan()
        self.view.set_result(result.image_bytes)
        self.view.set_enabled(True, True)
        self.view.set_status(
            f"Scanned at {result.resolution} x {result.resolution} "
            f"({result.cell_count} cells)."
        )
        await self.view.select_tab(1)
        self.page.update()

    async def on_clear(self, e: ft.Event) -> None:
        self.state.clear()
        self._sync_view()
        await self.view.select_tab(0)
        self.view.set_status("Cleared.")
        self.page.update()

    async def on_resolution_select(self, e: ft.Event) -> None:
        value = self.view.resolution.value
        if value:
            self.state.resolution = int(value)
        # Keep an existing scan consistent with the newly selected grid.
        if self.state.result is not None:
            await self.on_scan(e)

    # ----------------------------------------------------------------- camera
    async def on_open_camera(self, e: ft.Event) -> None:
        if self.camera is None:
            return

        self.view.set_camera_open(True)
        self.page.update()

        if not self._camera_ready:
            await self._ensure_camera()
        elif not await self._resume_preview():
            # Preview controller is gone; recreate it.
            self._camera_ready = False
            await self._ensure_camera()

        if self._camera_ready:
            self.view.set_status("Camera ready - press Capture.")
        self.page.update()

    def on_camera_state(self, e: fc.CameraStateEvent) -> None:
        """Keep readiness in sync and surface camera errors instead of silence."""
        if e.is_initialized:
            self._camera_ready = True
        if e.has_error and e.error_description:
            self.view.set_status(f"Camera error: {e.error_description}")
            self.page.update()

    async def on_capture(self, e: ft.Event) -> None:
        if self.camera is None or not self._camera_ready:
            self.view.set_status("Camera is not ready yet.")
            self.page.update()
            return

        try:
            data = await self.camera.take_picture()
        except Exception as exc:  # noqa: BLE001 - surface any capture failure
            self.view.set_status(f"Capture failed: {exc}")
            self.page.update()
            return

        if not data:
            self.view.set_status("The camera returned no image.")
            self.page.update()
            return

        self.view.set_camera_open(False)
        await self._pause_preview()
        await self._show_captured(data, "Photo captured")

    async def on_close_camera(self, e: ft.Event) -> None:
        self.view.set_camera_open(False)
        await self._pause_preview()
        self.page.update()

    async def _ensure_camera(self) -> None:
        """Find and initialize the first camera, retrying until it is mounted."""
        if self.camera is None or self._camera_ready:
            return

        for _ in range(12):
            try:
                cameras = await self.camera.get_available_cameras()
            except Exception:  # not mounted yet, or no client connected
                await asyncio.sleep(0.5)
                continue

            if not cameras:
                self.view.set_status("No camera found on this device.")
                return

            try:
                await self.camera.initialize(
                    description=cameras[0],
                    resolution_preset=fc.ResolutionPreset.HIGH,
                    enable_audio=False,
                    image_format_group=fc.ImageFormatGroup.JPEG,
                )
                # Mobile only (see the official example): keep captured photos
                # upright; web raises, so it is guarded by platform.
                if self.page.platform in CAMERA_PLATFORMS:
                    try:
                        await self.camera.lock_capture_orientation()
                    except Exception:
                        pass
                self._camera_ready = True
                return
            except Exception as exc:  # noqa: BLE001
                self.view.set_status(f"Could not start the camera: {exc}")
                return

        self.view.set_status("Camera did not become available.")

    async def _pause_preview(self) -> None:
        if self.camera is not None and self._camera_ready:
            try:
                await self.camera.pause_preview()
            except Exception:
                pass

    async def _resume_preview(self) -> bool:
        if self.camera is None:
            return False
        try:
            await self.camera.resume_preview()
            return True
        except Exception:
            return False

    # ----------------------------------------------------------------- shared
    async def _show_captured(self, data: bytes, label: str) -> None:
        """Standardize new image bytes, then refresh the panes and switch tab."""
        try:
            self.state.load(data)
        except Exception as exc:  # corrupt or unsupported image
            self.view.set_status(f"Could not open image: {exc}")
            self.page.update()
            return

        self._sync_view()
        await self.view.select_tab(0)
        self.view.set_status(f"{label}; standardised to a 512 px square.")
        self.page.update()

    def on_resize(self, e: ft.PageResizeEvent) -> None:
        compact = is_compact(self.page, width=e.width)
        if compact == self._compact:
            return
        self._compact = compact
        self._render()