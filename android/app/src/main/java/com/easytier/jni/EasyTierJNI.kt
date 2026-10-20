package com.easytier.jni

/** ABI-compatible bridge to the bundled EasyTier 2.6.4 core. No system VPN. */
object EasyTierJNI {
    @Volatile private var loaded = false

    @Synchronized fun ensureLoaded() {
        if (loaded) return
        System.loadLibrary("easytier_ffi")
        System.loadLibrary("easytier_android_jni")
        loaded = true
    }

    @JvmStatic external fun parseConfig(config: String): Int
    @JvmStatic external fun runNetworkInstance(config: String): Int
    @JvmStatic external fun retainNetworkInstance(instanceNames: Array<String>?): Int
    @JvmStatic external fun collectNetworkInfos(maxLength: Int): String?
    @JvmStatic external fun getLastError(): String?
}
