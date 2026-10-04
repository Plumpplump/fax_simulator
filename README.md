# Faxer app

## Run the app

### uv

Run as a desktop app:

```bash
uv run flet run
```

Run as a web app:

```bash
uv run flet run --web
```

For more details on running the app, refer to the [Getting Started Guide](https://flet.dev/docs/).

## Send and receive faxes over the network (LAN)

Every running copy of the app is both a sender and a receiver. It listens on TCP
port `9100` (change it in **Settings**) and can dial another copy on the same
network.

1. Start the app on two machines on the same Wi-Fi/LAN.
2. Open **Settings** on the receiving machine and note the **local IP** shown
   (it looks like `192.168.1.20`). Windows may ask to allow the app through the
   firewall the first time it starts listening - allow it.
3. On the sending machine: load or photograph an image, press **Scan**, then
   pick the receiver from the **Nearby faxes** dropdown (each running copy is
   found automatically over the LAN). If the list is empty you can still type
   the receiver's address into **Send to** (e.g. `192.168.1.20:9100`), then
   press **Send fax**.
4. The receiving machine's **Received** pane fills in line by line as the page
   arrives, then **Save** writes it to disk as a PNG.

The networking core lives in `src/p2p/` and has **no** dependency on Flet, Pillow
or the fax code, so it can be copied into any other Python project and imported
with `import p2p` (see [src/p2p/README.md](src/p2p/README.md)).

## Build the app

### Android

```bash
flet build apk -v
```

For more details on building and signing `.apk` or `.aab`, refer to the [Android Packaging Guide](https://flet.dev/docs/publish/android/).

### iOS

```bash
flet build ipa -v
```

For more details on building and signing `.ipa`, refer to the [iOS Packaging Guide](https://flet.dev/docs/publish/ios/).

### macOS

```bash
flet build macos -v
```

For more details on building macOS package, refer to the [macOS Packaging Guide](https://flet.dev/docs/publish/macos/).

### Linux

```bash
flet build linux -v
```

For more details on building Linux package, refer to the [Linux Packaging Guide](https://flet.dev/docs/publish/linux/).

### Windows

```bash
flet build windows -v
```

For more details on building Windows package, refer to the [Windows Packaging Guide](https://flet.dev/docs/publish/windows/).

### Web

```bash
flet build web -v
```

For more details on building Web app, refer to the [Web Packaging Guide](https://flet.dev/docs/publish/web/).
