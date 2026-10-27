package br.com.perabru.minianydesk

import android.app.Activity
import android.app.AlertDialog
import android.content.ActivityNotFoundException
import android.content.Intent
import android.content.res.ColorStateList
import android.graphics.Color
import android.net.Uri
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.view.View
import android.view.ViewGroup
import android.widget.Button
import android.widget.LinearLayout
import android.widget.ProgressBar
import android.widget.ScrollView
import android.widget.TextView
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors
import java.util.concurrent.Future

/** One manual checker per Activity. It never touches a PC session or installs an APK. */
internal class AndroidUpdateDialog(
    private val activity: Activity,
    private val installedVersion: String,
    private val source: AndroidReleaseSource = AndroidReleaseFetcher(),
    private val executor: ExecutorService = Executors.newSingleThreadExecutor { task ->
        Thread(task, "AliancaDX-update-check").apply { isDaemon = true }
    },
    private val now: () -> Long = { SystemClock.elapsedRealtime() }
) {
    private val main = Handler(Looper.getMainLooper())
    private val kit = UiKit(activity)
    private var dialog: AlertDialog? = null
    private var title: TextView? = null
    private var message: TextView? = null
    private var available: TextView? = null
    private var progress: ProgressBar? = null
    private var download: Button? = null
    private var retry: Button? = null
    private var inFlight = false
    private var disposed = false
    private var generation = 0
    private var nextAttempt = 0L
    private var result: AndroidReleaseUpdate.Result? = null
    private var error: String? = null
    private var task: Future<*>? = null
    private val refreshRetry = Runnable { if (!disposed && dialog?.isShowing == true) render() }

    fun show() {
        if (disposed || activity.isFinishing || activity.isDestroyed) return
        if (dialog?.isShowing == true) return
        val body = kit.column(12).apply { setPadding(kit.dp(20), kit.dp(18), kit.dp(20), kit.dp(12)) }
        body.addView(kit.eyebrow("AliançaDX · Acesso Remoto"))
        body.addView(kit.label("Atualizações", 24f, UiKit.TEXT, true))
        body.addView(kit.chip("Instalada neste celular · $installedVersion", UiKit.EMERALD))
        progress = ProgressBar(activity).apply {
            indeterminateTintList = ColorStateList.valueOf(UiKit.EMERALD)
            contentDescription = "Buscando a atualização"
        }
        body.addView(progress, LinearLayout.LayoutParams(kit.dp(32), kit.dp(32)))
        title = kit.label("Buscando atualização…", 19f, UiKit.TEXT, true).apply {
            accessibilityLiveRegion = View.ACCESSIBILITY_LIVE_REGION_POLITE
        }
        message = kit.label("Consultando a versão pública do AliançaDX no GitHub.", 14f, UiKit.MUTED)
        available = kit.chip("", UiKit.EMERALD)
        body.addView(title); body.addView(message); body.addView(available)
        download = kit.iconButton("Baixar versão", UiKit.Icon.DOWNLOAD, UiKit.ButtonKind.PRIMARY) { openDownload() }
        retry = kit.button("Buscar novamente", UiKit.ButtonKind.SECONDARY) { beginCheck() }
        body.addView(download); body.addView(retry)
        body.addView(kit.label("A consulta é manual. O download abre no navegador e você escolhe quando instalar. Suas conexões com os PCs continuam abertas.", 12f, UiKit.MUTED))
        val scroll = ScrollView(activity).apply { addView(body, ViewGroup.LayoutParams(-1, -2)) }
        dialog = AlertDialog.Builder(activity).setView(scroll).setNegativeButton("Fechar", null).create().also { current ->
            current.setOnDismissListener { main.removeCallbacks(refreshRetry) }
            current.show()
            current.window?.setBackgroundDrawable(kit.background(UiKit.CARD, 24, UiKit.BORDER))
            current.getButton(AlertDialog.BUTTON_NEGATIVE).setTextColor(UiKit.EMERALD)
        }
        render()
        if (!inFlight && now() >= nextAttempt) beginCheck()
    }

    private fun beginCheck() {
        if (disposed || inFlight || activity.isFinishing || activity.isDestroyed) return
        if (now() < nextAttempt) { render(); return }
        generation++
        val requestGeneration = generation
        inFlight = true; result = null; error = null
        nextAttempt = now() + 15_000
        render()
        task = executor.submit {
            var fetched: AndroidReleaseUpdate.Result? = null
            var failure: AndroidUpdateFailure? = null
            try { fetched = source.fetch(installedVersion) }
            catch (caught: AndroidUpdateFailure) { failure = caught }
            catch (_: Exception) { failure = AndroidUpdateFailure("Não foi possível buscar a atualização. Confira a internet e tente novamente.") }
            main.post {
                if (!disposed && requestGeneration == generation && !activity.isFinishing && !activity.isDestroyed) {
                    inFlight = false; result = fetched; error = failure?.message
                    failure?.retryAfterMillis?.takeIf { it > 0 }?.let { nextAttempt = maxOf(nextAttempt, now() + it) }
                    if (dialog?.isShowing == true) render()
                }
            }
        }
    }

    private fun render() {
        val release = result
        val failed = error
        progress?.visibility = if (inFlight) View.VISIBLE else View.GONE
        title?.text = when {
            inFlight -> "Buscando atualização…"
            failed != null -> "Não foi possível verificar"
            release?.relation == AndroidReleaseUpdate.Relation.AVAILABLE -> "Uma nova versão está disponível"
            release?.relation == AndroidReleaseUpdate.Relation.CURRENT -> "Você está atualizado"
            release?.relation == AndroidReleaseUpdate.Relation.LOCAL_NEWER -> "Seu aplicativo é mais recente"
            else -> "Buscar atualização"
        }
        message?.text = when {
            inFlight -> "Consultando a versão pública do AliançaDX no GitHub…"
            failed != null -> failed
            release?.relation == AndroidReleaseUpdate.Relation.AVAILABLE -> "Você pode baixar agora ou continuar usando o aplicativo."
            release?.relation == AndroidReleaseUpdate.Relation.CURRENT -> "Este celular já tem a versão pública mais recente."
            release?.relation == AndroidReleaseUpdate.Relation.LOCAL_NEWER -> "A versão deste celular está à frente da última versão pública. Continue usando a instalada."
            else -> "Toque abaixo para conferir a versão mais recente."
        }
        available?.visibility = if (!inFlight && release != null) View.VISIBLE else View.GONE
        available?.text = release?.let { "Versão no GitHub · ${it.version}" }.orEmpty()
        download?.visibility = if (!inFlight && failed == null && release?.relation == AndroidReleaseUpdate.Relation.AVAILABLE) View.VISIBLE else View.GONE
        download?.text = release?.let { "Baixar versão ${it.version}" } ?: "Baixar versão"
        val wait = (nextAttempt - now()).coerceAtLeast(0)
        retry?.isEnabled = !inFlight && wait == 0L
        retry?.text = when {
            inFlight -> "Aguarde a consulta"
            wait > 0 -> "Tentar novamente em ${(wait + 999) / 1000}s"
            failed != null -> "Tentar novamente"
            else -> "Buscar novamente"
        }
        main.removeCallbacks(refreshRetry)
        if (!inFlight && wait > 0 && dialog?.isShowing == true) main.postDelayed(refreshRetry, minOf(wait, 60_000))
    }

    private fun openDownload() {
        val release = result ?: return
        if (disposed || inFlight || error != null || release.relation != AndroidReleaseUpdate.Relation.AVAILABLE) return
        try {
            activity.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(release.apkUrl)).addCategory(Intent.CATEGORY_BROWSABLE))
        } catch (_: ActivityNotFoundException) {
            error = "Não encontramos um navegador para abrir o download. Ative um navegador no celular e tente novamente."
            render()
        } catch (_: Exception) {
            error = "Não foi possível abrir o navegador. Tente novamente."
            render()
        }
    }

    fun close() {
        if (disposed) return
        disposed = true; generation++; main.removeCallbacksAndMessages(null)
        dialog?.dismiss(); dialog = null
        task?.cancel(true); source.cancel(); executor.shutdownNow()
    }
}
