package br.com.perabru.minianydesk

/** Production allocation decision, no bitmap allocation or physical-device inference. */
object BitmapDecodePolicyHarness {
    @JvmStatic fun main(args: Array<String>) {
        var count = 0
        fun test(name: String, run: () -> Unit) { run(); count++; println("PASS $name") }
        val mib = 1024L * 1024
        test("4K preserves every decoded pixel on a 256MiB or larger heap") {
            check(3840L * 2160 > 8_000_000)
            for (heap in listOf(256, 384, 512, 1024)) check(BitmapDecodePolicy.sampleSize(3840, 2160, heap * mib) == 1)
        }
        test("low heap reduces 4K allocation but retains full HD when it fits") {
            check(BitmapDecodePolicy.sampleSize(3840, 2160, 128 * mib) == 2)
            check(BitmapDecodePolicy.sampleSize(1920, 1080, 128 * mib) == 1)
            check(BitmapDecodePolicy.sampleSize(3840, 2160, 64 * mib) == 2)
            check(BitmapDecodePolicy.sampleSize(1920, 1080, 64 * mib) == 1)
        }
        test("rounding odd dimensions cannot exceed the actual ARGB allowance") {
            for (heap in listOf(32L, 1024L, 32 * mib, 64 * mib, 128 * mib, 256 * mib)) {
                for (w in listOf(1, 1919, 3841, 4097, 32768)) for (h in listOf(1, 1081, 2161, 4097, 32768)) {
                    val sample = BitmapDecodePolicy.sampleSize(w, h, heap)
                    check(sample > 0 && (sample and (sample - 1)) == 0)
                    val dw = (w.toLong() + sample - 1) / sample; val dh = (h.toLong() + sample - 1) / sample
                    check(dw * dh <= minOf(BitmapDecodePolicy.MAX_DECODED_PIXELS, heap / 32))
                    check(dw <= 4096 && dh <= 4096)
                }
            }
        }
        test("huge heap never overrides the pixel or texture dimension bounds") {
            check(BitmapDecodePolicy.sampleSize(32768, 1, Long.MAX_VALUE) == 8)
            check(BitmapDecodePolicy.sampleSize(32768, 32768, Long.MAX_VALUE) >= 16)
            check(BitmapDecodePolicy.sampleSize(4096, 2048, Long.MAX_VALUE) == 1)
        }
        test("invalid bounds and impossible allocation budgets fail before decode") {
            for ((w, h, heap) in listOf(Triple(0, 1080, 256 * mib), Triple(32769, 1, 256 * mib), Triple(1, -1, 256 * mib), Triple(1, 1, 0L), Triple(1, 1, 31L))) {
                var rejected = false
                try { BitmapDecodePolicy.sampleSize(w, h, heap) } catch (_: IllegalArgumentException) { rejected = true }
                check(rejected)
            }
        }
        println("BITMAP_DECODE_POLICY_CHECKS_PASSED=$count")
    }
}
