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

## Version 7.3 image and response controls

`StreamSettingsUiTest` compiles the exact production gear, scrollable panel, session
model and `ControllerSession` against Android API 36 and runs on Robolectric API
35 with native Canvas graphics. Run it with the same Gradle command and
`--tests qa.StreamSettingsUiTest` to isolate these four tests. They cover portrait
and landscape at 100/200% fonts, 48 dp gear, radio choices and their descriptions,
Host compatibility notice, pending/confirmed mode, consuming touches outside or
inside the panel, Back closing the menu before leaving fullscreen, rotation
restoring input, hiding/revealing the gear with fullscreen commands, and independent
session state. PNGs are written to `output/settings73-*.png`.

The renders use a synthetic PC background. They are not physical-phone screenshots
or evidence of live remote image quality or internet latency. The full Activity
lifecycle test uses an inert, authenticated-session stand-in and sends no network
traffic. Separate `run_stream_settings_checks.py` checks the production mode
contract and reconnect behavior; `viewport/run_viewport_checks.py` proves an
obsolete decoded capture cannot announce capability or confirm a chosen mode.

## Version 7.4 manual update checker

`AndroidUpdateDialogTest` adds seven production-component/Activity tests: no startup
query, one off-main manual lookup, duplicate suppression, installed/current/local
newer states, offline/rate-limit retry, explicit browser-only download and missing-browser errors, disposal
and stale-result handling, portrait/landscape and large-font native renders,
and home/session entries keeping an existing Peer open. The release source is
controlled; these tests make no live GitHub query and install no APK.
`output/update74-*.png` contains the native Canvas previews. The complete parser
and HTTPS boundary coverage is documented in `../ANDROID_UPDATE_TESTS.txt`.

## Version 7.5 decoded-pixel fidelity

`NativeImageFidelityTest` uses the real native JPEG codec and Canvas with the
production `RemoteScreenView`. It verifies full 3840×2160 ARGB_8888 decode and
pixel-for-pixel equality of a native-size 640×480 visible crop (307,200 pixels),
then checks zoom/fit restoration and no default enlargement of smaller JPEGs.
The generated JPEG contains synthetic 13 px text and color strokes; this proves
no additional receiver resize/filter loss at 1:1, not fidelity to the PC before
JPEG compression or physical-phone/GPU performance. Native PNGs are named
`output/image75-*.png`. `StreamSettingsUiTest` also exercises the four local zoom
buttons through real touch dispatch and scroll, with the precise device-limit
resolution notice. See `../IMAGE_FIDELITY_TESTS.txt` for policy and JVM coverage.
