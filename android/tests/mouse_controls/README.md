# Native mouse controls verification

This isolated JVM harness compiles `MouseControlButton.kt` directly from the application source tree. It adds no runtime dependency to the Android app and requires no Android SDK or attached device.

Use JDK 17 and the existing Android Gradle wrapper:

```sh
cd android
./gradlew -p tests/mouse_controls test
```

The first run downloads the Kotlin/JUnit/Robolectric dependencies and the API 35 Android runtime from their Maven repositories. Configure any required network proxy outside the repository. The harness uses Robolectric 4.14.1 with native Android Canvas graphics; it does not use a browser or a replacement HTML drawing.

Six JUnit tests cover native rendering, five viewport sizes (320×568, 390×844, 844×390, 568×320, 240×240), safe insets, at least 48 dp touch targets, 100/130/200% font sizes in portrait and landscape, visible pressed state, translucency, cancellation, and preventing enabled/disabled pads or their gap from passing touches to the PC image. Disabled direct `performClick()` must also remain inert.

Reports are generated in `build/reports/tests/test/`; native Canvas PNGs are generated in `output/`. The fixture attaches a real Android framework view hierarchy to a Robolectric activity. It mirrors `UiKit.row(10)` and the compact viewer overlay geometry used by `ControllerSession`. If that layout changes, update the fixture as well.

The background is a synthetic PC image. These PNGs verify the production mouse component in a synthetic viewer; they are not screenshots of a physical device, the complete application, or a live remote PC session. Networking and gesture handling on the remote screen have separate tests.
