package br.com.perabru.minianydesk

/** Stable per-bitmap allowance: at most 32 MiB ARGB and one eighth of the app heap.
 * The viewer still owns bounded newest-only slots; no decision depends on transient free heap. */
internal object BitmapDecodePolicy {
    const val MAX_DECODED_PIXELS = 8_388_608L
    const val MAX_DECODED_DIMENSION = 4096
    private const val BYTES_PER_PIXEL = 4L

    fun sampleSize(width: Int, height: Int, maxHeapBytes: Long): Int {
        require(width in 1..32768 && height in 1..32768 && maxHeapBytes >= 32)
        val allowance = minOf(MAX_DECODED_PIXELS, maxHeapBytes / 8 / BYTES_PER_PIXEL)
        var sample = 1
        while (decodedPixels(width, height, sample) > allowance ||
            (width + sample - 1) / sample > MAX_DECODED_DIMENSION ||
            (height + sample - 1) / sample > MAX_DECODED_DIMENSION) sample *= 2
        return sample
    }

    private fun decodedPixels(width: Int, height: Int, sample: Int): Long =
        ((width.toLong() + sample - 1) / sample) * ((height.toLong() + sample - 1) / sample)
}
