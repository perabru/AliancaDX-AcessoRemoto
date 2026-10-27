package br.com.perabru.minianydesk

import kotlin.math.floor
import kotlin.math.round

/** Geometry uses inclusive pixel edges, just like the Windows capture rectangle. */
object ScreenGeometry {
    const val MAX_ZOOM = 6.0

    data class Fit(val left: Int, val top: Int, val width: Int, val height: Int) {
        val right: Int get() = left + width - 1
        val bottom: Int get() = top + height - 1
    }

    data class Point(val x: Double, val y: Double)

    /** The displayed desktop after local zoom/pan. Coordinates still address the entire capture. */
    data class Viewport(val left: Double, val top: Double, val width: Double, val height: Double, val zoom: Double) {
        val right: Double get() = left + width - 1.0
        val bottom: Double get() = top + height - 1.0
    }

    data class AsciiKey(val key: String, val shift: Boolean = false)

    fun fit(
        imageWidth: Int,
        imageHeight: Int,
        availableWidth: Int,
        availableHeight: Int,
        left: Int = 0,
        top: Int = 0
    ): Fit? {
        if (imageWidth <= 0 || imageHeight <= 0 || availableWidth <= 0 || availableHeight <= 0) return null
        val scale = minOf(1.0, availableWidth.toDouble() / imageWidth, availableHeight.toDouble() / imageHeight)
        val width = floor(imageWidth * scale).toInt().coerceIn(1, availableWidth)
        val height = floor(imageHeight * scale).toInt().coerceIn(1, availableHeight)
        return Fit(left + (availableWidth - width) / 2, top + (availableHeight - height) / 2, width, height)
    }

    /** A 1:1 base addresses decoded pixels, independent of fit rounding or Android density. */
    fun nativeBase(pixelWidth: Int, pixelHeight: Int, bounds: Fit): Fit? {
        if (pixelWidth <= 0 || pixelHeight <= 0 || bounds.width <= 0 || bounds.height <= 0) return null
        return Fit(bounds.left + (bounds.width - pixelWidth) / 2,
            bounds.top + (bounds.height - pixelHeight) / 2, pixelWidth, pixelHeight)
    }

    fun nativeViewport(pixelWidth: Int, pixelHeight: Int, bounds: Fit, center: Point = Point(.5, .5)): Viewport? {
        val base = nativeBase(pixelWidth, pixelHeight, bounds) ?: return null
        if (!center.x.isFinite() || !center.y.isFinite()) return null
        val left = round(bounds.left + (bounds.width - 1) / 2.0 - center.x.coerceIn(0.0, 1.0) * (pixelWidth - 1))
        val top = round(bounds.top + (bounds.height - 1) / 2.0 - center.y.coerceIn(0.0, 1.0) * (pixelHeight - 1))
        return alignNative(viewport(base, bounds, 1.0, left, top) ?: return null, pixelWidth, pixelHeight)
    }

    fun isNativeScale(viewport: Viewport, pixelWidth: Int, pixelHeight: Int): Boolean =
        kotlin.math.abs(viewport.width - pixelWidth) < .0001 && kotlin.math.abs(viewport.height - pixelHeight) < .0001

    /** No half-pixel translation when showing one decoded pixel per physical canvas pixel. */
    fun alignNative(viewport: Viewport, pixelWidth: Int, pixelHeight: Int): Viewport =
        if (isNativeScale(viewport, pixelWidth, pixelHeight)) viewport.copy(left = round(viewport.left), top = round(viewport.top))
        else viewport

    fun normalizedPoint(fit: Fit, x: Float, y: Float, clamp: Boolean = false): Point? {
        if (!x.isFinite() || !y.isFinite() || fit.width <= 0 || fit.height <= 0) return null
        if (!clamp && (x < fit.left || y < fit.top || x > fit.right || y > fit.bottom)) return null
        return Point(
            ((x.toDouble() - fit.left) / maxOf(1, fit.width - 1)).coerceIn(0.0, 1.0),
            ((y.toDouble() - fit.top) / maxOf(1, fit.height - 1)).coerceIn(0.0, 1.0)
        )
    }

    fun normalizedPoint(viewport: Viewport, x: Float, y: Float, clamp: Boolean = false): Point? {
        if (!x.isFinite() || !y.isFinite() || !validViewport(viewport)) return null
        if (!clamp && (x < viewport.left || y < viewport.top || x > viewport.right || y > viewport.bottom)) return null
        return Point(
            ((x - viewport.left) / maxOf(1.0, viewport.width - 1.0)).coerceIn(0.0, 1.0),
            ((y - viewport.top) / maxOf(1.0, viewport.height - 1.0)).coerceIn(0.0, 1.0)
        )
    }

    /** Keep a small image centered and a zoomed image covering the available area on each axis. */
    fun viewport(base: Fit, bounds: Fit, zoom: Double = 1.0, left: Double? = null, top: Double? = null): Viewport? {
        if (base.width <= 0 || base.height <= 0 || bounds.width <= 0 || bounds.height <= 0 ||
            !zoom.isFinite() || (left != null && !left.isFinite()) || (top != null && !top.isFinite())) return null
        val limitedZoom = zoom.coerceIn(1.0, MAX_ZOOM)
        val width = base.width * limitedZoom
        val height = base.height * limitedZoom
        fun position(size: Double, available: Int, origin: Int, requested: Double?): Double {
            val centered = origin + (available - size) / 2.0
            return if (size <= available) centered
            else (requested ?: centered).coerceIn(origin + available - size, origin.toDouble())
        }
        return Viewport(position(width, bounds.width, bounds.left, left), position(height, bounds.height, bounds.top, top), width, height, limitedZoom)
    }

    /** Move the pixel under the previous finger midpoint to the new midpoint while scaling. */
    fun transformViewport(base: Fit, bounds: Fit, current: Viewport, scale: Double,
                          fromX: Double, fromY: Double, toX: Double = fromX, toY: Double = fromY): Viewport? {
        if (!scale.isFinite() || scale <= 0 || !fromX.isFinite() || !fromY.isFinite() ||
            !toX.isFinite() || !toY.isFinite() || !validViewport(current)) return null
        val zoom = (current.zoom * scale).coerceIn(1.0, MAX_ZOOM)
        val x = (fromX - current.left) / maxOf(1.0, current.width - 1.0)
        val y = (fromY - current.top) / maxOf(1.0, current.height - 1.0)
        return viewport(base, bounds, zoom,
            toX - x * maxOf(1.0, base.width * zoom - 1.0),
            toY - y * maxOf(1.0, base.height * zoom - 1.0))
    }

    private fun validViewport(viewport: Viewport): Boolean = viewport.left.isFinite() && viewport.top.isFinite() &&
        viewport.width.isFinite() && viewport.height.isFinite() && viewport.width >= 1 && viewport.height >= 1 &&
        viewport.zoom.isFinite() && viewport.zoom in 1.0..MAX_ZOOM

    fun physicalPoint(left: Int, top: Int, width: Int, height: Int, point: Point): Pair<Int, Int>? {
        if (width <= 0 || height <= 0 || !point.x.isFinite() || !point.y.isFinite()) return null
        return Pair(
            left + round(point.x.coerceIn(0.0, 1.0) * (width - 1)).toInt(),
            top + round(point.y.coerceIn(0.0, 1.0) * (height - 1)).toInt()
        )
    }

    /** PyAutoGUI accepts lower-case letters and printable punctuation as key names. */
    fun asciiKeys(text: String): List<AsciiKey>? {
        if (text.length > 1024) return null
        val result = ArrayList<AsciiKey>(text.length)
        var index = 0
        while (index < text.length) {
            val character = text[index++]
            result.add(when (character) {
                '\r' -> {
                    if (index < text.length && text[index] == '\n') index++
                    AsciiKey("enter")
                }
                '\n' -> AsciiKey("enter")
                '\t' -> AsciiKey("tab")
                ' ' -> AsciiKey("space")
                in 'A'..'Z' -> AsciiKey(character.lowercaseChar().toString(), true)
                in '!'..'~' -> AsciiKey(character.toString())
                else -> return null
            })
        }
        return result
    }
}
