"""Control layer: wires the model and view together and handles all events."""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path

import flet as ft
import flet_camera as fc

from models.fax import FaxState, ReceivedFax
from p2p import PeerDiscovery, PeerInfo, PeerServer
from p2p.errors import P2PError
from services.fax_net import (
    DEFAULT_FAX_PORT,
    DEFAULT_LINE_DELAY,
    FAX_SERVICE,
    FaxReceiver,
    FaxSender,
    compute_local_ip,
    default_station_id,
    parse_address,
)
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
        # Flet 1.0's service registry prunes services whose Python reference
        # count is too low after *every* event (see Page.unregister_services in
        # flet/controls/page.py). A single attribute is not enough: the picker
        # gets dropped, and the next pick_files()/save_file() then fails with
        # "Timeout waiting for invoke method listener". Holding a second strong
        # reference keeps it above that threshold for the whole session.
        self._service_refs: list = [self.picker]

        # ---------------------------------------------------------- networking
        # Our "fax number", the port we listen on, and the transmission speed.
        # All three are editable later in the Settings dialog.
        self.station = default_station_id()
        self.listen_port = DEFAULT_FAX_PORT
        self.line_delay = DEFAULT_LINE_DELAY
        self.receive_enabled = True
        self._server_running = False
        self._receiving = False

        # The generic p2p listener, plus the fax-specific interpreter of its
        # messages. ``on_message`` may be async, which lets us touch the UI.
        self.server = PeerServer(
            host="0.0.0.0",
            port=self.listen_port,
            on_message=self._on_peer_message,
            on_error=self._on_peer_error,
            on_disconnect=self._on_peer_disconnect,
        )
        self.receiver = FaxReceiver(
            on_progress=self._on_receive_progress,
            on_complete=self._on_receive_complete,
            on_error=self._on_receive_error,
        )

        # Automatic LAN discovery: shout our presence and collect the shouts of
        # other fax machines so the user can pick one from a dropdown.
        self._peers: dict[str, PeerInfo] = {}
        self.discovery = PeerDiscovery(
            FAX_SERVICE,
            port=self.listen_port,
            name=self.station,
            on_peer=self._on_peer_found,
            on_lost=self._on_peer_lost,
        )

        self._compact: bool | None = None

        self.view.load_button.on_click = self.on_load
        self.view.scan_button.on_click = self.on_scan
        self.view.clear_button.on_click = self.on_clear
        self.view.camera_button.on_click = self.on_open_camera
        self.view.capture_button.on_click = self.on_capture
        self.view.close_camera_button.on_click = self.on_close_camera
        self.view.send_button.on_click = self.on_send
        self.view.save_button.on_click = self.on_save_received
        self.view.settings_button.on_click = self.on_open_settings
        self.view.settings_apply_button.on_click = self.on_apply_settings
        self.view.settings_cancel_button.on_click = self.on_cancel_settings
        self.view.peer_dropdown.on_select = self.on_peer_select
        # Flet 1.0 renamed the dropdown event: it is `on_select`, NOT `on_change`.
        self.view.resolution.on_select = self.on_resolution_select

    # ------------------------------------------------------------- lifecycle
    def build(self) -> None:
        """Create the initial layout and start listening for resize events."""
        self._compact = is_compact(self.page)
        self.page.on_resize = self.on_resize
        # Release the listening port if the client window/browser page goes away.
        self.page.on_disconnect = self._on_page_disconnect
        if self.receive_enabled:
            # Start the network listener as a background task on the page loop.
            self.page.run_task(self._start_server)
        # Discovery runs even when receiving is off - you still want to find peers.
        self.page.run_task(self._start_discovery)
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
        if self.state.received is not None:
            fax = self.state.received
            self.view.set_received(
                fax.image_bytes, f"From {fax.station} · {fax.received_at:%H:%M:%S}"
            )
            self.view.set_save_enabled(True)

    # ---------------------------------------------------------------- events
    def _ensure_picker_registered(self) -> None:
        """Re-attach the FilePicker if Flet dropped it from the page services.

        Belt-and-braces alongside ``_service_refs``: if the root view's service
        list was ever rebuilt, this puts the picker back before we invoke it.
        """
        services = self.page.services
        if self.picker not in services:
            services.append(self.picker)
            self.page.update()

    async def on_load(self, e: ft.Event) -> None:
        self._ensure_picker_registered()
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

    # ------------------------------------------------------------- networking
    async def _start_server(self) -> None:
        """Begin listening for incoming faxes (run once, at startup)."""
        if self._server_running or not self.receive_enabled:
            return
        try:
            port = await self.server.start()
        except OSError as exc:
            # The usual cause: another program - often a second copy of this
            # app - already holds the port.
            self.view.set_status(f"Could not listen on port {self.listen_port}: {exc}")
            self.view.set_network_status(
                f"Offline - port {self.listen_port} unavailable"
            )
            self.page.update()
            return

        self._server_running = True
        self.view.set_network_status(f"Listening on {compute_local_ip()}:{port}")
        self.page.update()

    async def _stop_server(self) -> None:
        await self.server.stop()
        self._server_running = False

    async def _restart_server(self) -> None:
        """Rebuild and restart the listener, e.g. after the port changed."""
        await self._stop_server()
        # A brand new server is simpler (and safer) than mutating a live one.
        self.server = PeerServer(
            host="0.0.0.0",
            port=self.listen_port,
            on_message=self._on_peer_message,
            on_error=self._on_peer_error,
            on_disconnect=self._on_peer_disconnect,
        )
        await self._start_server()

    async def _on_page_disconnect(self, e: ft.Event) -> None:
        # The client went away: give the port back to the operating system.
        await self.discovery.stop()
        if self._server_running:
            await self._stop_server()

    # --------------------------------------------------------------- discovery
    async def _start_discovery(self) -> None:
        """Broadcast our presence and listen for other fax machines on the LAN."""
        if self.discovery.is_running:
            return
        try:
            await self.discovery.start()
        except OSError as exc:
            # Not fatal: the user can still type an address by hand.
            self.view.set_status(f"Peer discovery unavailable: {exc}")
            self.page.update()

    def _on_peer_found(self, info: PeerInfo) -> None:
        self._peers[info.address] = info
        self._refresh_peer_options()
        self.page.update()

    def _on_peer_lost(self, info: PeerInfo) -> None:
        self._peers.pop(info.address, None)
        self._refresh_peer_options()
        self.page.update()

    def _refresh_peer_options(self) -> None:
        """Rebuild the dropdown from the currently known peers."""
        options = [
            (info.address, f"{info.name or 'fax machine'} · {info.address}")
            for info in sorted(
                self._peers.values(), key=lambda p: (p.name.lower(), p.host)
            )
        ]
        self.view.set_peers(options)

    def on_peer_select(self, e: ft.Event) -> None:
        """Copy the chosen peer's address into the "Send to" field."""
        address = self.view.peer_dropdown.value
        if address:
            self.view.recipient.value = address
            self.view.set_status(f"Selected {address}.")
            self.page.update()

    # ----------------------------------------------------------------- receiving
    async def _on_peer_message(self, peer, message) -> None:
        # Every frame goes to the fax receiver; it knows the fax.* kinds.
        await self.receiver.handle(peer, message)

    async def _on_peer_error(self, peer, exc: Exception) -> None:
        self.view.set_status(f"Network error from {peer.host_port}: {exc}")
        self.page.update()

    def _on_peer_disconnect(self, peer) -> None:
        # A peer that vanishes mid-page leaves a half-built canvas behind.
        self.receiver.forget(peer)
        if self._receiving:
            self._receiving = False
            self.view.set_status(f"Connection from {peer.host_port} closed.")
            self.page.update()

    async def _on_receive_progress(
        self, peer, preview_png: bytes, rows_done: int, rows_total: int
    ) -> None:
        # The first line of a new fax: jump to the Received tab so it is visible.
        if not self._receiving:
            self._receiving = True
            await self.view.select_tab(2)
        self.view.set_received(preview_png, f"Receiving from {peer.host_port} ...")
        self.view.set_received_progress(rows_done / rows_total if rows_total else 1.0)
        self.page.update()

    async def _on_receive_complete(self, peer, page) -> None:
        fax = ReceivedFax(
            station=page.station or peer.host_port,
            resolution=page.resolution,
            width=page.width,
            height=page.height,
            image_bytes=page.to_png_bytes(),
            received_at=datetime.now(),
            sender=peer.host_port,
        )
        self.state.received = fax
        self._receiving = False
        self.view.set_received(
            fax.image_bytes, f"From {fax.station} · {fax.received_at:%H:%M:%S}"
        )
        self.view.set_received_progress(1.0)
        self.view.set_save_enabled(True)
        self.view.set_status(
            f"Received a {fax.width}x{fax.height} fax from {fax.station}."
        )
        await self.view.select_tab(2)
        self.page.update()

    async def _on_receive_error(self, peer, exc: Exception) -> None:
        self._receiving = False
        self.view.set_status(f"Incoming fax from {peer.host_port} failed: {exc}")
        self.page.update()

    # ------------------------------------------------------------------- sending
    async def on_send(self, e: ft.Event) -> None:
        if self.state.result is None:
            self.view.set_status("Scan the document before sending it.")
            self.page.update()
            return

        try:
            host, port = parse_address(self.view.recipient.value)
        except ValueError as exc:
            self.view.set_status(f"Invalid address: {exc}")
            self.page.update()
            return

        # Block a double-click while the (possibly slow) transmission runs.
        self.view.send_button.disabled = True
        self.view.set_status(f"Dialing {host}:{port} ...")
        self.page.update()

        sender = FaxSender(station=self.station, speed=self.line_delay)
        try:
            await sender.send(host, port, self.state.result)
        except P2PError as exc:
            self.view.set_status(f"Send failed: {exc}")
        else:
            self.view.set_status(f"Fax sent to {host}:{port}.")
        finally:
            # Re-enable only if there is still a scan to send.
            self.view.send_button.disabled = self.state.result is None
            self.page.update()

    async def on_save_received(self, e: ft.Event) -> None:
        fax = self.state.received
        if fax is None:
            return
        self._ensure_picker_registered()
        # Build a tidy filename: "fax-from-<station>-<date>-<time>.png".
        safe = "".join(c if c.isalnum() else "-" for c in fax.station)[:20] or "fax"
        file_name = f"fax-from-{safe}-{fax.received_at:%Y%m%d-%H%M%S}.png"
        path = await self.picker.save_file(
            dialog_title="Save received fax",
            file_name=file_name,
            # src_bytes is required on web/iOS/Android and written on desktop.
            src_bytes=fax.image_bytes,
            file_type=ft.FilePickerFileType.IMAGE,
        )
        self.view.set_status(f"Saved to {path}" if path else "Save cancelled.")
        self.page.update()

    # ------------------------------------------------------------------ settings
    async def on_open_settings(self, e: ft.Event) -> None:
        self.view.set_settings_values(
            self.station,
            self.listen_port,
            round(self.line_delay * 1000),
            compute_local_ip(),
        )
        self.page.show_dialog(self.view.settings_dialog)

    async def on_cancel_settings(self, e: ft.Event) -> None:
        self.page.pop_dialog()

    async def on_apply_settings(self, e: ft.Event) -> None:
        station = (self.view.station_field.value or "").strip() or default_station_id()

        # Parse defensively: a typo keeps the previous value instead of crashing.
        try:
            port = int(self.view.port_field.value)
        except (TypeError, ValueError):
            port = self.listen_port
        if not 0 < port < 65536:
            port = self.listen_port

        try:
            delay_ms = max(0, int(self.view.speed_field.value))
        except (TypeError, ValueError):
            delay_ms = round(self.line_delay * 1000)

        enable = bool(self.view.receive_switch.value)
        self.station = station
        self.line_delay = delay_ms / 1000
        self.page.pop_dialog()

        port_changed = port != self.listen_port
        self.listen_port = port
        self.receive_enabled = enable
        # Keep discovery advertising our new identity and data port.
        self.discovery.update_name(self.station)
        self.discovery.update_port(self.listen_port)

        if enable and (port_changed or not self._server_running):
            await self._restart_server()
        elif not enable and self._server_running:
            await self._stop_server()
            self.view.set_network_status("Receiving is off")

        self.view.set_status(f"Saved network settings (station {self.station}).")
        self.page.update()

    def on_resize(self, e: ft.PageResizeEvent) -> None:
        compact = is_compact(self.page, width=e.width)
        if compact == self._compact:
            return
        self._compact = compact
        self._render()