#!/usr/bin/env bash
# macOS 打包：前端构建 → PyInstaller（spec 在 darwin 下多出一个 .app BUNDLE）→ 包内容自检 → dmg。
# 必须在 macOS 上跑（没有跨平台编译）。签名/公证见 desktop/README.md。
set -euo pipefail
cd "$(dirname "$0")/.."

VERSION=$(python3 -c "import sys; sys.path.insert(0,'src'); from genkit.version import __version__; print(__version__)")
echo "== StoryStudio $VERSION"

if command -v npm >/dev/null 2>&1; then
  echo "== frontend build"
  (cd frontend && { [ -d node_modules ] || npm ci --no-audit --no-fund; } && npm run build)
fi

echo "== PyInstaller"
# 中间产物（build/，记着本机的绝对路径）放到临时目录，不留在仓库里
WORK="${TMPDIR:-/tmp}/storystudio-pyi-build"
python3 -m PyInstaller desktop/storystudio.spec --noconfirm --workpath "$WORK"
APP="dist/StoryStudio.app"
[ -d "$APP" ] || { echo "没有产出 $APP"; exit 1; }

# 包里不许有测试、白名单外的数据文件、本机数据和打包机自己的路径（见 desktop/verify_bundle.py 文件头）
echo "== 包内容自检"
python3 desktop/verify_bundle.py "$APP" || { echo "包内容自检没过：按上面的清单修掉再打包"; exit 1; }

# 签名（可选）：export CODESIGN_IDENTITY="Developer ID Application: ..."；不设就不签
if [ -n "${CODESIGN_IDENTITY:-}" ]; then
  echo "== codesign"
  codesign --deep --force --options runtime --entitlements desktop/entitlements.plist --sign "$CODESIGN_IDENTITY" "$APP"
fi

echo "== dmg"
mkdir -p dist/installer
DMG="dist/installer/StoryStudio-$VERSION.dmg"
rm -f "$DMG"
STAGING=$(mktemp -d)
cp -R "$APP" "$STAGING/"
ln -s /Applications "$STAGING/Applications"
hdiutil create -volname "StoryStudio" -srcfolder "$STAGING" -ov -format UDZO "$DMG"
rm -rf "$STAGING"
echo "dmg：$DMG"

# 公证（可选）：export NOTARY_PROFILE=<xcrun notarytool store-credentials 里存的名字>
if [ -n "${NOTARY_PROFILE:-}" ]; then
  echo "== notarize"
  xcrun notarytool submit "$DMG" --keychain-profile "$NOTARY_PROFILE" --wait
  xcrun stapler staple "$DMG"
fi
