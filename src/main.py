"""faxer - a Flet fax machine simulator.

ARCHITECTURE (Control / Model / View + dataclasses)
    src/
      main.py                        entry point: ``ft.run(main)``
      models/fax.py                  ``@dataclass`` FaxState / ScanResult plus
                                     pure image logic (standardise onto a
                                     WORK_SIZE square, scan to an n x n grid of
                                     averaged grey cells). No Flet imports.
      views/fax_view.py              every Flet control; no business logic
      controllers/fax_controller.py  wires model <-> view, owns the FilePicker
                                     service and the responsive layout switch

CROSS-PLATFORM (desktop / web / phone)
    - Images are read as bytes (``pick_files(with_data=True)``) and displayed as
      raw PNG bytes, never through a file path: ``FilePickerFile.path`` is None
      in the browser.
    - The layout follows the device: narrow screens (or mobile platforms) get a
      two-tab bar, wide screens get the original and the scan side by side.

RUN
    uv run flet run            # desktop
    uv run flet run --web      # browser
    flet build apk -v          # Android (also ipa / windows / macos / linux)
"""

import flet as ft

from controllers.fax_controller import FaxController


def main(page: ft.Page) -> None:
    page.title = "Fax Machine Simulator"
    page.theme_mode = ft.ThemeMode.LIGHT

    FaxController(page).build()


if __name__ == "__main__":
    ft.run(main)