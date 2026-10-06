#!/bin/sh
# Builds android/build/dota2-model.apk without Android Studio.
# Runs on Android 7.0+ (v2 signature). Needs Java 11+, python3 and:
#   AAPT2       aapt2 binary (Android build-tools, or: pip download aapt2 -> aapt2/bin/Linux/aapt2)
#   ANDROID_JAR android.jar, API 24+  (e.g. https://raw.githubusercontent.com/Sable/android-platforms/master/android-30/android.jar)
#   DX_JAR      https://repo1.maven.org/maven2/com/jakewharton/android/repackaged/dalvik-dx/16.0.1/dalvik-dx-16.0.1.jar
#   APKSIG_JAR  https://repo1.maven.org/maven2/com/android/tools/build/apksig/2.3.0/apksig-2.3.0.jar
# The page itself comes from ../app.html (run build.py first); it is packed in as assets/index.html.
# Signing key: android/keystore.jks (git-ignored; created on first build). A phone only installs an update
# signed with the same key, so keep it (on GitHub: secret ANDROID_KEYSTORE_BASE64, see build-apk.yml).
set -e
cd "$(dirname "$0")"
: "${AAPT2:?set AAPT2}" "${ANDROID_JAR:?set ANDROID_JAR}" "${DX_JAR:?set DX_JAR}" "${APKSIG_JAR:?set APKSIG_JAR}"
KEYSTORE=${KEYSTORE:-keystore.jks}; KEYSTORE_PASS=${KEYSTORE_PASS:-dotamodel}; KEY_ALIAS=${KEY_ALIAS:-dotamodel}
[ -f ../app.html ] || { echo "app.html missing: run python build.py first"; exit 1; }
rm -rf build && mkdir -p build/gen build/classes build/res build/dex build/tools build/assets
cp ../app.html build/assets/index.html
"$AAPT2" compile --dir res -o build/res/res.zip
"$AAPT2" link -o build/unsigned.apk -I "$ANDROID_JAR" --manifest AndroidManifest.xml -A build/assets --java build/gen build/res/res.zip
javac -nowarn -Xlint:-options --release 8 -classpath "$ANDROID_JAR" -d build/classes \
  build/gen/com/dotamodel/app/R.java src/com/dotamodel/app/*.java 2>/dev/null || \
javac -nowarn -Xlint:-options -source 8 -target 8 -bootclasspath "$ANDROID_JAR" -classpath "$ANDROID_JAR" -d build/classes \
  build/gen/com/dotamodel/app/R.java src/com/dotamodel/app/*.java
java -cp "$DX_JAR" com.android.dx.command.Main --dex --min-sdk-version=24 --output=build/dex/classes.dex build/classes
(cd build/dex && zip -q ../unsigned.apk classes.dex)
if [ ! -f "$KEYSTORE" ]; then
  keytool -genkeypair -keystore "$KEYSTORE" -storetype JKS -storepass "$KEYSTORE_PASS" -keypass "$KEYSTORE_PASS" -alias "$KEY_ALIAS" \
    -keyalg RSA -keysize 2048 -validity 10000 -dname "CN=Dota 2 Model" >/dev/null 2>&1
fi
# align, then add the APK Signature Scheme v2 signature (Android 7+, minSdkVersion 24)
python3 tools/zipalign.py build/unsigned.apk build/aligned.apk
javac -nowarn -cp "$APKSIG_JAR" -d build/tools tools/Sign.java
java --add-exports java.base/sun.security.x509=ALL-UNNAMED -cp "$APKSIG_JAR:build/tools" Sign build/aligned.apk build/dota2-model.apk "$KEYSTORE" "$KEYSTORE_PASS" "$KEY_ALIAS"
echo "Built: android/build/dota2-model.apk"
