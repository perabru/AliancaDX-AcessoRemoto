package br.com.perabru.minianydesk

import android.content.Context
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.widget.LinearLayout

/** Four real, accessible targets. Compact rows wrap with viewer width and Android font size. */
internal class MouseControlsPad @JvmOverloads constructor(context: Context, cancelPointer: (() -> Unit)? = null,
    onAction: (MouseControlButton.Action) -> Unit) : LinearLayout(context) {
    val buttons = MouseControlButton.Action.values().map { action ->
        MouseControlButton(context, action, cancelPointer = cancelPointer).apply { setOnClickListener { onAction(action) } }
    }
    private val kit = UiKit(context)
    private var columns = 0

    init {
        orientation = VERTICAL
        gravity = Gravity.END
        setPadding(kit.dp(2), kit.dp(2), kit.dp(2), kit.dp(2))
        isClickable = true
        isMotionEventSplittingEnabled = true
        importantForAccessibility = View.IMPORTANT_FOR_ACCESSIBILITY_NO
        clipChildren = false
        clipToPadding = false
        buttons.last().setDragActive(false)
    }

    fun setDragActive(active: Boolean) = buttons.last().setDragActive(active)

    override fun onMeasure(widthMeasureSpec: Int, heightMeasureSpec: Int) {
        val targetWidth = buttons.maxOf { it.preferredWidthPx() }
        val available = if (MeasureSpec.getMode(widthMeasureSpec) == MeasureSpec.UNSPECIFIED) Int.MAX_VALUE
            else MeasureSpec.getSize(widthMeasureSpec) - paddingLeft - paddingRight
        val gap = kit.dp(6)
        val wanted = when {
            available >= 4 * targetWidth + 3 * gap -> 4
            available >= 2 * targetWidth + gap -> 2
            else -> 1
        }
        if (wanted != columns) {
            columns = wanted
            // Measurement never changes the action listeners or input state of a target.
            buttons.forEach { (it.parent as? ViewGroup)?.removeView(it) }
            removeAllViews()
            buttons.chunked(columns).forEachIndexed { rowIndex, controls ->
                val row = kit.row(6).apply {
                    isClickable = true
                    isMotionEventSplittingEnabled = true
                    clipChildren = false
                    clipToPadding = false
                    importantForAccessibility = View.IMPORTANT_FOR_ACCESSIBILITY_NO
                }
                controls.forEach { control -> row.addView(control, LayoutParams(targetWidth, control.preferredHeightPx())) }
                addView(row, LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply {
                    if (rowIndex > 0) topMargin = gap
                })
            }
        }
        super.onMeasure(widthMeasureSpec, heightMeasureSpec)
    }
}
