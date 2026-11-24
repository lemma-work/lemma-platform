#!/usr/bin/env bash
# Build the iOS app and upload it to App Store Connect, for TestFlight.
#
# Signing is Xcode's: sign in to Xcode (Settings > Accounts) with an Apple ID
# on the team, and create the app once in App Store Connect with bundle ID
# work.lemma.mobile. Xcode then makes the certificate and the profile itself
# (-allowProvisioningUpdates). Nothing here holds a credential.
#
#   APPLE_TEAM_ID=XXXXXXXXXX scripts/testflight.sh
#
# Each upload needs a build number App Store Connect has not seen, higher
# than the last, so it is the time of the build in UTC: year.monthday.hourminute,
# e.g. 2026.1023.2341, with no leading zeros (BUILD_NUMBER overrides it).
set -euo pipefail

: "${APPLE_TEAM_ID:?Set APPLE_TEAM_ID to the Apple Developer team that owns work.lemma.mobile}"
BUILD_NUMBER="${BUILD_NUMBER:-$(date -u +%Y).$((10#$(date -u +%m%d))).$((10#$(date -u +%H%M)))}"
HERE="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$HERE/ios/build/testflight"

export LANG="${LANG:-en_US.UTF-8}"
cd "$HERE"

# The native project is generated: regenerate it, so app.json is what ships.
CI=1 npx expo prebuild --platform ios

# Prebuild writes app.json's buildNumber into Info.plist as a literal, which
# CURRENT_PROJECT_VERSION does not reach, so the number goes in there.
/usr/libexec/PlistBuddy -c "Set :CFBundleVersion $BUILD_NUMBER" ios/Lemma/Info.plist

rm -rf "$OUT"
mkdir -p "$OUT"

xcodebuild archive \
    -workspace ios/Lemma.xcworkspace \
    -scheme Lemma \
    -configuration Release \
    -destination generic/platform=iOS \
    -archivePath "$OUT/Lemma.xcarchive" \
    -allowProvisioningUpdates \
    DEVELOPMENT_TEAM="$APPLE_TEAM_ID" \
    CODE_SIGN_STYLE=Automatic \
    CURRENT_PROJECT_VERSION="$BUILD_NUMBER"

cat > "$OUT/ExportOptions.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>method</key><string>app-store-connect</string>
    <key>destination</key><string>upload</string>
    <key>teamID</key><string>${APPLE_TEAM_ID}</string>
    <key>signingStyle</key><string>automatic</string>
    <key>uploadSymbols</key><true/>
    <key>manageAppVersionAndBuildNumber</key><false/>
</dict>
</plist>
PLIST

xcodebuild -exportArchive \
    -archivePath "$OUT/Lemma.xcarchive" \
    -exportOptionsPlist "$OUT/ExportOptions.plist" \
    -exportPath "$OUT/export" \
    -allowProvisioningUpdates

echo "Uploaded build $BUILD_NUMBER. App Store Connect processes it for TestFlight in a few minutes."
