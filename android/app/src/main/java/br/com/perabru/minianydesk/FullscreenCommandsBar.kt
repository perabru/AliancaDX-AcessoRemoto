package br.com.perabru.minianydesk

import android.content.Context
import android.content.res.ColorStateList
import android.graphics.Color
import android.graphics.drawable.RippleDrawable
import android.view.View
import android.view.ViewGroup
import android.widget.Button
import android.widget.LinearLayout
import kotlin.math.ceil

/** Compact two-column chrome stays inside narrow viewers with enlarged Android fonts. */
internal class FullscreenCommandsBar(context: Context, sessions: () -> Unit, tools: () -> Unit,
    hide: () -> Unit, exit: () -> Unit) : LinearLayout(context) {
    private val kit = UiKit(context)
    private fun dp(value: Int) = kit.dp(value)
    val exitButton: Button

    init {
        orientation = VERTICAL
        setPadding(dp(2), dp(2), dp(2), dp(2))
        isClickable = true
        importantForAccessibility = View.IMPORTANT_FOR_ACCESSIBILITY_NO
        val buttons = listOf(
            action("Sessões", UiKit.Icon.DEVICES, sessions),
            action("Controles", UiKit.Icon.CONTROL, tools),
            action("Ocultar\ncomandos", null, hide).apply {
                contentDescription = "Ocultar comandos. Use a alça lateral para mostrar novamente."
            },
            action("Voltar", UiKit.Icon.CLOSE, exit).apply { contentDescription = "Sair da tela cheia" })
        exitButton = buttons.last()
        val widths = (0..1).map { column ->
            maxOf(dp(64), buttons.filterIndexed { index, _ -> index % 2 == column }.maxOf { button ->
                ceil(button.text.toString().lines().maxOf { button.paint.measureText(it) }).toInt() + dp(12)
            })
        }
        for (index in 0..1) {
            val row = kit.row(6)
            for (column in 0..1) row.addView(buttons[index * 2 + column],
                LayoutParams(widths[column], ViewGroup.LayoutParams.WRAP_CONTENT))
            addView(row, LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply {
                if (index > 0) topMargin = dp(6)
            })
        }
    }

    private fun action(title: String, icon: UiKit.Icon?, onClick: () -> Unit): Button =
        kit.button(title, UiKit.ButtonKind.SECONDARY, onClick).apply {
            textSize = 11f; isAllCaps = false
            setSingleLine(icon != null); maxLines = if (icon == null) 2 else 1
            minHeight = dp(56); minimumHeight = dp(56)
            minWidth = dp(48); minimumWidth = dp(48)
            setPadding(dp(6), dp(5), dp(6), dp(5))
            setTextColor(Color.WHITE)
            if (icon != null) setCompoundDrawablesRelativeWithIntrinsicBounds(null,
                UiIconDrawable(icon, Color.WHITE, dp(18)), null, null)
            compoundDrawablePadding = dp(3)
            backgroundTintList = null
            background = RippleDrawable(ColorStateList.valueOf(Color.argb(86, 112, 255, 174)),
                kit.background(Color.argb(145, 7, 31, 22), 17, Color.argb(80, 233, 255, 244)),
                kit.background(Color.WHITE, 17, Color.TRANSPARENT))
            contentDescription = title.replace('\n', ' ')
        }
}
