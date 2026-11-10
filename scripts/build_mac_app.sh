#!/bin/bash
# Build "Film Maker.app" (macOS native window) that runs this checkout's .venv.
#   scripts/build_mac_app.sh              -> ~/Applications/Film Maker.app
#   scripts/build_mac_app.sh /Applications
# Re-run after moving the project folder or recreating .venv.
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$(pwd)"
DEST_DIR="${1:-$HOME/Applications}"
APP="$DEST_DIR/Film Maker.app"
VENV_PY="$ROOT/.venv/bin/python"

[ -x "$VENV_PY" ] || { echo "Chưa có .venv — chạy scripts/setup_local.sh trước"; exit 1; }
echo "→ Cài thư viện..."
if ! PIPLOG="$("$VENV_PY" -m pip install -q -e '.[ai,ui,app]' 2>&1)"; then echo "$PIPLOG"; exit 1; fi
echo "→ Tạo app..."

PYCFG="$("$VENV_PY" -c 'import sys,os; print(os.path.join(sys.base_prefix,"bin",f"python{sys.version_info[0]}.{sys.version_info[1]}-config"))')"
[ -x "$PYCFG" ] || { echo "Không tìm thấy $PYCFG"; exit 1; }

BUILD="$(mktemp -d)"
trap 'rm -rf "$BUILD"' EXIT

# Launcher: embeds Python so the process belongs to this bundle (own Dock icon/name).
cat > "$BUILD/launcher.c" <<EOF
#include <Python.h>
int main(int argc, char **argv) {
    PyConfig c;
    PyConfig_InitPythonConfig(&c);
    PyConfig_SetBytesString(&c, &c.program_name, "$VENV_PY");
    PyConfig_SetBytesString(&c, &c.executable, "$VENV_PY");
    char *args[] = {"$VENV_PY", "-m", "filmmaker.desktop"};
    PyConfig_SetBytesArgv(&c, 3, args);
    PyStatus s = Py_InitializeFromConfig(&c);
    PyConfig_Clear(&c);
    if (PyStatus_Exception(s)) Py_ExitStatusException(s);
    return Py_RunMain();
}
EOF
# shellcheck disable=SC2046
clang -O2 -o "$BUILD/FilmMaker" "$BUILD/launcher.c" $("$PYCFG" --cflags) $("$PYCFG" --embed --ldflags) \
  -Wl,-rpath,"$("$VENV_PY" -c 'import sys; print(sys.base_prefix)')/.."

# Icon: draw the 🎬 emoji into an .icns.
mkdir -p "$BUILD/icon.iconset"
"$VENV_PY" - "$BUILD/icon.iconset" <<'PY'
import sys, AppKit
out = sys.argv[1]
for size in (16, 32, 64, 128, 256, 512, 1024):
    rep = AppKit.NSBitmapImageRep.alloc().initWithBitmapDataPlanes_pixelsWide_pixelsHigh_bitsPerSample_samplesPerPixel_hasAlpha_isPlanar_colorSpaceName_bytesPerRow_bitsPerPixel_(
        None, size, size, 8, 4, True, False, AppKit.NSDeviceRGBColorSpace, 0, 0)
    AppKit.NSGraphicsContext.saveGraphicsState()
    AppKit.NSGraphicsContext.setCurrentContext_(AppKit.NSGraphicsContext.graphicsContextWithBitmapImageRep_(rep))
    r = AppKit.NSMakeRect(size * 0.06, size * 0.06, size * 0.88, size * 0.88)
    AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(0.09, 0.10, 0.13, 1).set()
    AppKit.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(r, size * 0.2, size * 0.2).fill()
    font = AppKit.NSFont.systemFontOfSize_(size * 0.56)
    s = AppKit.NSAttributedString.alloc().initWithString_attributes_("🎬", {AppKit.NSFontAttributeName: font})
    w, h = s.size()
    s.drawAtPoint_(((size - w) / 2, (size - h) / 2))
    AppKit.NSGraphicsContext.restoreGraphicsState()
    png = rep.representationUsingType_properties_(AppKit.NSBitmapImageFileTypePNG, {})
    for name, scale in ((f"icon_{size}x{size}.png", 1), (f"icon_{size // 2}x{size // 2}@2x.png", 2)):
        if scale == 2 and size == 16:
            continue
        if scale == 1 and size == 1024:
            continue
        png.writeToFile_atomically_(f"{out}/{name}", True)
PY
iconutil -c icns -o "$BUILD/FilmMaker.icns" "$BUILD/icon.iconset"

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp "$BUILD/FilmMaker" "$APP/Contents/MacOS/FilmMaker"
cp "$BUILD/FilmMaker.icns" "$APP/Contents/Resources/FilmMaker.icns"
cat > "$APP/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>Film Maker</string>
  <key>CFBundleDisplayName</key><string>Film Maker</string>
  <key>CFBundleIdentifier</key><string>ai.filmmaker.app</string>
  <key>CFBundleExecutable</key><string>FilmMaker</string>
  <key>CFBundleIconFile</key><string>FilmMaker</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>0.1.0</string>
  <key>LSMinimumSystemVersion</key><string>11.0</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>NSDesktopFolderUsageDescription</key><string>Film Maker cần đọc mã nguồn và dự án phim nằm trong thư mục Desktop (kể cả khi điều khiển Flow để tạo video).</string>
  <key>NSDocumentsFolderUsageDescription</key><string>Film Maker cần đọc mã nguồn và dự án phim nằm trong thư mục Documents.</string>
  <key>NSDownloadsFolderUsageDescription</key><string>Film Maker cần đọc/lưu ảnh, video trong thư mục Downloads.</string>
  <key>NSAppTransportSecurity</key><dict><key>NSAllowsLocalNetworking</key><true/></dict>
</dict></plist>
EOF
codesign --force --deep -s - "$APP" >/dev/null 2>&1 || true
touch "$APP"
echo "✔ Đã tạo: $APP"
echo "  Mở bằng Finder/Launchpad/Spotlight (\"Film Maker\"), kéo vào Dock để mở nhanh."
