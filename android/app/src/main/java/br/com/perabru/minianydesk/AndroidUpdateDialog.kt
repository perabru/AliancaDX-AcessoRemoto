package br.com.perabru.minianydesk

import android.app.Activity
import android.app.AlertDialog
import android.content.Intent
import android.content.res.ColorStateList
import android.net.Uri
import android.provider.Settings
import android.content.pm.PackageInstaller
import java.io.File
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

/** Manual discovery and an explicit in-app update, with Android-managed installation. */
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
    internal var mayInstall: () -> Boolean = { true }
    internal var downloadFactory: () -> AndroidApkSource = { AndroidApkDownload() }
    internal var installer: AndroidApkInstallTarget = AndroidApkInstaller(activity)
    private var downloader: AndroidApkSource? = null
    private var readyFile: File? = null
    private var phase = "idle"
    private var transferText = ""
    private var permissionPending = false
    val installing: Boolean get() = phase == "preparing" || phase == "installing"
    private val continueReady = Runnable { if (!disposed && dialog?.isShowing == true && phase == "ready") attemptInstallation() }
    private val refreshRetry = Runnable { if (!disposed && dialog?.isShowing == true) render() }

    fun show() {
        if (disposed || activity.isFinishing || activity.isDestroyed) return
        if (dialog?.isShowing == true) return
        restoreInstallation()
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
        download = kit.iconButton("Atualizar agora", UiKit.Icon.DOWNLOAD, UiKit.ButtonKind.PRIMARY) { updateNow() }
        retry = kit.button("Buscar novamente", UiKit.ButtonKind.SECONDARY) { beginCheck() }
        body.addView(download); body.addView(retry)
        body.addView(kit.label("Ao tocar em Atualizar, o aplicativo baixa e confere a nova versão aqui, sem arquivo no navegador. No Android 12 ou superior, o sistema pode concluir automaticamente; se pedir, confirme a instalação. Seus acessos salvos são preservados.", 12f, UiKit.MUTED))
        val scroll = ScrollView(activity).apply { addView(body, ViewGroup.LayoutParams(-1, -2)) }
        dialog = AlertDialog.Builder(activity).setView(scroll).setNegativeButton("Fechar", null).create().also { current ->
            current.setOnDismissListener {
                main.removeCallbacks(refreshRetry); main.removeCallbacks(continueReady)
                if (phase == "downloading" || phase == "verifying") cancelDownload()
            }
            current.show()
            current.window?.setBackgroundDrawable(kit.background(UiKit.CARD, 24, UiKit.BORDER))
            current.getButton(AlertDialog.BUTTON_NEGATIVE).setTextColor(UiKit.EMERALD)
        }
        AndroidInstallState.observer = { consumeInstallStatus() }
        render()
        if (!inFlight && phase == "idle" && error == null && now() >= nextAttempt) beginCheck()
        consumeInstallStatus()
    }

    private fun beginCheck() {
        if (disposed || inFlight || phase != "idle" || activity.isFinishing || activity.isDestroyed) return
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
        progress?.visibility = if (inFlight || phase in listOf("downloading", "verifying", "preparing", "installing")) View.VISIBLE else View.GONE
        title?.text = when {
            phase == "downloading" -> "Baixando a atualização…"
            phase == "verifying" -> "Conferindo o aplicativo…"
            phase == "ready" -> "Atualização pronta para instalar"
            phase == "permission" -> "Autorize a atualização no Android"
            phase == "preparing" -> "Preparando a instalação…"
            phase == "installing" -> "Instalando a atualização…"
            inFlight -> "Buscando atualização…"
            failed != null -> if (release != null) "Atualização interrompida" else "Não foi possível verificar"
            release?.relation == AndroidReleaseUpdate.Relation.AVAILABLE -> "Uma nova versão está disponível"
            release?.relation == AndroidReleaseUpdate.Relation.CURRENT -> "Você está atualizado"
            release?.relation == AndroidReleaseUpdate.Relation.LOCAL_NEWER -> "Seu aplicativo é mais recente"
            else -> "Buscar atualização"
        }
        message?.text = when {
            failed != null -> failed
            phase == "downloading" -> transferText
            phase == "verifying" -> "Conferindo integridade, identidade, versão e assinatura do aplicativo."
            phase == "ready" -> if (mayInstall()) "A atualização já foi conferida. Vamos instalar a nova versão." else "O download terminou. Encerre as conexões e transferências abertas para instalar automaticamente. Nenhum PC será desconectado pelo atualizador."
            phase == "permission" -> "Ative Permitir desta fonte para o AliançaDX e volte. O aplicativo continuará a instalação do mesmo arquivo já conferido."
            phase == "preparing" -> "Enviando a atualização conferida ao instalador do Android. Seus acessos salvos serão mantidos."
            phase == "installing" -> "O Android está concluindo a atualização. Se aparecer uma confirmação, toque em Atualizar ou Instalar. Depois, abra o AliançaDX novamente."
            inFlight -> "Consultando a versão pública do AliançaDX no GitHub…"
            release?.relation == AndroidReleaseUpdate.Relation.AVAILABLE -> if (release.sha256 == null) "A publicação não informou a verificação necessária para atualizar pelo aplicativo. Tente novamente mais tarde." else "Toque em Atualizar agora. O download, a conferência e a instalação serão conduzidos aqui."
            release?.relation == AndroidReleaseUpdate.Relation.CURRENT -> "Este celular já tem a versão pública mais recente."
            release?.relation == AndroidReleaseUpdate.Relation.LOCAL_NEWER -> "A versão deste celular está à frente da última versão pública. Continue usando a instalada."
            else -> "Toque abaixo para conferir a versão mais recente."
        }
        available?.visibility = if (!inFlight && release != null) View.VISIBLE else View.GONE
        available?.text = release?.let { "Versão no GitHub · ${it.version}" }.orEmpty()
        download?.visibility = if (!inFlight && phase !in listOf("downloading", "verifying", "preparing", "installing") && release?.relation == AndroidReleaseUpdate.Relation.AVAILABLE && release.sha256 != null) View.VISIBLE else View.GONE
        download?.text = when (phase) { "permission" -> "Autorizar e continuar"; "ready" -> "Instalar atualização"; else -> "Atualizar agora" }
        retry?.visibility = if (phase == "idle") View.VISIBLE else View.GONE
        dialog?.getButton(AlertDialog.BUTTON_NEGATIVE)?.text = if (phase in listOf("downloading", "verifying")) "Cancelar download" else "Fechar"
        progress?.contentDescription = title?.text
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

    private fun updateNow() {
        if (phase == "ready" || phase == "permission") { attemptInstallation(); return }
        val release = result ?: return
        if (disposed || inFlight || phase != "idle" || release.relation != AndroidReleaseUpdate.Relation.AVAILABLE || release.sha256 == null) return
        generation++
        val requestGeneration = generation
        phase = "downloading"; error = null; transferText = "Preparando o download seguro da versão ${release.version}…"
        val receiver = downloadFactory(); downloader = receiver
        render()
        task = executor.submit {
            var file: File? = null
            try {
                var lastProgress = 0L
                val downloaded = receiver.download(release, File(activity.cacheDir, "app-updates")) { received, total ->
                    val tick = SystemClock.elapsedRealtime()
                    if (received == total || tick - lastProgress >= 200) {
                        lastProgress = tick
                        main.post { if (!disposed && requestGeneration == generation) {
                            transferText = "${received * 100 / total}% · ${received / 1024 / 1024} de ${(total + 1048575) / 1048576} MB. O arquivo fica somente no armazenamento privado do aplicativo."
                            render()
                        } }
                    }
                }
                main.post { if (!disposed && requestGeneration == generation) { phase = "verifying"; render() } }
                file = downloaded
                installer.verify(downloaded, release)
                val verified = downloaded
                main.post {
                    if (disposed || requestGeneration != generation) verified.delete()
                    else { downloader = null; readyFile = verified; phase = "ready"; render(); attemptInstallation() }
                }
                file = null
            } catch (failure: Exception) {
                file?.delete()
                main.post { if (!disposed && requestGeneration == generation) {
                    downloader = null; phase = "idle"; error = failure.message?.takeIf { it.isNotBlank() } ?: "Não foi possível baixar a atualização. Confira a internet e tente novamente."
                    render()
                } }
            }
        }
    }

    private fun attemptInstallation() {
        main.removeCallbacks(continueReady)
        val file = readyFile ?: return
        val release = result ?: return
        if (disposed || activity.isFinishing || activity.isDestroyed || phase !in listOf("ready", "permission")) return
        if (!mayInstall()) {
            phase = "ready"; render()
            if (dialog?.isShowing == true) main.postDelayed(continueReady, 1000)
            return
        }
        if (!activity.packageManager.canRequestPackageInstalls()) {
            phase = "permission"; render(); permissionPending = true
            try { activity.startActivity(Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES, Uri.parse("package:" + activity.packageName))) }
            catch (_: Exception) { permissionPending = false; error = "Não foi possível abrir a permissão. Nas configurações do Android, permita instalar atualizações pelo AliançaDX e tente novamente."; phase = "ready"; render() }
            return
        }
        phase = "preparing"; error = null; render()
        generation++
        val requestGeneration = generation
        task = executor.submit {
            try {
                installer.commit(file, release)
                file.delete()
                main.post { if (!disposed && requestGeneration == generation) {
                    readyFile = null; phase = "installing"; render(); consumeInstallStatus()
                } }
            } catch (failure: Exception) {
                main.post { if (!disposed && requestGeneration == generation) {
                    phase = "idle"; error = failure.message ?: "Não foi possível preparar a instalação. Tente novamente."
                    readyFile?.delete(); readyFile = null; render()
                } }
            }
        }
    }

    fun onResume() {
        if (disposed) return
        if (permissionPending) {
            permissionPending = false
            if (activity.packageManager.canRequestPackageInstalls()) { phase = "ready"; attemptInstallation() }
            else { phase = "permission"; render() }
        }
        consumeInstallStatus()
    }

    fun restoreInstallation() {
        if (disposed || phase != "idle" || !AndroidInstallState.hasSession(activity)) return
        val recoveryError = AndroidInstallState.restore(activity, installedVersion)
        if (recoveryError != null) error = recoveryError
        else if (AndroidInstallState.hasSession(activity)) phase = "installing"
        AndroidInstallState.observer = { consumeInstallStatus() }
    }

    private fun consumeInstallStatus() {
        if (disposed || phase != "installing") return
        val status = AndroidInstallState.status(activity)
        when (status) {
            Int.MIN_VALUE, Int.MAX_VALUE -> return
            PackageInstaller.STATUS_PENDING_USER_ACTION -> {
                if (!mayInstall()) return
                val confirmation = AndroidInstallState.takeConfirmation()
                if (confirmation != null) {
                    try { activity.startActivity(confirmation) }
                    catch (_: Exception) { failInstallation("O Android não abriu a confirmação. Tente atualizar novamente.") }
                }
            }
            PackageInstaller.STATUS_SUCCESS -> {
                phase = "idle"; result = null; error = null
                title?.text = "Atualização instalada"; message?.text = "Abra o AliançaDX novamente para usar a nova versão."
                download?.visibility = View.GONE; retry?.visibility = View.GONE; progress?.visibility = View.GONE
                AndroidInstallState.clear(activity)
            }
            else -> failInstallation(AndroidInstallState.failureMessage(status))
        }
    }

    private fun failInstallation(message: String) {
        phase = "idle"; error = message; AndroidInstallState.clear(activity); render()
    }

    private fun cancelDownload() {
        generation++; downloader?.cancel(); downloader = null; task?.cancel(true)
        phase = "idle"; error = "Atualização cancelada. Você continua usando a versão instalada."
        readyFile?.delete(); readyFile = null
    }

    fun close() {
        if (disposed) return
        disposed = true; generation++; main.removeCallbacksAndMessages(null)
        AndroidInstallState.observer = null; downloader?.cancel(); downloader = null
        readyFile?.delete(); readyFile = null
        dialog?.dismiss(); dialog = null
        task?.cancel(true); source.cancel(); executor.shutdownNow()
    }
}
