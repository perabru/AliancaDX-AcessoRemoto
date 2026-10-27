package br.com.perabru.minianydesk

import android.content.Context
import android.content.res.ColorStateList
import android.graphics.Color
import android.graphics.drawable.RippleDrawable
import android.view.Gravity
import android.view.MotionEvent
import android.widget.Button

/** 48 dp gear with an accessible name; its whole gesture belongs to local chrome. */
internal class StreamSettingsButton(context: Context, release: () -> Unit, open: () -> Unit) : Button(context) {
    init {
        val kit = UiKit(context)
        text = ""; gravity = Gravity.CENTER
        contentDescription = "Imagem e resposta. Abrir ajustes da conexão"
        tooltipText = "Imagem e resposta"
        minWidth = kit.dp(48); minimumWidth = kit.dp(48)
        minHeight = kit.dp(48); minimumHeight = kit.dp(48)
        setPadding(kit.dp(12), kit.dp(12), kit.dp(12), kit.dp(12))
        setCompoundDrawablesRelativeWithIntrinsicBounds(UiIconDrawable(UiKit.Icon.SETTINGS, Color.WHITE, kit.dp(24)), null, null, null)
        compoundDrawablePadding = 0
        backgroundTintList = null
        background = RippleDrawable(ColorStateList.valueOf(Color.argb(86, 112, 255, 174)),
            kit.background(Color.argb(165, 7, 31, 22), 18, Color.argb(90, 233, 255, 244)),
            kit.background(Color.WHITE, 18, Color.TRANSPARENT))
        setOnTouchListener { _, event -> if (event.actionMasked == MotionEvent.ACTION_DOWN) release(); false }
        setOnClickListener { release(); open() }
    }
}
