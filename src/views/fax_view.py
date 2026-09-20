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

# Below this width the two panes move into a tab bar.
COMPACT_WIDTH = 700

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
        )
        self.scanned_image = ft.Image(
            src=None,
            key="scanned_image",
            fit=ft.BoxFit.CONTAIN,
            expand=True,
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
            ],
            wrap=True,
            spacing=10,
            run_spacing=10,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

        header = ft.Column(
            controls=[
                ft.Text("Fax Machine Simulator", size=24, weight=ft.FontWeight.BOLD),
                ft.Text(
                    "Load a picture or take a photo, scan it into a coarse grid "
                    "of grey cells, and compare with the original.",
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
            length=2,
            selected_index=0,
            expand=True,
            content=ft.Column(
                controls=[
                    ft.TabBar(
                        tabs=[
                            ft.Tab(label="Original", icon=ft.Icons.IMAGE),
                            ft.Tab(label="Scanned", icon=ft.Icons.FAX),
                        ]
                    ),
                    ft.TabBarView(
                        expand=True,
                        controls=[
                            self._panel(self.original_image, self.original_hint),
                            self._panel(self.scanned_image, self.scanned_hint),
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

    def set_status(self, text: str) -> None:
        self.status.value = text

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