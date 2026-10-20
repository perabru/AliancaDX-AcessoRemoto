package br.com.perabru.minianydesk

import android.graphics.Canvas
import android.graphics.ColorFilter
import android.graphics.Paint
import android.graphics.Path
import android.graphics.PixelFormat
import android.graphics.RectF
import android.graphics.drawable.Drawable

/** Small scalable native line icons. No bitmaps, fonts or external dependencies. */
internal class UiIconDrawable(private val icon: UiKit.Icon, color: Int, private val size: Int) : Drawable() {
    private val paint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        this.color = color; style = Paint.Style.STROKE; strokeWidth = 1.85f
        strokeCap = Paint.Cap.ROUND; strokeJoin = Paint.Join.ROUND
    }
    override fun getIntrinsicWidth() = size
    override fun getIntrinsicHeight() = size
    override fun setAlpha(alpha: Int) { paint.alpha = alpha; invalidateSelf() }
    override fun setColorFilter(colorFilter: ColorFilter?) { paint.colorFilter = colorFilter; invalidateSelf() }
    override fun setTint(tintColor: Int) { paint.color = tintColor; invalidateSelf() }
    @Deprecated("Required by Drawable") override fun getOpacity() = PixelFormat.TRANSLUCENT
    override fun draw(canvas: Canvas) {
        canvas.save(); canvas.translate(bounds.left.toFloat(), bounds.top.toFloat())
        canvas.scale(bounds.width() / 24f, bounds.height() / 24f)
        fun line(a: Float, b: Float, c: Float, d: Float) = canvas.drawLine(a, b, c, d, paint)
        fun rect(a: Float, b: Float, c: Float, d: Float) = canvas.drawRoundRect(RectF(a,b,c,d), 1.5f, 1.5f, paint)
        when (icon) {
            UiKit.Icon.DEVICES -> { rect(2f,4f,16f,15f); line(9f,15f,9f,19f); line(5f,19f,13f,19f); rect(17f,9f,22f,20f) }
            UiKit.Icon.SCREEN -> { rect(2f,4f,22f,17f); line(12f,17f,12f,21f); line(8f,21f,16f,21f) }
            UiKit.Icon.CONNECT -> { line(4f,8f,18f,8f); line(14f,4f,18f,8f); line(18f,8f,14f,12f); line(20f,16f,6f,16f); line(10f,12f,6f,16f); line(6f,16f,10f,20f) }
            UiKit.Icon.SHIELD -> {
                canvas.drawPath(Path().apply { moveTo(12f,2f); lineTo(21f,6f); lineTo(20f,14f); quadTo(18f,20f,12f,22f); quadTo(6f,20f,4f,14f); lineTo(3f,6f); close() }, paint)
                line(8f,12f,11f,15f); line(11f,15f,17f,9f)
            }
            UiKit.Icon.FILE -> { canvas.drawPath(Path().apply { moveTo(5f,2f); lineTo(14f,2f); lineTo(20f,8f); lineTo(20f,22f); lineTo(5f,22f); close(); moveTo(14f,2f); lineTo(14f,8f); lineTo(20f,8f) },paint); line(8f,13f,16f,13f); line(8f,17f,14f,17f) }
            UiKit.Icon.CONTROL -> { line(5f,3f,5f,21f); line(12f,3f,12f,21f); line(19f,3f,19f,21f); rect(3f,7f,7f,11f); rect(10f,14f,14f,18f); rect(17f,5f,21f,9f) }
            UiKit.Icon.FULLSCREEN -> { line(3f,9f,3f,3f); line(3f,3f,9f,3f); line(15f,3f,21f,3f); line(21f,3f,21f,9f); line(21f,15f,21f,21f); line(21f,21f,15f,21f); line(9f,21f,3f,21f); line(3f,21f,3f,15f) }
            UiKit.Icon.CLOSE -> { line(6f,6f,18f,18f); line(18f,6f,6f,18f) }
            UiKit.Icon.ADD -> { line(12f,4f,12f,20f); line(4f,12f,20f,12f) }
            UiKit.Icon.REFRESH -> { canvas.drawArc(RectF(4f,4f,20f,20f),45f,285f,false,paint); line(20f,3f,20f,9f); line(20f,9f,14f,9f) }
            UiKit.Icon.COPY -> { rect(8f,7f,21f,22f); canvas.drawPath(Path().apply { moveTo(4f,17f); lineTo(3f,17f); lineTo(3f,2f); lineTo(16f,2f); lineTo(16f,3f) },paint) }
            UiKit.Icon.LOCK -> { rect(4f,10f,20f,22f); canvas.drawArc(RectF(7f,2f,17f,15f),180f,180f,false,paint); line(12f,15f,12f,18f) }
            UiKit.Icon.HELP -> { canvas.drawCircle(12f,12f,10f,paint); canvas.drawPath(Path().apply { moveTo(8f,8f); quadTo(9f,4f,13f,6f); quadTo(18f,9f,12f,12f); lineTo(12f,14f) },paint); canvas.drawPoint(12f,18f,paint) }
        }
        canvas.restore()
    }
}
