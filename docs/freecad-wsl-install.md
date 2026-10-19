# Installing FreeCAD on WSL (Ubuntu)

FreeCAD is used here to generate CNC G-code from STL models via the Path (CAM) workbench. The headless binary `freecadcmd` can be scripted from the terminal, so no GUI is required inside WSL.

## Why not `apt`?

On Ubuntu 24.04 (noble) in WSL, `apt-cache policy freecad` reports the package but no candidate version — the index entry exists but no installable artifact is published for this release. The AppImage from GitHub is the fastest, version-pinned alternative and needs no `sudo`.

## Install steps

From any writable location (home directory used here):

```bash
cd ~
wget -O FreeCAD_1.1.1.AppImage \
  "https://github.com/FreeCAD/FreeCAD/releases/download/1.1.1/FreeCAD_1.1.1-Linux-x86_64-py311.AppImage"
chmod +x FreeCAD_1.1.1.AppImage
./FreeCAD_1.1.1.AppImage --appimage-extract
```

The `--appimage-extract` step unpacks the AppImage into `~/squashfs-root/` so it runs without FUSE (WSL does not ship FUSE by default).

## Verifying the install

```bash
~/squashfs-root/usr/bin/freecadcmd --version
```

Expected output (version string will match the AppImage you downloaded):

```
FreeCAD 1.1.1 Revision: 20260414 (Git shallow)
```

## Making `freecadcmd` easier to call

Optional — add a symlink into `~/.local/bin` (already on most Ubuntu `$PATH`s):

```bash
mkdir -p ~/.local/bin
ln -sf ~/squashfs-root/usr/bin/freecadcmd ~/.local/bin/freecadcmd
```

After that, `freecadcmd script.py` works from anywhere.

## Upgrading

Download the newer AppImage from <https://github.com/FreeCAD/FreeCAD/releases>, re-run `--appimage-extract` (it overwrites `squashfs-root/`), and the existing symlink continues to point at the new binary.
