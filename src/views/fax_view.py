"""View layer: builds every Flet control. No business logic lives here.

The controller owns the model and wires ``on_click`` / ``on_select`` handlers
onto the controls created here, then calls ``build()`` to assemble the layout.

The camera is injected already-constructed (or as ``None`` when the platform
does not support it) so this layer never imports ``flet_camera`` itself.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import flet as ft

from models.fax import DEFAULT_RESOLUTION, RESOLUTIONS

if TYPE_CHECKING:  # avoid importing flet_camera just for a type hint
    from flet_camera import Camera

PANEL_BG = ft.Colors.WHITE
PANEL_BORDER = ft.Colors.GREY_300
MUTED = ft.Colors.GREY_500

# Below this width the panes move into a tab bar. It is wider than the old
# two-pane layout because three side-by-side panels need more room to stay
# readable.
COMPACT_WIDTH = 900

CAMERA_UNSUPPORTED_HINT = (
    "Camera is only available on Android, iOS and Web builds - not desktop."
)


class FaxView:
    """Owns the controls; arrangement is chosen by ``build(compact=...)``."""

    def __init__(self, camera: "Camera | None" = None) -> None:
        self.original_image = ft.Image(
            src=None,
            key="original_image",
            fit=ft.BoxFit.CONTAIN,
            expand=True,
            visible=False,  # a src-less Image must be hidden or Flet rejects it
        )
        self.scanned_image = ft.Image(
            src=None,
            key="scanned_image",
            fit=ft.BoxFit.CONTAIN,
            expand=True,
            visible=False,
        )

        self.original_hint = ft.Text(
            "Load an image to begin",
            color=MUTED,
            text_align=ft.TextAlign.CENTER,
        )
        self.scanned_hint = ft.Text(
            "Press Scan to see the fax",
            color=MUTED,
            text_align=ft.TextAlign.CENTER,
        )

        self.status = ft.Text("", size=13, color=MUTED, key="status_text")

        self.load_button = ft.Button(
            content="Load image",
            icon=ft.Icons.UPLOAD_FILE,
            key="load_button",
        )
        self.scan_button = ft.Button(
            content="Scan",
            icon=ft.Icons.SCANNER,
            key="scan_button",
            disabled=True,
        )
        self.clear_button = ft.Button(
            content="Clear",
            icon=ft.Icons.DELETE_OUTLINE,
            key="clear_button",
            disabled=True,
        )
        self.camera_button = ft.Button(
            content="Take photo",
            icon=ft.Icons.CAMERA_ALT,
            key="camera_button",
            disabled=camera is None,
            tooltip=None if camera is not None else CAMERA_UNSUPPORTED_HINT,
        )
        self.capture_button = ft.Button(
            content="Capture",
            icon=ft.Icons.PHOTO_CAMERA,
            key="capture_button",
        )
        self.close_camera_button = ft.Button(
            content="Close",
            icon=ft.Icons.CLOSE,
            key="close_camera_button",
        )

        self.resolution = ft.Dropdown(
            key="resolution_dropdown",
            label="Resolution",
            value=str(DEFAULT_RESOLUTION),
            width=170,
            options=[
                ft.DropdownOption(key=str(size), text=f"{size} x {size}")
                for size in RESOLUTIONS
            ],
        )

        # ---------------------------------------------------------- transceiver
        # "Send to" is the peer's LAN address. It is plain text, parsed by the
        # controller, so the view does not need to understand host:port.
        self.recipient = ft.TextField(
            key="recipient_field",
            label="Send to",
            hint_text="192.168.1.20:9100",
            width=200,
        )
        self.send_button = ft.Button(
            content="Send fax",
            icon=ft.Icons.SEND,
            key="send_button",
            disabled=True,  # only enabled once there is a scan to send
        )
        self.settings_button = ft.IconButton(
            icon=ft.Icons.SETTINGS,
            key="settings_button",
            tooltip="Network settings",
        )
        # A live list of other fax machines found on the LAN. Picking one fills
        # the "Send to" field, so you rarely have to type an IP address.
        self.peer_dropdown = ft.Dropdown(
            key="peer_dropdown",
            label="Nearby faxes",
            width=280,
            options=[],
            hint_text="Searching the network...",
        )

        # ------------------------------------------------------- received inbox
        # This pane fills in line by line while a fax is arriving, just like the
        # paper coming out of a real machine.
        self.received_image = ft.Image(
            src=None,
            key="received_image",
            fit=ft.BoxFit.CONTAIN,
            expand=True,
            visible=False,  # shown only once a fax actually arrives
        )
        self.received_hint = ft.Text(
            "No fax received yet",
            color=MUTED,
            text_align=ft.TextAlign.CENTER,
        )
        self.received_caption = ft.Text(
            "",
            size=12,
            color=MUTED,
            key="received_caption",
            text_align=ft.TextAlign.CENTER,
        )
        self.received_progress = ft.ProgressBar(
            value=0, key="received_progress", visible=False
        )
        self.save_button = ft.Button(
            content="Save",
            icon=ft.Icons.DOWNLOAD,
            key="save_button",
            disabled=True,
        )

        # --------------------------------------------------------- settings form
        self.station_field = ft.TextField(
            key="station_field", label="Station ID", width=200
        )
        self.port_field = ft.TextField(
            key="port_field", label="Listen port", value="9100", width=130
        )
        self.speed_field = ft.TextField(
            key="speed_field", label="Line delay (ms)", value="10", width=140
        )
        self.receive_switch = ft.Switch(
            key="receive_switch", label="Receive faxes", value=True
        )
        self.local_ip_text = ft.Text("", key="local_ip_text", size=12, color=MUTED)
        self.network_status = ft.Text(
            "", key="network_status", size=12, color=MUTED
        )
        self.settings_apply_button = ft.Button(content="Apply")
        self.settings_cancel_button = ft.Button(content="Cancel")
        self.settings_dialog = ft.AlertDialog(
            key="settings_dialog",
            modal=True,
            title=ft.Text("Network settings"),
            content=ft.Column(
                controls=[
                    self.station_field,
                    ft.Row(
                        controls=[self.port_field, self.speed_field],
                        spacing=10,
                        wrap=True,
                    ),
                    self.receive_switch,
                    ft.Text("This machine:", size=12, color=MUTED),
                    self.local_ip_text,
                    self.network_status,
                ],
                tight=True,
                spacing=12,
                width=340,
            ),
            actions=[self.settings_cancel_button, self.settings_apply_button],
        )

        # Only mounted in compact (tabbed) layouts.
        self.tabs: ft.Tabs | None = None

        # Kept mounted even when hidden so the camera control stays attached.
        self._camera_open = False
        self.camera_panel: ft.Container | None = None
        if camera is not None:
            # Kept mounted and visible for the whole session. Hiding it would
            # unmount the preview and make flet-camera dispose the controller,
            # so a second "Take photo" showed a dead black preview. Instead the
            # image panes are stacked on top and toggled, and the preview is
            # paused/resumed, which keeps the controller alive.
            self.camera_panel = ft.Container(
                key="camera_panel",
                expand=True,
                bgcolor=ft.Colors.BLACK,
                border_radius=12,
                clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
                content=ft.Stack(
                    expand=True,
                    controls=[
                        camera,
                        ft.Container(
                            alignment=ft.Alignment.BOTTOM_CENTER,
                            padding=16,
                            content=ft.Row(
                                alignment=ft.MainAxisAlignment.CENTER,
                                spacing=12,
                                controls=[self.capture_button, self.close_camera_button],
                            ),
                        ),
                    ],
                ),
            )

        self.body_container: ft.Container | None = None

    # ------------------------------------------------------------------ build
    def build(self, compact: bool) -> ft.Control:
        """Return a fresh control tree in side-by-side or tabbed form."""
        toolbar = ft.Row(
            controls=[
                self.load_button,
                self.camera_button,
                self.scan_button,
                self.clear_button,
                self.resolution,
                self.peer_dropdown,
                self.recipient,
                self.send_button,
                self.settings_button,
            ],
            wrap=True,
            spacing=10,
            run_spacing=10,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

        header = ft.Column(
            controls=[
                ft.Text("Fax Machine Transceiver", size=24, weight=ft.FontWeight.BOLD),
                ft.Text(
                    "Load a picture or take a photo, scan it into a coarse grid of "
                    "grey cells, then send it to another machine on the network. "
                    "Incoming faxes print line by line in the Received pane.",
                    size=13,
                    color=MUTED,
                ),
            ],
            spacing=2,
        )

        body = self._build_tabs() if compact else self._build_side_by_side()
        # Opaque, so it fully covers the camera preview stacked underneath.
        self.body_container = ft.Container(
            expand=True,
            bgcolor=ft.Colors.SURFACE,
            visible=not self._camera_open,
            content=body,
        )

        if self.camera_panel is not None:
            stage: ft.Control = ft.Stack(
                expand=True,
                controls=[self.camera_panel, self.body_container],
            )
        else:
            stage = self.body_container

        controls: list[ft.Control] = [header, toolbar, self.status, stage]

        return ft.SafeArea(
            expand=True,
            content=ft.Container(
                expand=True,
                padding=16,
                content=ft.Column(controls=controls, expand=True, spacing=12),
            ),
        )

    def _build_tabs(self) -> ft.Control:
        self.tabs = ft.Tabs(
            key="tabs",
            length=3,
            selected_index=0,
            expand=True,
            content=ft.Column(
                controls=[
                    ft.TabBar(
                        tabs=[
                            ft.Tab(label="Original", icon=ft.Icons.IMAGE),
                            ft.Tab(label="Scanned", icon=ft.Icons.FAX),
                            ft.Tab(label="Received", icon=ft.Icons.INBOX),
                        ]
                    ),
                    ft.TabBarView(
                        expand=True,
                        controls=[
                            self._panel(self.original_image, self.original_hint),
                            self._panel(self.scanned_image, self.scanned_hint),
                            self._received_panel(),
                        ],
                    ),
                ],
                expand=True,
                spacing=0,
            ),
        )
        return self.tabs

    def _build_side_by_side(self) -> ft.Control:
        self.tabs = None
        return ft.Row(
            expand=True,
            spacing=12,
            controls=[
                self._panel(self.original_image, self.original_hint),
                self._panel(self.scanned_image, self.scanned_hint),
                self._received_panel(),
            ],
        )

    @staticmethod
    def _panel(image: ft.Image, hint: ft.Text) -> ft.Container:
        return ft.Container(
            expand=True,
            bgcolor=PANEL_BG,
            border=ft.Border.all(1, PANEL_BORDER),
            border_radius=12,
            padding=8,
            alignment=ft.Alignment.CENTER,
            content=ft.Column(
                controls=[hint, image],
                expand=True,
                alignment=ft.MainAxisAlignment.CENTER,
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            ),
        )

    def _received_panel(self) -> ft.Container:
        """The inbox pane: preview, caption, progress bar and Save button."""
        return ft.Container(
            expand=True,
            bgcolor=PANEL_BG,
            border=ft.Border.all(1, PANEL_BORDER),
            border_radius=12,
            padding=8,
            alignment=ft.Alignment.CENTER,
            content=ft.Column(
                controls=[
                    self.received_hint,
                    self.received_image,
                    self.received_progress,
                    self.received_caption,
                    self.save_button,
                ],
                expand=True,
                alignment=ft.MainAxisAlignment.CENTER,
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                spacing=8,
            ),
        )

    # ----------------------------------------------------------------- updates
    def set_source(self, data: bytes | None) -> None:
        self.original_image.src = data
        self.original_image.visible = data is not None
        self.original_hint.visible = data is None

    def set_result(self, data: bytes | None) -> None:
        self.scanned_image.src = data
        self.scanned_image.visible = data is not None
        self.scanned_hint.visible = data is None

    def set_enabled(self, has_source: bool, has_result: bool) -> None:
        self.scan_button.disabled = not has_source
        self.clear_button.disabled = not (has_source or has_result)
        # You can only "dial" once there is a scan to transmit.
        self.send_button.disabled = not has_result

    def set_status(self, text: str) -> None:
        self.status.value = text

    def set_received(self, data: bytes | None, caption: str) -> None:
        """Show (or clear) the inbox preview with a one-line caption."""
        self.received_image.src = data
        self.received_image.visible = data is not None
        self.received_hint.visible = data is None
        self.received_caption.value = caption
        self.received_caption.visible = bool(caption)

    def set_received_progress(self, fraction: float) -> None:
        """Update the receive progress bar; hide it once the page is complete."""
        done = fraction >= 1.0
        self.received_progress.value = 1.0 if done else fraction
        self.received_progress.visible = not done

    def set_save_enabled(self, enabled: bool) -> None:
        self.save_button.disabled = not enabled

    def set_network_status(self, text: str) -> None:
        self.network_status.value = text

    def set_peers(self, options: list[tuple[str, str]]) -> None:
        """Replace the "Nearby faxes" dropdown with discovered peers.

        ``options`` is a list of ``(value, label)`` pairs, where ``value`` is the
        ``host:port`` address used when sending.
        """
        self.peer_dropdown.options = [
            ft.DropdownOption(key=value, text=label) for value, label in options
        ]
        # If the selected peer has disappeared, clear the selection. The text in
        # "Send to" is left alone so a manual send can still proceed.
        if self.peer_dropdown.value not in {value for value, _ in options}:
            self.peer_dropdown.value = None

    def set_settings_values(
        self, station: str, port: int, delay_ms: int, local_ip: str
    ) -> None:
        """Copy the controller's current settings into the dialog fields."""
        self.station_field.value = station
        self.port_field.value = str(port)
        self.speed_field.value = str(delay_ms)
        self.local_ip_text.value = f"This machine sends/receives on {local_ip}"

    def set_camera_open(self, opened: bool) -> None:
        """Reveal the live preview by hiding the panes stacked on top of it.

        The camera panel itself is never hidden: hiding it would unmount the
        preview and dispose the camera controller (which broke the second use).
        """
        self._camera_open = opened
        if self.body_container is not None:
            self.body_container.visible = not opened

    async def select_tab(self, index: int) -> None:
        """Switch the tab bar, if one is currently mounted."""
        if self.tabs is not None:
            await self.tabs.move_to(index)