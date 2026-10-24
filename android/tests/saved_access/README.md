# Saved-access Activity checks

This isolated Robolectric project compiles the actual Controller `MainActivity`,
`ControllerSession`, registry, AES-GCM vault and connection helpers. It executes the
real Android view hierarchy, `SharedPreferences.commit`, form callbacks, dialogs,
activity recreation and four-session routing on API 35.

Use JDK 17, Android SDK API 36 and the existing Gradle wrapper:

```sh
cd android
./gradlew -p tests/saved_access test
```

`ANDROID_HOME`, `ANDROID_SDK_ROOT`, or `android/local.properties` supplies the SDK.
The first run downloads Kotlin, JUnit, Robolectric and its API 35 runtime from Maven.
Reports appear under `build/reports/tests/test/`.

The build generates test-only `BuildConfig` and zero-valued `R` symbols. A zero logo
resource disables the decorative image; it does not replace form/session logic.
The vault uses actual JCE AES-GCM with a synthetic in-memory fixture key and cipher
store. Production Android `AtomicFile` behavior has separate checks in
`tests/mouse_controls`; physical AndroidKeyStore behavior needs a device.

These tests do not claim a live Host, actual mesh/TLS authentication, phone or
emulator. They verify that authentication is not started without the current Host
password, cancellation preserves a saved code and clears the password, activity
recreation reloads saved metadata/code, invitation updates preserve identity and
history, stale/deleted/corrupt card snapshots never select an old endpoint, and
saved-card routing preserves the four independent owners.

The pure production coordinator and vault also have separate JVM runners with
fresh write/read processes:

```sh
python3 tests/run_saved_access_checks.py KOTLIN_LIB ANDROID_JAR JSON_JAR OUTPUT_JAR
python3 tests/run_mesh_code_vault_checks.py --kotlin-lib KOTLIN_LIB --android-jar ANDROID_JAR --java JAVA --output OUTPUT_JAR
```
