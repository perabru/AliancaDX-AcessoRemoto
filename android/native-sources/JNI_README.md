# Minimal EasyTier JNI adapter

`easytier_jni.c` links directly to EasyTier 2.6.4's `easytier-ffi` C ABI.
It does not require the Rust `jni` crate, Android logging, a Java VM in Rust,
a VPN service, or a TUN descriptor when the network uses `[flags] no_tun = true`.

## Kotlin contract

Keep the class name and native methods with R8. The methods work on a Kotlin
`object` (instance methods) or on Java static methods; the receiver is unused.

```kotlin
package com.easytier.jni

object EasyTierJNI {
    init {
        System.loadLibrary("easytier_ffi")
        System.loadLibrary("easytier_android_jni")
    }

    external fun parseConfig(config: String): Int
    external fun runNetworkInstance(config: String): Int
    external fun retainNetworkInstance(instanceNames: Array<String>?): Int
    external fun stopAllInstances(): Int
    external fun collectNetworkInfos(maxLength: Int = 100): String?
    external fun getLastError(): String?
}
```

```proguard
-keep class com.easytier.jni.EasyTierJNI { *; }
```

- Integer methods return `0` for success and upstream's failure code, normally
  `-1`, on failure. `getLastError()` returns a generic English message or null.
- There are no deliberate `ThrowNew` calls. An allocation failure in the JVM
  can still raise the JVM's usual `OutOfMemoryError`.
- `retainNetworkInstance(null)`, an empty array, and `stopAllInstances()` all
  remove every instance, as in upstream. Null array elements are rejected.
- Status is `{"map":{"instance-name":{...upstream-running-info...}}}`.
  `maxLength` must be 1..100. A successful request with no ready instance returns
  `{"map":{}}`. Failure returns null; read `getLastError()` immediately.
- Configuration supports ordinary Unicode, including surrogate pairs. Embedded
  NUL and malformed UTF-16 are rejected before calling Rust. Rust-owned strings
  returned by status are freed in success and partial-failure paths.
- The wrapper serializes FFI access, wipes its temporary copy of a configuration,
  and never logs config/status/error contents. Upstream errors are deliberately
  not exposed because TOML error source excerpts can contain the pairing secret.
  Do not log the configuration or full network status from Kotlin either.
- `getLastError()` is process-global, matching the upstream manager. Invoke the
  public operations on one application executor so another successful operation
  does not clear an error before the caller reads it.

## Build with NDK r27+ (API 26)

First compile `libeasytier_ffi.so` for the same ABI. Port forwarding is enabled
by the EasyTier `socks5` Cargo feature. Use release builds, retain EasyTier's
licenses, and preserve the exact desktop-compatible EasyTier version.

Set `NDK_TOOLCHAIN` to the NDK's `toolchains/llvm/prebuilt/linux-x86_64` directory,
and `EASYTIER_FFI_ABI_DIR` to the directory containing the ABI's Rust `.so`.

For arm64-v8a:

```sh
"$NDK_TOOLCHAIN/bin/aarch64-linux-android26-clang" \
  -std=c11 -D_POSIX_C_SOURCE=200809L -fPIC -fvisibility=hidden \
  -Wall -Wextra -Werror -O2 -shared easytier_jni.c \
  -L"$EASYTIER_FFI_ABI_DIR" -leasytier_ffi \
  -Wl,-soname,libeasytier_android_jni.so \
  -Wl,-z,max-page-size=16384 -Wl,-z,common-page-size=16384 \
  -Wl,-z,relro -Wl,-z,now -Wl,-z,noexecstack \
  -o libeasytier_android_jni.so
```

For x86_64, substitute `x86_64-linux-android26-clang`. For armeabi-v7a,
substitute `armv7a-linux-androideabi26-clang`. Both `.so` files must be placed
in `app/src/main/jniLibs/<abi>/`. The JNI `.so` has an explicit `DT_NEEDED`
dependency on `libeasytier_ffi.so`; load FFI before JNI as shown above.
Check both `.so` files with `llvm-readelf -lW`: each `LOAD` alignment must be
at least `0x4000` on arm64/x86_64. The Rust link must independently set the same
16 KiB page-size flags. Final APK native-library ZIP entries must also be aligned
by the Android packaging tools.

## Runtime readiness and no-TUN forwarding

`runNetworkInstance()` succeeds after spawning EasyTier's dedicated runtime
thread. It does **not** wait for relay access or the local forwarding listener.
No Tokio initialization is required from JNI. Poll status on a background
executor; an empty map during startup is normal. Then attempt the application's
TLS session on the configured loopback forwarding port with bounded retries.
The remote desktop TLS handshake, certificate check, password and Host approval
remain authoritative. A local TCP connection alone does not prove the remote
PC can be reached.

On Android, the mobile TUN receiver is an asynchronous task and does not block
the instance when no descriptor is sent. `no_tun = true` therefore needs neither
`VpnService.prepare()` nor `setTunFd()`. A loopback-only TCP port forward is
enough for the controller's remote desktop protocol. Configure `bind_addr` as
`127.0.0.1:<local-port>` and `dst_addr` as the desktop Host's mesh IP/port.

## Local JNI boundary tests

The `tests/` directory contains a fake FFI and a Java harness. They exercise the
real native JNI adapter in a real JVM, including Unicode input/output, malformed
input, null/empty status, partial-allocation cleanup and concurrent calls. They
do not establish a real EasyTier network and are not evidence of Android device
or public-relay compatibility. Build/run commands are in `tests/run_tests.py`.
