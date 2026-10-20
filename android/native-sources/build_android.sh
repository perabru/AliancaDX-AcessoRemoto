#!/usr/bin/env bash
set -euo pipefail
NATIVE_SOURCE_DIR=$(cd "$(dirname "$0")" && pwd)
NATIVE_BUILD_DIR=${NATIVE_BUILD_DIR:-"$NATIVE_SOURCE_DIR/.build"}
NATIVE_ABI=${1:-arm64-v8a}
: "${ANDROID_NDK_ROOT:?Set ANDROID_NDK_ROOT to Android NDK r27c or newer}"
: "${PROTOC:?Set PROTOC to an executable protoc 30.2}"
case "$NATIVE_ABI" in
 arm64-v8a) NATIVE_TARGET=aarch64-linux-android; NATIVE_CC=aarch64-linux-android26-clang ;;
 x86_64) NATIVE_TARGET=x86_64-linux-android; NATIVE_CC=x86_64-linux-android26-clang ;;
 *) echo 'Supported ABI: arm64-v8a or x86_64' >&2; exit 2 ;;
esac
NATIVE_TOOLCHAIN="$ANDROID_NDK_ROOT/toolchains/llvm/prebuilt/linux-x86_64"
if [ ! -d "$NATIVE_BUILD_DIR/EasyTier-2.6.4" ]; then
 if [ -f "$NATIVE_SOURCE_DIR/easytier-2.6.4-source.tar.gz" ] && [ -f "$NATIVE_SOURCE_DIR/easytier-2.6.4-dependency-sources.tar.xz" ]; then
  python3 "$NATIVE_SOURCE_DIR/prepare_native_source.py" "$NATIVE_SOURCE_DIR" "$NATIVE_BUILD_DIR"
 else
  echo "Native sources are not prepared. Run: python3 $NATIVE_SOURCE_DIR/bootstrap_native_sources.py --destination $NATIVE_BUILD_DIR" >&2
  exit 2
 fi
fi
export RUSTUP_TOOLCHAIN=1.95.0
export CC="$NATIVE_TOOLCHAIN/bin/$NATIVE_CC"
export AR="$NATIVE_TOOLCHAIN/bin/llvm-ar"
export "CARGO_TARGET_$(echo "$NATIVE_TARGET" | tr '[:lower:]-' '[:upper:]_')_LINKER=$CC"
export RUSTFLAGS='-C link-arg=-Wl,-z,max-page-size=16384 -C link-arg=-Wl,-z,common-page-size=16384'
export CARGO_BUILD_JOBS=${CARGO_BUILD_JOBS:-4}
cd "$NATIVE_BUILD_DIR/EasyTier-2.6.4"
cargo build -p easytier-ffi --offline --target "$NATIVE_TARGET" --release
NATIVE_LIB_DIR="$NATIVE_SOURCE_DIR/../app/src/main/jniLibs/$NATIVE_ABI"
mkdir -p "$NATIVE_LIB_DIR"
cp "target/$NATIVE_TARGET/release/libeasytier_ffi.so" "$NATIVE_LIB_DIR/"
"$CC" -std=c11 -D_POSIX_C_SOURCE=200809L -fPIC -fvisibility=hidden \
 -Wall -Wextra -Werror -O2 -shared "$NATIVE_SOURCE_DIR/easytier_jni.c" \
 -L"$NATIVE_LIB_DIR" -leasytier_ffi -Wl,--no-undefined -Wl,-soname,libeasytier_android_jni.so \
 -Wl,-z,max-page-size=16384 -Wl,-z,common-page-size=16384 \
 -Wl,-z,relro -Wl,-z,now -Wl,-z,noexecstack \
 -o "$NATIVE_LIB_DIR/libeasytier_android_jni.so"
"$NATIVE_TOOLCHAIN/bin/llvm-strip" --strip-unneeded "$NATIVE_LIB_DIR/libeasytier_android_jni.so"
"$NATIVE_TOOLCHAIN/bin/llvm-readelf" -lW "$NATIVE_LIB_DIR/libeasytier_ffi.so"
"$NATIVE_TOOLCHAIN/bin/llvm-readelf" -lW "$NATIVE_LIB_DIR/libeasytier_android_jni.so"
