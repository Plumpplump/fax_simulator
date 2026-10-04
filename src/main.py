"""faxer - a Flet fax machine *transceiver* (send and receive over a network).

ARCHITECTURE (Control / Model / View + dataclasses, plus a reusable p2p layer)
    src/
      main.py                        entry point: ``ft.run(main)``
      models/fax.py                  ``@dataclass`` FaxState / ScanResult /
                                     ReceivedFax plus pure image logic. No Flet.
      views/fax_view.py              every Flet control; no business logic
      controllers/fax_controller.py  wires model <-> view; owns the FilePicker,
                                     the camera, AND the network listener/sender
      services/fax_net.py            the fax protocol (line-by-line transfer),
                                     built on top of the generic ``p2p`` toolkit
      p2p/                           INDEPENDENT, dependency-free peer-to-peer
                                     messaging toolkit - copy it into any other
                                     Python project (see ``src/p2p/README.md``)

SENDING A FAX (LAN peer-to-peer)
    Each running copy of the app listens on TCP port 9100 (configurable in
    Settings) and announces itself on the LAN over UDP (see p2p/discovery.py).
    Other copies appear automatically in the "Nearby faxes" dropdown - pick one
    and press "Send fax"; or type an address like 192.168.1.20:9100 into
    "Send to". The scan is streamed row by row and prints line by line in the
    receiver's Received pane. Both machines must be on the same network (or the
    port forwarded), and the Windows firewall may ask to allow the app the first
    time it listens or broadcasts.

CROSS-PLATFORM (desktop / web / phone)
    - Images are read as bytes and displayed as raw PNG bytes, never a file path.
    - Received faxes are saved with ``FilePicker.save_file(src_bytes=...)``.
    - Layout follows the device: narrow screens get a three-tab bar (Original /
      Scanned / Received), wide screens get three panes side by side.
    - In ``--web`` mode the socket lives on the machine running Python (the
      server), not inside the browser.

RUN
    uv run flet run            # desktop
    uv run flet run --web      # browser
    flet build apk -v          # Android (also ipa / windows / macos / linux)
"""

import flet as ft

from controllers.fax_controller import FaxController


def main(page: ft.Page) -> None:
    page.title = "Fax Machine Transceiver"
    page.theme_mode = ft.ThemeMode.LIGHT

    FaxController(page).build()


if __name__ == "__main__":
    ft.run(main)