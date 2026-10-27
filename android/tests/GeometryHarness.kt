package br.com.perabru.minianydesk

/** Pure geometry/text checks. Android touch/render tests still require a device. */
fun main() {
    var count = 0
    fun test(name: String, body: () -> Unit) { body(); count++; println("OK:$name") }
    test("desktop fit in both axes") {
        val fit = ScreenGeometry.fit(5760, 2160, 390, 240)!!
        check(fit == ScreenGeometry.Fit(0, 47, 390, 146))
        check(fit.right == 389 && fit.bottom == 192)
    }
    test("fit enlarges a smaller decoded image to the available area") {
        check(ScreenGeometry.fit(640, 360, 1920, 1080) == ScreenGeometry.Fit(0, 0, 1920, 1080))
        check(ScreenGeometry.fit(1, 1, 390, 240) == ScreenGeometry.Fit(75, 0, 240, 240))
        check(ScreenGeometry.fit(284, 160, 720, 1028) == ScreenGeometry.Fit(0, 311, 720, 405))
    }
    test("portrait fit in landscape viewer") {
        check(ScreenGeometry.fit(1080, 2400, 800, 500) == ScreenGeometry.Fit(287, 0, 225, 500))
    }
    test("inclusive visible edges reach physical desktop corners") {
        val fit = ScreenGeometry.fit(5760, 2160, 390, 240)!!
        val first = ScreenGeometry.normalizedPoint(fit, fit.left.toFloat(), fit.top.toFloat())!!
        val last = ScreenGeometry.normalizedPoint(fit, fit.right.toFloat(), fit.bottom.toFloat())!!
        check(first == ScreenGeometry.Point(0.0, 0.0) && last == ScreenGeometry.Point(1.0, 1.0))
        check(ScreenGeometry.physicalPoint(-1920, -1080, 5760, 2160, first) == Pair(-1920, -1080))
        check(ScreenGeometry.physicalPoint(-1920, -1080, 5760, 2160, last) == Pair(3839, 1079))
    }
    test("black bars reject a new touch; active drag clamps") {
        val fit = ScreenGeometry.fit(5760, 2160, 390, 240)!!
        check(ScreenGeometry.normalizedPoint(fit, 200f, 0f) == null)
        check(ScreenGeometry.normalizedPoint(fit, -50f, 500f, true) == ScreenGeometry.Point(0.0, 1.0))
    }
    test("negative origins and Python round ties") {
        check(ScreenGeometry.physicalPoint(-1, -1, 2, 4, ScreenGeometry.Point(.5, .5)) == Pair(-1, 1))
        check(ScreenGeometry.physicalPoint(-1920, -1080, 5760, 2160, ScreenGeometry.Point(.5, .5)) == Pair(960, 0))
    }
    test("invalid geometry is never an input target") {
        check(ScreenGeometry.fit(0, 1080, 390, 240) == null)
        check(ScreenGeometry.fit(1920, 1080, 0, 240) == null)
        check(ScreenGeometry.normalizedPoint(ScreenGeometry.Fit(0, 0, 2, 2), Float.NaN, 1f, true) == null)
        check(ScreenGeometry.physicalPoint(0, 0, 2, 2, ScreenGeometry.Point(Double.POSITIVE_INFINITY, .5)) == null)
    }
    test("varied aspect ratios stay inside the available viewer") {
        for (imageWidth in listOf(1, 192, 1920, 5760, 32768)) {
            for (imageHeight in listOf(1, 108, 1080, 2400)) {
                for (viewerWidth in listOf(1, 240, 390, 800)) {
                    for (viewerHeight in listOf(1, 100, 240, 500)) {
                        val fit = ScreenGeometry.fit(imageWidth, imageHeight, viewerWidth, viewerHeight, 8, 9)!!
                        check(fit.left >= 8 && fit.top >= 9 && fit.right < 8 + viewerWidth && fit.bottom < 9 + viewerHeight)
                        check(ScreenGeometry.normalizedPoint(fit, fit.right.toFloat(), fit.bottom.toFloat()) != null)
                    }
                }
            }
        }
    }
    test("ASCII upper-case and layout punctuation") {
        val keys = ScreenGeometry.asciiKeys("Aa@!? ")!!
        check(keys == listOf(ScreenGeometry.AsciiKey("a", true), ScreenGeometry.AsciiKey("a"),
            ScreenGeometry.AsciiKey("@"), ScreenGeometry.AsciiKey("!"), ScreenGeometry.AsciiKey("?"), ScreenGeometry.AsciiKey("space")))
        check(ScreenGeometry.asciiKeys((32..126).map { it.toChar() }.joinToString(""))!!.size == 95)
    }
    test("CRLF is one Enter and unsupported text rejects before sending") {
        check(ScreenGeometry.asciiKeys("a\r\nb\tc\rd\n")!!.map { it.key } == listOf("a", "enter", "b", "tab", "c", "enter", "d", "enter"))
        check(ScreenGeometry.asciiKeys("válido") == null)
        check(ScreenGeometry.asciiKeys("a\u0000b") == null)
        check(ScreenGeometry.asciiKeys("a".repeat(1025)) == null)
    }
    println("GEOMETRY_OK:$count")
}
