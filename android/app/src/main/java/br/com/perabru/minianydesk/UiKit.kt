package br.com.perabru.minianydesk

import android.content.Context
import android.content.res.ColorStateList
import android.graphics.Color
import android.graphics.Typeface
import android.graphics.drawable.Drawable
import android.graphics.drawable.GradientDrawable
import android.graphics.drawable.RippleDrawable
import android.graphics.drawable.StateListDrawable
import android.os.Build
import android.text.InputType
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.view.Window
import android.view.WindowInsetsController
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.TextView
import java.util.IdentityHashMap
import kotlin.math.roundToInt

/** Small, dependency-free native view helpers for the Android Controller. */
class UiKit(private val context: Context) {
    enum class ButtonKind { PRIMARY, SECONDARY, DANGER }
    enum class Icon { DEVICES, CONNECT, SCREEN, SHIELD, FILE, CONTROL, FULLSCREEN, CLOSE, ADD, REFRESH, COPY, LOCK, HELP, SETTINGS, DOWNLOAD }

    companion object {
        val BG: Int = Color.rgb(244, 250, 247)
        val CARD: Int = Color.WHITE
        val TEXT: Int = Color.rgb(18, 58, 42)
        val FOREST: Int = TEXT
        val MUTED: Int = Color.rgb(80, 107, 93)
        val EMERALD: Int = Color.rgb(0, 122, 80)
        val BRIGHT_GREEN: Int = Color.rgb(0, 229, 83)
        val SOFT_MINT: Int = Color.rgb(234, 248, 239)
        val DEEP_GREEN: Int = Color.rgb(0, 92, 61)
        val BORDER: Int = Color.rgb(215, 232, 222)
        val DISABLED: Int = Color.rgb(238, 243, 240)
        val DANGER: Int = Color.rgb(185, 28, 28)
        val SOFT_DANGER: Int = Color.rgb(254, 242, 242)
        val FIELD: Int = Color.rgb(248, 252, 250)
    }

    fun dp(value: Int): Int = (value * context.resources.displayMetrics.density).roundToInt()

    fun label(text: String, size: Float = 14f, color: Int = TEXT,
              bold: Boolean = false): TextView = TextView(context).apply {
        this.text = text
        textSize = size
        setTextColor(color)
        setTypeface(Typeface.create("sans-serif", if (bold) Typeface.BOLD else Typeface.NORMAL))
        includeFontPadding = true
        setLineSpacing(0f, 1.1f)
        ellipsize = null
        if (Build.VERSION.SDK_INT >= 28 && bold && size >= 18f) isAccessibilityHeading = true
        layoutParams = LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT)
    }

    fun column(spacing: Int = 10): LinearLayout = stack(LinearLayout.VERTICAL, spacing)

    fun row(spacing: Int = 10): LinearLayout = stack(LinearLayout.HORIZONTAL, spacing).apply {
        gravity = Gravity.CENTER_VERTICAL
    }

    /** Reflows a group into full-width rows when its natural text will not fit. */
    fun responsiveRow(spacing: Int = 10, minItemWidth: Int = 132): LinearLayout =
        ResponsiveRow(context, dp(spacing.coerceAtLeast(0)), dp(minItemWidth.coerceAtLeast(48))).apply {
            layoutParams = LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT)
        }

    fun spacer(height: Int = 8): View = View(context).apply {
        layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(height.coerceAtLeast(0)))
        importantForAccessibility = View.IMPORTANT_FOR_ACCESSIBILITY_NO
    }

    private fun stack(direction: Int, spacing: Int): LinearLayout = LinearLayout(context).apply {
        orientation = direction
        layoutParams = LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT)
        // Transparent dividers reserve space without changing callers' weighted layout params.
        dividerDrawable = GradientDrawable().apply {
            setColor(Color.TRANSPARENT)
            setSize(dp(spacing.coerceAtLeast(0)), dp(spacing.coerceAtLeast(0)))
        }
        showDividers = LinearLayout.SHOW_DIVIDER_MIDDLE
    }

    fun background(fill: Int = CARD, radius: Int = 20, stroke: Int = BORDER, strokeWidth: Int = 1): Drawable =
        GradientDrawable().apply {
            setColor(fill)
            cornerRadius = dp(radius).toFloat()
            setStroke(dp(strokeWidth).coerceAtLeast(1), stroke)
        }

    fun card(padding: Int = 18): LinearLayout = column().apply {
        background = background()
        setPadding(dp(padding), dp(padding), dp(padding), dp(padding))
        clipToOutline = true
    }

    fun eyebrow(text: String): TextView = label(text.uppercase(java.util.Locale.ROOT), 11f, EMERALD, true).apply {
        letterSpacing = .08f
    }

    fun pageHeader(title: String, subtitle: String): LinearLayout = column(7).apply {
        addView(View(context).apply {
            background = background(BRIGHT_GREEN, 3, Color.TRANSPARENT)
            layoutParams = LinearLayout.LayoutParams(dp(34), dp(4))
            importantForAccessibility = View.IMPORTANT_FOR_ACCESSIBILITY_NO
        })
        addView(label(title, 26f, FOREST, true))
        if (subtitle.isNotBlank()) addView(label(subtitle, 14f, MUTED))
    }

    fun divider(): View = View(context).apply {
        setBackgroundColor(BORDER)
        layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(1))
        importantForAccessibility = View.IMPORTANT_FOR_ACCESSIBILITY_NO
    }

    fun iconButton(text: String, icon: Icon, kind: ButtonKind = ButtonKind.SECONDARY,
                   onClick: () -> Unit): Button = button(text, kind, onClick).apply {
        val color = when (kind) { ButtonKind.DANGER -> DANGER; ButtonKind.PRIMARY -> Color.WHITE; else -> FOREST }
        val drawable = UiIconDrawable(icon, color, dp(20))
        setCompoundDrawablesRelativeWithIntrinsicBounds(drawable, null, null, null)
        compoundDrawablePadding = dp(9)
    }

    fun navigationButton(text: String, icon: Icon, onClick: () -> Unit): Button =
        button(text, ButtonKind.SECONDARY, onClick).apply {
            textSize = 12f
            minHeight = dp(68); minimumHeight = dp(68)
            setPadding(dp(4), dp(8), dp(4), dp(8))
            val drawable = UiIconDrawable(icon, FOREST, dp(22))
            setCompoundDrawablesRelativeWithIntrinsicBounds(null, drawable, null, null)
            compoundDrawablePadding = dp(5)
            val shapes = StateListDrawable().apply {
                addState(intArrayOf(android.R.attr.state_focused), background(SOFT_MINT, 14, FOREST, 2))
                addState(intArrayOf(android.R.attr.state_selected), background(SOFT_MINT, 14, Color.TRANSPARENT))
                addState(intArrayOf(), background(CARD, 14, Color.TRANSPARENT))
            }
            background = RippleDrawable(ColorStateList.valueOf(Color.argb(28, 0, 122, 80)), shapes,
                background(Color.WHITE, 14, Color.WHITE))
            backgroundTintList = null
        }

    fun button(text: String, kind: ButtonKind = ButtonKind.PRIMARY,
               onClick: () -> Unit): Button {
        val fill = when (kind) {
            ButtonKind.PRIMARY -> EMERALD
            ButtonKind.SECONDARY -> CARD
            ButtonKind.DANGER -> SOFT_DANGER
        }
        val ink = when (kind) {
            ButtonKind.PRIMARY -> Color.WHITE
            ButtonKind.SECONDARY -> TEXT
            ButtonKind.DANGER -> DANGER
        }
        val border = when (kind) {
            ButtonKind.PRIMARY -> EMERALD
            ButtonKind.SECONDARY -> BORDER
            ButtonKind.DANGER -> Color.rgb(254, 202, 202)
        }
        return Button(context).apply {
            this.text = text
            isAllCaps = false
            textSize = 14f
            includeFontPadding = true
            setSingleLine(false)
            maxLines = Int.MAX_VALUE
            ellipsize = null
            setLineSpacing(0f, 1.05f)
            setTypeface(Typeface.create("sans-serif-medium", Typeface.NORMAL))
            minHeight = dp(48)
            minimumHeight = dp(48)
            minWidth = dp(48)
            minimumWidth = dp(48)
            isFocusable = true
            setPadding(dp(16), dp(14), dp(16), dp(14))
            gravity = Gravity.CENTER
            setTextColor(ColorStateList(
                arrayOf(intArrayOf(-android.R.attr.state_enabled), intArrayOf()),
                intArrayOf(MUTED, ink)))
            val shapes = StateListDrawable().apply {
                addState(intArrayOf(-android.R.attr.state_enabled),
                    background(DISABLED, 12, BORDER))
                val selectedFill = if (kind == ButtonKind.DANGER) fill else SOFT_MINT
                addState(intArrayOf(android.R.attr.state_focused, android.R.attr.state_selected),
                    background(selectedFill, 12, FOREST, 2))
                addState(intArrayOf(android.R.attr.state_focused), background(fill, 12, FOREST, 2))
                addState(intArrayOf(android.R.attr.state_pressed, android.R.attr.state_selected),
                    background(if (kind == ButtonKind.DANGER) fill else DEEP_GREEN, 12, border))
                addState(intArrayOf(android.R.attr.state_selected), background(selectedFill, 12, border))
                addState(intArrayOf(android.R.attr.state_pressed), background(
                    when (kind) {
                        ButtonKind.PRIMARY -> DEEP_GREEN
                        ButtonKind.SECONDARY -> DISABLED
                        ButtonKind.DANGER -> Color.rgb(254, 228, 226)
                    }, 12, border))
                addState(intArrayOf(), background(fill, 12, border))
            }
            background = RippleDrawable(
                ColorStateList.valueOf(Color.argb(28, 0, 122, 80)), shapes,
                background(Color.WHITE, 12, Color.WHITE))
            backgroundTintList = null
            elevation = 0f
            stateListAnimator = null
            layoutParams = LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT)
            setOnClickListener { onClick() }
        }
    }

    /** Preserve the ripple/focus/disabled drawable when highlighting navigation. */
    fun setButtonSelected(button: Button, selected: Boolean) {
        button.isSelected = selected
        button.setTextColor(if (selected) EMERALD else FOREST)
        button.compoundDrawablesRelative.forEach { it?.setTint(if (selected) EMERALD else FOREST) }
        if (Build.VERSION.SDK_INT >= 30) button.stateDescription = if (selected) "Selecionado" else null
    }

    fun edit(hint: String = "", text: String = "",
             inputType: Int = InputType.TYPE_CLASS_TEXT): EditText = EditText(context).apply {
        this.hint = hint
        this.inputType = inputType
        setSingleLine(true)
        setText(text)
        textSize = 15f
        setTextColor(TEXT)
        setHintTextColor(MUTED)
        setTypeface(Typeface.create("sans-serif", Typeface.NORMAL))
        minHeight = dp(52)
        minimumHeight = dp(52)
        minWidth = dp(48)
        minimumWidth = dp(48)
        setPadding(dp(14), dp(12), dp(14), dp(12))
        background = StateListDrawable().apply {
            addState(intArrayOf(-android.R.attr.state_enabled), background(DISABLED, 12, BORDER))
            addState(intArrayOf(android.R.attr.state_activated), background(CARD, 12, DANGER, 2))
            addState(intArrayOf(android.R.attr.state_focused), background(CARD, 12, FOREST, 2))
            addState(intArrayOf(), background(FIELD, 12, BORDER))
        }
        backgroundTintList = null
        layoutParams = LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT)
    }

    /** Native error semantics plus a visible outline; clearing restores focus styling. */
    fun setFieldError(input: EditText, message: String?) {
        val error = message?.takeIf { it.isNotBlank() }
        input.isActivated = error != null
        input.error = error
    }

    /** A visible field label remains available after its input contains text. */
    fun field(title: String, input: View): LinearLayout = column(7).apply {
        if (input.id == View.NO_ID) input.id = View.generateViewId()
        addView(label(title, 13f, TEXT, true).apply { labelFor = input.id })
        addView(input)
    }

    fun chip(text: String, color: Int = DEEP_GREEN): TextView = label(text, 12f, color, true).apply {
        background = background(Color.argb(19, Color.red(color), Color.green(color), Color.blue(color)),
            24, Color.TRANSPARENT)
        gravity = Gravity.CENTER
        setPadding(dp(12), dp(7), dp(12), dp(7))
        minHeight = dp(30)
        layoutParams = LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT)
    }

    /** Uses light-bar icon appearance while the activity applies the system-bar insets. */
    @Suppress("DEPRECATION")
    fun configureWindow(window: Window) {
        // Activity.onCreate calls this before setContentView. PhoneWindow needs a decor
        // before its insets-controller getter is safe on Android 15.
        val decor = window.decorView
        window.statusBarColor = BG
        window.navigationBarColor = BG
        if (Build.VERSION.SDK_INT >= 29) {
            window.isStatusBarContrastEnforced = false
            window.isNavigationBarContrastEnforced = false
        }
        if (Build.VERSION.SDK_INT >= 30) {
            val flags = WindowInsetsController.APPEARANCE_LIGHT_STATUS_BARS or
                WindowInsetsController.APPEARANCE_LIGHT_NAVIGATION_BARS
            window.insetsController?.setSystemBarsAppearance(flags, flags)
        } else {
            decor.systemUiVisibility = decor.systemUiVisibility or
                View.SYSTEM_UI_FLAG_LIGHT_STATUS_BAR
            if (Build.VERSION.SDK_INT >= 27) decor.systemUiVisibility =
                decor.systemUiVisibility or View.SYSTEM_UI_FLAG_LIGHT_NAVIGATION_BAR
        }
    }

    private class ResponsiveRow(context: Context, private val gap: Int,
                                private val minimumItemWidth: Int) : LinearLayout(context) {
        private class Parameters(val original: LayoutParams, var applied: LayoutParams)
        private val saved = IdentityHashMap<View, Parameters>()

        init {
            orientation = HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            isBaselineAligned = false
            dividerDrawable = GradientDrawable().apply {
                setColor(Color.TRANSPARENT)
                setSize(gap, gap)
            }
            showDividers = SHOW_DIVIDER_MIDDLE
        }

        override fun onViewRemoved(child: View) {
            saved.remove(child)
            super.onViewRemoved(child)
        }

        override fun onMeasure(widthMeasureSpec: Int, heightMeasureSpec: Int) {
            val mode = View.MeasureSpec.getMode(widthMeasureSpec)
            val available = (View.MeasureSpec.getSize(widthMeasureSpec) - paddingLeft - paddingRight).coerceAtLeast(0)
            var fixedPreferred = 0L
            var margins = 0L
            var totalWeight = 0.0
            var widthPerWeight = 0.0
            var visible = 0
            for (index in 0 until childCount) {
                val child = getChildAt(index)
                val current = child.layoutParams as LayoutParams
                val cached = saved[child]
                if (cached == null || cached.applied !== current) saved[child] = Parameters(LayoutParams(current), current)
                val original = saved[child]!!.original
                if (child.visibility == View.GONE) continue
                child.measure(View.MeasureSpec.makeMeasureSpec(0, View.MeasureSpec.UNSPECIFIED),
                    View.MeasureSpec.makeMeasureSpec(0, View.MeasureSpec.UNSPECIFIED))
                val natural = maxOf(minimumItemWidth, child.measuredWidth, original.width.coerceAtLeast(0))
                if (original.weight > 0f) totalWeight += original.weight
                if (original.width == 0 && original.weight > 0f) {
                    widthPerWeight = maxOf(widthPerWeight, natural / original.weight.toDouble())
                } else fixedPreferred += natural
                margins += original.leftMargin.toLong() + original.rightMargin
                visible++
            }
            // Equal/unequal weight slots must each fit, not only their summed text widths.
            val preferred = fixedPreferred + widthPerWeight * totalWeight + margins +
                gap.toLong() * (visible - 1).coerceAtLeast(0)
            val stacked = mode != View.MeasureSpec.UNSPECIFIED && visible > 1 && preferred > available
            val desiredOrientation = if (stacked) VERTICAL else HORIZONTAL
            if (orientation != desiredOrientation) orientation = desiredOrientation
            for (index in 0 until childCount) {
                val child = getChildAt(index)
                val cached = saved[child]!!
                val original = cached.original
                val width = if (stacked) ViewGroup.LayoutParams.MATCH_PARENT else original.width
                val height = if (stacked && original.height == 0 && original.weight > 0) ViewGroup.LayoutParams.WRAP_CONTENT else original.height
                val weight = if (stacked) 0f else original.weight
                val current = child.layoutParams as LayoutParams
                if (current.width != width || current.height != height || current.weight != weight) {
                    val adjusted = LayoutParams(original).apply {
                        this.width = width
                        this.height = height
                        this.weight = weight
                    }
                    cached.applied = adjusted
                    child.layoutParams = adjusted
                }
            }
            super.onMeasure(widthMeasureSpec, heightMeasureSpec)
        }
    }
}
