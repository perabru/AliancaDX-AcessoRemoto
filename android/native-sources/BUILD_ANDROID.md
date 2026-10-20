# Rebuild native Android transport from a public checkout

The Git repository contains authored JNI/build sources, public metadata and
licenses, without prebuilt Android libraries or large third-party archives.
The network core is unmodified upstream EasyTier 2.6.4 (LGPLv3), pinned to commit
`8428a89d2dabc94c97d370ec607c6ca142473626`. `upstream-source.json` pins the official
archive SHA-256. `Cargo.lock.upstream` and `Cargo.toml.upstream` are exact copies
of that commit's files. `source-inventory.json` pins 669 dependency downloads.

Requirements: Linux x86_64, Python 3.12+, Rust 1.95.0, Android NDK r27c, protoc 30.2,
JDK 17, Android SDK 36 and Build Tools 35.0.0. Expanded third-party sources occupy
about 1 GiB; allow several additional GiB for downloads and optimized Rust builds.
Use official toolchains from rust-lang.org, developer.android.com/ndk/downloads
and github.com/protocolbuffers/protobuf/releases/tag/v30.2. The NDK version used
for historical binaries was 27.2.12479018; newer toolchains can change output bytes.

From `android/`:

```sh
rustup toolchain install 1.95.0 --profile minimal
rustup target add --toolchain 1.95.0 aarch64-linux-android x86_64-linux-android
python3 native-sources/bootstrap_native_sources.py --verify-manifest
python3 native-sources/bootstrap_native_sources.py
export ANDROID_NDK_ROOT=/absolute/path/to/android-ndk-r27c
export PROTOC=/absolute/path/to/protoc-30.2/bin/protoc
bash native-sources/build_android.sh arm64-v8a
bash native-sources/build_android.sh x86_64
python3 native-sources/verify_binaries.py \
  --readelf "$ANDROID_NDK_ROOT/toolchains/llvm/prebuilt/linux-x86_64/bin/llvm-readelf"
./gradlew :app:assembleControladorRelease :app:lintControladorRelease
```

The explicit bootstrap downloads from the official GitHub commit/crates.io
locations recorded in the inventory and rejects incorrect SHA-256, unsafe
archive paths and modified lock metadata. It downloads no prebuilt Android
library. The upstream source tar also carries unrelated Windows driver and GUI
keystore files; extraction omits them. Download caches and expanded trees stay
outside Git. Existing prepared source edits are retained; use a new destination
for a clean preparation. `--destination` must match `NATIVE_BUILD_DIR` when a
custom path is used with `build_android.sh`.

The preparation selects the EasyTier/rpc-build/FFI workspace, replaces registry
and Git sources with verified local paths, removes the host mold linker choice
and selects smoltcp/socks5/websocket/aes-gcm/tun/magic-dns Cargo features. Rust
runtime source files are not edited. Cargo then builds with `--offline`.
Upstream Android types require the tun feature at compile time; the application
still runs `no_tun=true`, without a system VPN or TUN descriptor.

The build emits `libeasytier_ffi.so` and `libeasytier_android_jni.so` in each
`app/src/main/jniLibs/{arm64-v8a,x86_64}/` directory. Both ELF LOAD segments must
be aligned to at least 0x4000. `verify_binaries.py` checks all four libraries,
required exports, stack flags and dynamic dependencies, writing a new local
`NATIVE_REBUILD_VERIFICATION.json`. It does not overwrite historical provenance
or infer compiler versions or actual device test results.

Build the Android APK only after those libraries exist. Gradle preserves their
bytes and uses uncompressed native packaging; final APK verification also needs
16 KiB ZIP alignment:

```sh
zipalign -c -P 16 -v 4 app/build/outputs/apk/controlador/release/app-controlador-release-unsigned.apk
```

The Gradle release is unsigned. You can build/install a separate Debug package
or sign your own modified APK. Only the owner with external private keys can
use the production signing pipeline to preserve official update identity.
Public certificates and lineage are provided, never private signing material.

## Historical complete-source distribution

The original downloadable source package contained two expanded-source archive
containers. `NATIVE_PROVENANCE.json` retains their hashes and the original
native artifact hashes; those archives and libraries are intentionally absent
from this Git checkout. The legacy `prepare_native_source.py SOURCES DEST`
mode still accepts the exact original archive files. The public bootstrap
retrieves equivalent upstream files/package contents individually; its new
container download hash is not the hash of the original sanitized/repacked tar.
It does not promise byte-identical rebuilt ELF binaries across paths/toolchains.

See `JNI_README.md` for JNI readiness and mock-boundary semantics. Building and
inspecting an ELF file does not establish Android installation, approved input,
public relay reachability or desktop compatibility on a real device. Fresh
rebuilds need their own tests. The application can be relinked with modified
LGPL code, subject to the component licenses.
