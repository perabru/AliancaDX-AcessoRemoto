package android.os

/** Selects Android's ALPN availability in headless JVM transport tests. */
object Build {
    object VERSION {
        @JvmField var SDK_INT: Int = 35
    }
    object VERSION_CODES {
        const val Q: Int = 29
    }
}
