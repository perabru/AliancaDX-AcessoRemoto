package br.com.perabru.minianydesk

import android.content.Context
import android.content.res.ColorStateList
import android.graphics.Color
import android.view.Gravity
import android.view.MotionEvent
import android.view.View
import android.view.ViewGroup
import android.widget.Button
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.RadioButton
import android.widget.ScrollView
import android.widget.TextView

/** Modal native overlay: bounded, scrollable and consumes the whole gesture outside its card. */
internal class StreamSettingsPanel(context: Context, private val onSelect: (String) -> Unit,
    private val onClose: () -> Unit) : FrameLayout(context) {
    private val kit = UiKit(context)
    private val card = kit.column(12)
    private val scroller = ScrollView(context)
    private val options = LinkedHashMap<String, RadioButton>()
    private val status: TextView
    private var closing = false
    private val siblingAccessibility = LinkedHashMap<View, Int>()
    private var renderedState: List<Any?>? = null
    val closeButton: Button
    val isOpen: Boolean get() = visibility == View.VISIBLE && !closing

    init {
        visibility = View.GONE
        isClickable = true; isFocusable = true
        importantForAccessibility = View.IMPORTANT_FOR_ACCESSIBILITY_YES
        setBackgroundColor(Color.argb(115, 0, 12, 8))
        setOnClickListener { onClose() }
        card.apply {
            setPadding(kit.dp(18), kit.dp(12), kit.dp(18), kit.dp(18))
            background = kit.background(Color.argb(246, 8, 39, 28), 24, Color.argb(105, 146, 236, 182))
            isClickable = true
            addView(kit.label("Imagem e resposta", 21f, Color.WHITE, true))
        }
        closeButton = kit.button("Fechar", UiKit.ButtonKind.SECONDARY) { onClose() }.apply {
            contentDescription = "Fechar ajustes de imagem e resposta"
            setTextColor(Color.WHITE); minHeight = kit.dp(48)
            backgroundTintList = ColorStateList.valueOf(Color.argb(90, 83, 143, 105))
        }
        card.addView(closeButton)
        card.addView(kit.label("Escolha o que importa para esta conexão. Você pode mudar sem reconectar.", 13f, Color.rgb(208, 233, 219), false))
        for ((mode, description) in listOf(
            "auto" to "Equilibra nitidez e resposta conforme a conexão.",
            "latency" to "Prioriza menor atraso. A imagem pode ficar menos detalhada.",
            "quality" to "Prioriza os detalhes da imagem. Pode aumentar o atraso.")) {
            val option = RadioButton(context).apply {
                text = StreamSettings.title(mode) + "\n" + description
                textSize = 15f; setTextColor(Color.WHITE)
                buttonTintList = ColorStateList.valueOf(Color.rgb(132, 246, 172))
                minHeight = kit.dp(64); minimumHeight = kit.dp(64)
                setPadding(kit.dp(8), kit.dp(10), kit.dp(8), kit.dp(10))
                background = kit.background(Color.argb(36, 116, 207, 148), 16, Color.argb(50, 207, 255, 225))
                setOnClickListener { if (isEnabled) onSelect(mode) }
            }
            options[mode] = option; card.addView(option, LinearLayout.LayoutParams(-1, -2))
        }
        status = kit.label("Aguardando a primeira imagem do PC…", 13f, Color.rgb(184, 246, 205), false).apply {
            accessibilityLiveRegion = View.ACCESSIBILITY_LIVE_REGION_POLITE
        }
        card.addView(status)
        scroller.apply { isFillViewport = false; isClickable = true; clipToPadding = false; addView(card, ViewGroup.LayoutParams(-1, -2)) }
        addView(scroller, LayoutParams(-1, -2, Gravity.TOP or Gravity.END).apply {
            setMargins(kit.dp(12), kit.dp(12), kit.dp(12), kit.dp(12))
        })
    }

    override fun onMeasure(widthMeasureSpec: Int, heightMeasureSpec: Int) {
        val available = View.MeasureSpec.getSize(widthMeasureSpec)
        val params = scroller.layoutParams as LayoutParams
        val desiredWidth = minOf(kit.dp(370), (available - kit.dp(24)).coerceAtLeast(0))
        if (params.width != desiredWidth) { params.width = desiredWidth; scroller.layoutParams = params }
        super.onMeasure(widthMeasureSpec, heightMeasureSpec)
    }

    fun render(settings: StreamSettings) {
        val state = listOf(settings.capable, settings.hasFrame, settings.displayedMode, settings.appliedMode, settings.pending)
        if (renderedState == state) return
        renderedState = state
        for ((mode, option) in options) {
            option.isChecked = mode == settings.displayedMode
            option.jumpDrawablesToCurrentState()
            option.isEnabled = settings.capable
            option.alpha = if (settings.capable || mode == "auto") 1f else .52f
            option.contentDescription = StreamSettings.title(mode) + if (option.isChecked) ", selecionado" else ""
        }
        status.text = when {
            !settings.hasFrame -> "Aguardando a primeira imagem do PC para conferir os ajustes disponíveis."
            !settings.capable -> "Automático · este Hospedeiro ainda não permite escolher. Atualize o programa do PC para a versão 7.3 ou mais recente."
            settings.pending -> "Solicitado: ${StreamSettings.title(settings.preferredMode)}. Aguardando a imagem confirmar a mudança…"
            else -> "Em uso: ${StreamSettings.title(settings.appliedMode ?: "auto")}. Ajuste confirmado pela imagem do PC."
        }
    }

    fun showPanel() {
        animate().cancel(); closing = false; visibility = View.VISIBLE
        val siblings = parent as? ViewGroup
        if (siblings != null && siblingAccessibility.isEmpty()) for (index in 0 until siblings.childCount) {
            val sibling = siblings.getChildAt(index)
            if (sibling !== this) {
                siblingAccessibility[sibling] = sibling.importantForAccessibility
                sibling.importantForAccessibility = View.IMPORTANT_FOR_ACCESSIBILITY_NO_HIDE_DESCENDANTS
            }
        }
        alpha = 0f; scroller.translationY = kit.dp(-8).toFloat()
        animate().alpha(1f).setDuration(140).start()
        scroller.animate().translationY(0f).setDuration(140).start()
        closeButton.requestFocus()
    }

    fun hidePanel(animated: Boolean = false) {
        animate().cancel(); scroller.animate().cancel()
        for ((sibling, importance) in siblingAccessibility) sibling.importantForAccessibility = importance
        siblingAccessibility.clear()
        if (animated && visibility == View.VISIBLE) {
            closing = true
            animate().alpha(0f).setDuration(100).withEndAction {
                visibility = View.GONE; closing = false; alpha = 1f
            }.start()
        } else { visibility = View.GONE; closing = false; alpha = 1f }
    }

    // Even a disabled option or a gap owns its gesture; it cannot reach the PC canvas.
    override fun onTouchEvent(event: MotionEvent): Boolean { super.onTouchEvent(event); return true }
}
