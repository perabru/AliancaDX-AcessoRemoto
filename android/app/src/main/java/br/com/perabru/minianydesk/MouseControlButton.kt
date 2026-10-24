package br.com.perabru.minianydesk

import android.annotation.SuppressLint
import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.LinearGradient
import android.graphics.Paint
import android.graphics.Path
import android.graphics.RectF
import android.graphics.Shader
import android.graphics.Typeface
import android.text.TextPaint
import android.text.TextUtils
import android.view.MotionEvent
import android.widget.Button
import kotlin.math.ceil

/** Translucent emulator-style mouse pad. A release performs one complete mouse action. */
@SuppressLint("ViewConstructor") // Programmatic only: its action and release callback are required.
internal class MouseControlButton(context: Context, private val rightButton: Boolean,
                                  private val releasePointer: () -> Unit) : Button(context) {
    private val density = resources.displayMetrics.density
    private val ink = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        strokeCap = Paint.Cap.ROUND
        strokeJoin = Paint.Join.ROUND
    }
    private val caption = TextPaint(Paint.ANTI_ALIAS_FLAG).apply {
        typeface = Typeface.create(Typeface.DEFAULT, Typeface.BOLD)
        textSize = 11f * resources.displayMetrics.scaledDensity
        textAlign = Paint.Align.CENTER
    }
    private val mouse = RectF()
    private val captionBounds = RectF()
    private val shape = Path()
    private var glass: Shader? = null

    init {
        text = if (rightButton) "Clique direito" else "Clique duplo"
        contentDescription = "$text na posição do cursor desta conexão"
        isAllCaps = false
        isClickable = true
        isLongClickable = false
        isFocusable = true
        filterTouchesWhenObscured = true
        background = null
        backgroundTintList = null
        stateListAnimator = null
        setPadding(0, 0, 0, 0)
        minimumWidth = dp(64f).toInt()
        minimumHeight = dp(78f).toInt()
    }

    private fun dp(value: Float) = value * density

    fun preferredWidthPx(): Int = ceil(maxOf(dp(64f), minOf(dp(100f),
        caption.measureText(if (rightButton) "Direito" else "Duplo") + dp(12f)))).toInt()

    fun preferredHeightPx(): Int = ceil(maxOf(dp(78f), dp(64f) +
        caption.fontMetrics.let { it.descent - it.ascent } + dp(5f))).toInt()

    override fun onSizeChanged(w: Int, h: Int, oldw: Int, oldh: Int) {
        super.onSizeChanged(w, h, oldw, oldh)
        glass = LinearGradient(0f, 0f, 0f, dp(61f),
            Color.argb(170, 43, 68, 59), Color.argb(155, 5, 24, 18), Shader.TileMode.CLAMP)
    }

    override fun drawableStateChanged() {
        super.drawableStateChanged()
        invalidate()
    }

    override fun onTouchEvent(event: MotionEvent): Boolean {
        // Even a disabled floating pad must never pass a finger to the PC image underneath.
        if (!isEnabled) return true
        if (event.actionMasked == MotionEvent.ACTION_DOWN) releasePointer()
        return super.onTouchEvent(event)
    }

    override fun performClick(): Boolean {
        if (!isEnabled) return false
        return super.performClick()
    }

    override fun onDraw(canvas: Canvas) {
        val cx = width / 2f
        val cy = dp(31f)
        val radius = minOf(dp(28.5f), width / 2f - dp(2f))
        val active = isEnabled && (isPressed || isFocused)
        val opacity = if (isEnabled) 255 else 120

        ink.shader = null
        ink.style = Paint.Style.FILL
        ink.color = if (active) Color.argb(72, 2, 239, 95) else Color.argb(28, 255, 255, 255)
        canvas.drawCircle(cx, cy, radius + dp(2f), ink)
        ink.shader = glass
        ink.alpha = opacity
        canvas.drawCircle(cx, cy, radius, ink)
        ink.shader = null
        ink.alpha = opacity
        ink.style = Paint.Style.STROKE
        ink.strokeWidth = dp(if (active) 1.6f else 1f)
        ink.color = if (active) Color.rgb(112, 255, 174) else Color.argb(132, 226, 255, 239)
        canvas.drawCircle(cx, cy, radius - dp(.4f), ink)

        // A real mouse silhouette makes the left double-click and right-click distinguishable.
        mouse.set(cx - dp(9f), cy - dp(15f), cx + dp(9f), cy + dp(15f))
        shape.reset()
        shape.addRoundRect(mouse, dp(8f), dp(8f), Path.Direction.CW)
        canvas.save()
        canvas.clipPath(shape)
        ink.style = Paint.Style.FILL
        ink.color = Color.rgb(112, 255, 174)
        ink.alpha = if (isEnabled) (if (active) 220 else 150) else 70
        if (rightButton) canvas.drawRect(cx + dp(.7f), mouse.top, mouse.right, cy - dp(1f), ink)
        else canvas.drawRect(mouse.left, mouse.top, cx - dp(.7f), cy - dp(1f), ink)
        canvas.restore()
        ink.style = Paint.Style.STROKE
        ink.strokeWidth = dp(1.55f)
        ink.color = Color.WHITE
        ink.alpha = opacity
        canvas.drawRoundRect(mouse, dp(8f), dp(8f), ink)
        canvas.drawLine(cx, mouse.top + dp(1f), cx, cy - dp(1f), ink)
        canvas.drawLine(mouse.left, cy - dp(1f), mouse.right, cy - dp(1f), ink)
        canvas.drawLine(cx, cy - dp(10f), cx, cy - dp(6f), ink)

        if (!rightButton) {
            ink.style = Paint.Style.FILL
            ink.color = Color.argb(if (isEnabled) 238 else 110, 5, 31, 20)
            canvas.drawCircle(cx + dp(16.5f), cy - dp(16.5f), dp(8.5f), ink)
            caption.color = Color.rgb(147, 255, 191)
            caption.alpha = opacity
            caption.textSize = dp(8.5f)
            canvas.drawText("2×", cx + dp(16.5f), cy - dp(13.5f), caption)
        }

        caption.color = Color.WHITE
        caption.alpha = opacity
        caption.textSize = 11f * resources.displayMetrics.scaledDensity
        val label = TextUtils.ellipsize(if (rightButton) "Direito" else "Duplo", caption,
            width - dp(12f), TextUtils.TruncateAt.END).toString()
        val captionWidth = minOf(width.toFloat(), caption.measureText(label) + dp(12f))
        captionBounds.set(cx - captionWidth / 2f, dp(62f), cx + captionWidth / 2f,
            dp(66f) + caption.fontMetrics.let { it.descent - it.ascent })
        ink.style = Paint.Style.FILL
        ink.color = Color.argb(if (isEnabled) 170 else 95, 5, 28, 20)
        canvas.drawRoundRect(captionBounds, dp(8f), dp(8f), ink)
        // Keep the caption below the circular pad, including larger Android font sizes.
        canvas.drawText(label, cx, dp(64f) - caption.fontMetrics.ascent, caption)
    }
}
