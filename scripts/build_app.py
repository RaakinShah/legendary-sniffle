#!/usr/bin/env python3
"""Build a double-clickable macOS ``Aide.app`` bundle.

This does NOT produce a standalone/redistributable app. It builds a thin native
``.app`` that launches Aide from this project's virtualenv — reliable, fast, and
free of the pyobjc/pywebview/Vision packaging headaches that py2app/PyInstaller
hit. The result behaves like a real app: it lives in /Applications, shows in
Launchpad and Spotlight, has its own icon and Dock entry, and runs on the Haiku
backend exactly like ``assistant-gui`` does.

How it works
------------
* ``Contents/MacOS/Aide`` is a tiny shell launcher that sets a Finder-safe PATH
  (so the Claude CLI is found) and ``exec -a Aide``'s the venv Python on
  ``assistant.gui``. ``-a Aide`` makes the menu-bar/Dock name read "Aide"
  instead of "Python".
* ``Contents/Info.plist`` carries the bundle identity + the Automation usage
  string macOS shows when Aide first asks for AppleScript access.
* ``Contents/Resources/AppIcon.icns`` is generated from the app's gradient orb.
* The bundle is ad-hoc code-signed so its TCC identity is stable across runs.

Note on permissions: because this is a new bundle identity, the first launch will
re-prompt for Screen Recording / Automation / Full Disk Access — those grants do
not carry over from the terminal-run version.

Usage
-----
    python scripts/build_app.py                 # install to /Applications (or ~/Applications)
    python scripts/build_app.py --target ~/Desktop
    python scripts/build_app.py --no-icon       # skip icon generation
"""

from __future__ import annotations

import argparse
import plistlib
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
VENV_PYTHON = PROJECT / ".venv" / "bin" / "python"
BUNDLE_ID = "com.rashah04.aide"
APP_NAME = "Aide"

LAUNCHER = """#!/bin/bash
# Aide launcher — runs the app from its project virtualenv.
# A Finder-launched app gets a minimal PATH; add the usual user bin dirs so the
# Claude CLI (Haiku backend) is found. exec -a Aide sets the process name so the
# menu bar and Dock read "Aide" rather than "Python".
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
exec -a Aide "{python}" -m assistant.gui
"""

INFO_PLIST = {
    "CFBundleName": APP_NAME,
    "CFBundleDisplayName": APP_NAME,
    "CFBundleExecutable": APP_NAME,
    "CFBundleIdentifier": BUNDLE_ID,
    "CFBundleIconFile": "AppIcon",
    "CFBundlePackageType": "APPL",
    "CFBundleShortVersionString": "1.0",
    "CFBundleVersion": "1.0",
    "LSMinimumSystemVersion": "11.0",
    "NSHighResolutionCapable": True,
    "LSApplicationCategoryType": "public.app-category.productivity",
    "NSAppleEventsUsageDescription":
        "Aide uses AppleScript to read your calendar, reminders, mail, and "
        "contacts, and to draft messages you ask it to.",
}


# --- icon -------------------------------------------------------------------

_ORB_STOPS = [(255, 111, 156), (255, 180, 95), (87, 201, 255), (91, 141, 239)]


def _interp(colors: list[tuple], t: float) -> tuple:
    t = max(0.0, min(1.0, t))
    n = len(colors) - 1
    f = t * n
    i = int(f)
    if i >= n:
        return colors[n]
    a, b = colors[i], colors[i + 1]
    r = f - i
    return tuple(round(a[k] + (b[k] - a[k]) * r) for k in range(3))


def _orb_icon(size: int = 1024):
    """The app's gradient orb on a dark squircle — flat, no glow."""
    from PIL import Image, ImageDraw

    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # dark rounded-square background
    pad = int(size * 0.085)
    radius = int(size * 0.225)
    draw.rounded_rectangle([pad, pad, size - pad, size - pad], radius=radius,
                           fill=(26, 26, 29, 255))

    # diagonal gradient orb: build a 1px gradient row, stretch, rotate 45°, crop
    od = int(size * 0.6)
    row = Image.new("RGB", (od, 1))
    row.putdata([_interp(_ORB_STOPS, x / (od - 1)) for x in range(od)])
    grad = row.resize((od, od)).rotate(45, expand=True)
    cx, cy = grad.width // 2, grad.height // 2
    grad = grad.crop((cx - od // 2, cy - od // 2, cx - od // 2 + od, cy - od // 2 + od))

    mask = Image.new("L", (od, od), 0)
    ImageDraw.Draw(mask).ellipse([0, 0, od - 1, od - 1], fill=255)
    ox = (size - od) // 2
    img.paste(grad, (ox, ox), mask)

    # crisp hairline ring (matches the app orb's inset ring, no bloom)
    ring = int(size * 0.004)
    draw.ellipse([ox, ox, ox + od, ox + od], outline=(255, 255, 255, 40), width=ring)
    return img


def _build_icns(resources: Path) -> bool:
    try:
        from PIL import Image
    except Exception:
        print("  ! Pillow not available — skipping icon (app still works).")
        return False
    master = _orb_icon(1024)
    plan = {16: ["16x16"], 32: ["16x16@2x", "32x32"], 64: ["32x32@2x"],
            128: ["128x128"], 256: ["128x128@2x", "256x256"],
            512: ["256x256@2x", "512x512"], 1024: ["512x512@2x"]}
    with tempfile.TemporaryDirectory() as tmp:
        iconset = Path(tmp) / "Aide.iconset"
        iconset.mkdir()
        for px, names in plan.items():
            im = master.resize((px, px), Image.LANCZOS)
            for nm in names:
                im.save(iconset / f"icon_{nm}.png")
        r = subprocess.run(["iconutil", "-c", "icns", str(iconset),
                            "-o", str(resources / "AppIcon.icns")],
                           capture_output=True, text=True)
        if r.returncode != 0:
            print("  ! iconutil failed:", r.stderr.strip())
            return False
    return True


# --- bundle -----------------------------------------------------------------

def build(into: Path, make_icon: bool = True) -> Path:
    if not VENV_PYTHON.exists():
        sys.exit(f"venv python not found at {VENV_PYTHON} — run pip install -e '.[gui]' first.")

    app = into / f"{APP_NAME}.app"
    if app.exists():
        shutil.rmtree(app)
    contents = app / "Contents"
    macos = contents / "MacOS"
    resources = contents / "Resources"
    macos.mkdir(parents=True)
    resources.mkdir(parents=True)

    (contents / "Info.plist").write_bytes(plistlib.dumps(INFO_PLIST))

    launcher = macos / APP_NAME
    launcher.write_text(LAUNCHER.format(python=VENV_PYTHON))
    launcher.chmod(0o755)

    if make_icon:
        _build_icns(resources)

    # ad-hoc sign so the TCC identity is stable across launches
    r = subprocess.run(["codesign", "--force", "--deep", "--sign", "-", str(app)],
                       capture_output=True, text=True)
    if r.returncode != 0:
        print("  ! codesign (ad-hoc) failed (non-fatal):", r.stderr.strip())

    return app


def install(built: Path, target: Path) -> Path:
    """Copy the built bundle to target; fall back to ~/Applications if the
    preferred location isn't writable."""
    dest = target / built.name
    try:
        target.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(built, dest, symlinks=True)
        return dest
    except PermissionError:
        alt = Path.home() / "Applications"
        alt.mkdir(parents=True, exist_ok=True)
        dest = alt / built.name
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(built, dest, symlinks=True)
        print(f"  (couldn't write to {target}, used {alt})")
        return dest


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the Aide.app bundle.")
    ap.add_argument("--target", default="/Applications",
                    help="install dir (default: /Applications, falls back to ~/Applications)")
    ap.add_argument("--no-icon", action="store_true", help="skip icon generation")
    args = ap.parse_args()

    with tempfile.TemporaryDirectory() as tmp:
        print("Building Aide.app …")
        built = build(Path(tmp), make_icon=not args.no_icon)
        dest = install(built, Path(args.target).expanduser())
    print(f"✓ Installed: {dest}")
    print("  Launch it from Launchpad/Spotlight, or:  open -a Aide")
    print("  First run will re-ask for Screen Recording / Automation / Full Disk Access.")


if __name__ == "__main__":
    main()
