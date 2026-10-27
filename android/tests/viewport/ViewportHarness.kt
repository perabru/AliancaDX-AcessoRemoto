package br.com.perabru.minianydesk

import kotlin.math.abs

/** Executes production geometry. No Android rendering or physical networks are simulated as tested. */
fun main() {
    var count = 0
    fun test(name: String, body: () -> Unit) { body(); count++; println("OK:$name") }
    fun close(actual: Double, expected: Double, tolerance: Double = 1e-8) { check(abs(actual - expected) <= tolerance) { "$actual != $expected" } }
    val bounds = ScreenGeometry.Fit(8, 16, 640, 480)
    val base = ScreenGeometry.fit(1920, 1080, bounds.width, bounds.height, bounds.left, bounds.top)!!
    val start = ScreenGeometry.viewport(base, bounds)!!
    test("fit and full-desktop inclusive coordinates") {
        close(start.zoom, 1.0)
        check(start.width == 640.0 && start.height == 360.0)
        check(ScreenGeometry.normalizedPoint(start, start.left.toFloat(), start.top.toFloat()) == ScreenGeometry.Point(0.0, 0.0))
        check(ScreenGeometry.normalizedPoint(start, start.right.toFloat(), start.bottom.toFloat()) == ScreenGeometry.Point(1.0, 1.0))
        check(ScreenGeometry.normalizedPoint(start, 8f, 16f) == null)
    }
    test("zoom retains the physical PC pixel under the finger midpoint") {
        val before = ScreenGeometry.normalizedPoint(start, 328f, 256f)!!
        val zoomed = ScreenGeometry.transformViewport(base, bounds, start, 2.0, 328.0, 256.0)!!
        val after = ScreenGeometry.normalizedPoint(zoomed, 328f, 256f)!!
        close(before.x, after.x); close(before.y, after.y)
        check(ScreenGeometry.physicalPoint(-1920, -1080, 5760, 2160, before) == ScreenGeometry.physicalPoint(-1920, -1080, 5760, 2160, after))
    }
    test("two-finger drag moves viewport without changing the physical pixel below the fingers") {
        val zoomed = ScreenGeometry.transformViewport(base, bounds, start, 3.0, 328.0, 256.0)!!
        val original = ScreenGeometry.normalizedPoint(zoomed, 328f, 256f)!!
        val moved = ScreenGeometry.transformViewport(base, bounds, zoomed, 1.0, 328.0, 256.0, 378.0, 286.0)!!
        val after = ScreenGeometry.normalizedPoint(moved, 378f, 286f)!!
        close(original.x, after.x); close(original.y, after.y)
        close(moved.zoom, zoomed.zoom)
    }
    test("pinch and pan update together") {
        val zoomed = ScreenGeometry.transformViewport(base, bounds, start, 2.0, 328.0, 256.0)!!
        val before = ScreenGeometry.normalizedPoint(zoomed, 200f, 200f)!!
        val moved = ScreenGeometry.transformViewport(base, bounds, zoomed, 1.5, 200.0, 200.0, 210.0, 230.0)!!
        val after = ScreenGeometry.normalizedPoint(moved, 210f, 230f)!!
        close(before.x, after.x); close(before.y, after.y)
    }
    test("panning to both ends reveals every corner and cannot lose the desktop") {
        val zoomed = ScreenGeometry.transformViewport(base, bounds, start, 4.0, 328.0, 256.0)!!
        val upperLeft = ScreenGeometry.transformViewport(base, bounds, zoomed, 1.0, 0.0, 0.0, 1e9, 1e9)!!
        close(upperLeft.left, bounds.left.toDouble()); close(upperLeft.top, bounds.top.toDouble())
        check(ScreenGeometry.normalizedPoint(upperLeft, bounds.left.toFloat(), bounds.top.toFloat()) == ScreenGeometry.Point(0.0, 0.0))
        val bottomRight = ScreenGeometry.transformViewport(base, bounds, zoomed, 1.0, 0.0, 0.0, -1e9, -1e9)!!
        close(bottomRight.right, bounds.right.toDouble()); close(bottomRight.bottom, bounds.bottom.toDouble())
        check(ScreenGeometry.normalizedPoint(bottomRight, bounds.right.toFloat(), bounds.bottom.toFloat()) == ScreenGeometry.Point(1.0, 1.0))
    }
    test("out-of-picture drag remains clamped after zoom") {
        val zoomed = ScreenGeometry.transformViewport(base, bounds, start, 3.0, 328.0, 256.0)!!
        check(ScreenGeometry.normalizedPoint(zoomed, -1e9f, -1e9f) == null)
        check(ScreenGeometry.normalizedPoint(zoomed, -1e9f, 1e9f, true) == ScreenGeometry.Point(0.0, 1.0))
    }
    test("zoom limits and fit reset") {
        val maximum = ScreenGeometry.transformViewport(base, bounds, start, 1e9, 328.0, 256.0)!!
        close(maximum.zoom, ScreenGeometry.MAX_ZOOM)
        val minimum = ScreenGeometry.transformViewport(base, bounds, maximum, 1e-9, 328.0, 256.0)!!
        check(minimum == start)
    }
    test("pinch gesture sequence has no cumulative pixel drift without bounds clamping") {
        var current = ScreenGeometry.transformViewport(base, bounds, start, 2.0, 328.0, 256.0)!!
        val original = current
        repeat(1000) {
            current = ScreenGeometry.transformViewport(base, bounds, current, 1.01, 328.0, 256.0, 329.0, 257.0)!!
            current = ScreenGeometry.transformViewport(base, bounds, current, 1.0 / 1.01, 329.0, 257.0, 328.0, 256.0)!!
        }
        close(current.left, original.left, 1e-6); close(current.top, original.top, 1e-6)
        close(current.zoom, original.zoom, 1e-6)
    }
    test("many aspect ratios, padding, zoom levels and pans stay bounded") {
        for (imageWidth in listOf(1, 1920, 5760, 32768)) for (imageHeight in listOf(1, 1080, 2400, 32768)) {
            for (viewerWidth in listOf(1, 390, 800)) for (viewerHeight in listOf(1, 240, 600)) {
                val box = ScreenGeometry.Fit(13, 17, viewerWidth, viewerHeight)
                val fitted = ScreenGeometry.fit(imageWidth, imageHeight, viewerWidth, viewerHeight, box.left, box.top)!!
                for (zoom in listOf(1.0, 1.2, 3.0, 6.0)) for (position in listOf(-1e9, 0.0, 1e9)) {
                    val view = ScreenGeometry.viewport(fitted, box, zoom, position, position)!!
                    if (view.width > box.width) { check(view.left <= box.left && view.right >= box.right) }
                    else { close(view.left, box.left + (box.width - view.width) / 2) }
                    if (view.height > box.height) { check(view.top <= box.top && view.bottom >= box.bottom) }
                    else { close(view.top, box.top + (box.height - view.height) / 2) }
                    for (x in listOf(view.left, view.right)) for (y in listOf(view.top, view.bottom)) {
                        val point = ScreenGeometry.normalizedPoint(view, x.toFloat(), y.toFloat(), true)!!
                        check(point.x in 0.0..1.0 && point.y in 0.0..1.0)
                    }
                }
            }
        }
    }
    test("nonfinite values and invalid viewports never produce a remote target") {
        for (value in listOf(Double.NaN, Double.POSITIVE_INFINITY, Double.NEGATIVE_INFINITY)) {
            check(ScreenGeometry.viewport(base, bounds, value) == null)
            check(ScreenGeometry.viewport(base, bounds, 2.0, value) == null)
            check(ScreenGeometry.transformViewport(base, bounds, start, value, 0.0, 0.0) == null)
            check(ScreenGeometry.transformViewport(base, bounds, start, 2.0, value, 0.0) == null)
            check(ScreenGeometry.normalizedPoint(start.copy(left=value), 0f, 0f, true) == null)
            check(ScreenGeometry.normalizedPoint(start.copy(width=value), 0f, 0f, true) == null)
        }
        check(ScreenGeometry.transformViewport(base, bounds, start, 0.0, 0.0, 0.0) == null)
        check(ScreenGeometry.transformViewport(base, bounds, start, -1.0, 0.0, 0.0) == null)
        check(ScreenGeometry.viewport(base.copy(width=0), bounds) == null)
    }
    test("native 4K maps each decoded pixel once with integer origin on a small canvas") {
        val box = ScreenGeometry.Fit(7, 11, 390, 780)
        val native = ScreenGeometry.nativeViewport(3840, 2160, box)!!
        close(native.width, 3840.0); close(native.height, 2160.0)
        check(native.left % 1.0 == 0.0 && native.top % 1.0 == 0.0)
        check(ScreenGeometry.isNativeScale(native, 3840, 2160))
        val first = ScreenGeometry.normalizedPoint(native, native.left.toFloat(), native.top.toFloat())!!
        val last = ScreenGeometry.normalizedPoint(native, native.right.toFloat(), native.bottom.toFloat())!!
        check(ScreenGeometry.physicalPoint(-3840, 0, 7680, 2160, first) == Pair(-3840, 0))
        check(ScreenGeometry.physicalPoint(-3840, 0, 7680, 2160, last) == Pair(3839, 2159))
    }
    test("native small images stay centered and explicit larger zoom retains normalized targets") {
        val box = ScreenGeometry.Fit(0, 0, 1920, 1080)
        val native = ScreenGeometry.nativeViewport(640, 360, box)!!
        check(native == ScreenGeometry.Viewport(640.0, 360.0, 640.0, 360.0, 1.0))
        val base = ScreenGeometry.nativeBase(640, 360, box)!!
        val enlarged = ScreenGeometry.transformViewport(base, box, native, 3.0, 959.5, 539.5)!!
        close(enlarged.width, 1920.0); close(enlarged.height, 1080.0)
        check(ScreenGeometry.normalizedPoint(enlarged, 959.5f, 539.5f) == ScreenGeometry.Point(.5, .5))
        check(!ScreenGeometry.isNativeScale(enlarged, 640, 360))
    }
    test("native focus is clamped and one-to-one panning is snapped without scaling") {
        val box = ScreenGeometry.Fit(12, 20, 640, 360)
        val native = ScreenGeometry.nativeViewport(3840, 2160, box, ScreenGeometry.Point(1.0, 1.0))!!
        close(native.right, box.right.toDouble()); close(native.bottom, box.bottom.toDouble())
        val rounded = ScreenGeometry.alignNative(native.copy(left=native.left + .3, top=native.top - .2), 3840, 2160)
        check(rounded.left % 1.0 == 0.0 && rounded.top % 1.0 == 0.0 && rounded.width == native.width)
        check(ScreenGeometry.nativeViewport(0, 2, box) == null)
        check(ScreenGeometry.nativeViewport(3840, 2160, box, ScreenGeometry.Point(Double.NaN, .5)) == null)
    }
    println("VIEWPORT_OK:$count")
}
