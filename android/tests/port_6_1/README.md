# Controller ↔ Desktop 6.1 interoperability

The portable runner uses the current production Kotlin files and a separate Python Desktop6.1
directory. No packaged historical Python reference is used. It excludes the old Android Host
role because this deliverable controls a PC from Android.

Requires JDK17+, Gradle8.11.1's Kotlin compiler jars, `org.json:json:20240303`, Python3.11+,
Pillow and cryptography:

```sh
python tests/port_6_1/run_controller_interop.py \
  --desktop ../desktop \
  --kotlin-lib /path/to/gradle-8.11.1/lib \
  --json-jar /path/to/json-20240303.jar \
  --output /tmp/minianydesk-controller-qa
```

The report contains source hashes, actual executed test counts, skips and failures. Tests use
real local TLS, exact Python Host dispatch, streaming file transfers with SHA-256, read-only
ALPN status, deadlines/backpressure, pairing-code/HMAC vectors, and independently compared
TOML configurations. The JVM Android provider doubles are never compiled into an APK.

Separate Android instrumentation is in `app/src/androidTest`. It uses Android's real TLS,
bitmap decoder, AndroidKeyStore, and a real content provider holding synthetic files. The native-lab
fixture runs an unrelated local relay, disables P2P, and forwards to the Python Host. These
results must be reported separately; neither a local relay nor an emulator proves access
between physical providers or availability of public relays.

For the native lab, use a Linux host, the official Linux x86_64 EasyTier2.6.4 core, and an
API35 x86_64 emulator. Build and install the separate Debug target and its test APK;
the 6.1.1 Release package has no `.debug` suffix and is not this lab's default target.
Wait until `adb shell getprop sys.boot_completed` returns `1`, then run in the same
network namespace as the emulator and its ADB server:

```sh
./gradlew :app:assembleControladorDebug :app:assembleControladorDebugAndroidTest
adb -s emulator-5554 install -r app/build/outputs/apk/controlador/debug/app-controlador-debug.apk
adb -s emulator-5554 install -r -t app/build/outputs/apk/androidTest/controlador/debug/app-controlador-debug-androidTest.apk
python tests/port_6_1/run_native_lab.py \
  --desktop ../desktop \
  --core /path/to/easytier-linux-x86_64-v2.6.4/easytier-core \
  --adb /path/to/android-sdk/platform-tools/adb \
  --target-package br.com.perabru.minianydesk.controlador.debug \
  --target-apk app/build/outputs/apk/controlador/debug/app-controlador-debug.apk \
  --output /tmp/minianydesk-native-qa
```

The default instrumentation component is
`br.com.perabru.minianydesk.controlador.debug.test/br.com.perabru.minianydesk.PortInteropInstrumentation`.
`--target-package` also determines the package stopped on timeout and hashed in the report;
`--test-package` overrides the default `TARGET_PACKAGE.test` when needed. `--target-apk`
supports a Debug APK built in an isolated directory. The driver requires its hash to match
the installed target before reporting success; future reports use
`verified_local_target_apk_sha256` and `local_target_matches_installed`.

These are reproduction instructions, not an Android 6.1.1 runtime pass. The existing
`evidence/native-runtime-report.json` belongs to the historical 6.1.0 Release hash and
must not be relabeled or overwritten as a 6.1.1 result. The separate API36 attempt did
not finish booting; its limited-environment result is recorded in
`docs/evidencias_6_1_1/runtime-api36/runtime-scope.json` at the project root.

The driver verifies that the target is an emulator, creates and shuts down its local relay
and Host, disables P2P, removes private generated test inputs, and writes a report containing
only scenario results and source/binary hashes. Scripted file selection uses a local synthetic
Android provider through the framework's `ContentResolver.wrap` and real file descriptors;
this does not test provider discovery over Binder or the interactive system document picker.
Document-URI classification is fixture setup using a registered authority; every query and
file operation remains on the wrapped local synthetic provider.

If the synthetic emulator's Wi-Fi carrier is unavailable, the optional `--adb-reverse`
flag connects the Android native core to the same local relay over an ADB loopback TCP
carrier. The report labels this explicitly. It still runs real Android JNI, EasyTier relay,
end-to-end TLS and files, but does not validate the guest's Wi-Fi/NAT path or public WAN.
