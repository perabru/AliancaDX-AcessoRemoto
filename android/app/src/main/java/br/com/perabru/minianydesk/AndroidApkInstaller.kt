package br.com.perabru.minianydesk

import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.pm.PackageInfo
import android.content.pm.PackageInstaller
import android.content.pm.PackageManager
import android.os.Build
import android.os.Handler
import android.os.Looper
import java.io.File
import java.security.MessageDigest

internal interface AndroidApkInstallTarget {
    fun verify(file: File, release: AndroidReleaseUpdate.Result)
    fun commit(file: File, release: AndroidReleaseUpdate.Result): Int
}

/** Android, rather than this app, performs the final signature and installation checks. */
internal class AndroidApkInstaller(private val context: Context) : AndroidApkInstallTarget {
    @Suppress("DEPRECATION")
    override fun verify(file: File, release: AndroidReleaseUpdate.Result) {
        require(file.isFile && file.length() == release.apkSize && release.sha256 != null) { "O arquivo da atualização não está completo." }
        val hash = MessageDigest.getInstance("SHA-256")
        file.inputStream().use { input -> val buffer = ByteArray(65536); while (true) {
            val count = input.read(buffer); if (count < 0) break; hash.update(buffer, 0, count)
        } }
        require(MessageDigest.isEqual(hash.digest(), AndroidApkDownload.hexBytes(release.sha256))) { "O arquivo da atualização mudou. Baixe novamente pelo aplicativo." }
        val flags = if (Build.VERSION.SDK_INT >= 28) PackageManager.GET_SIGNING_CERTIFICATES else PackageManager.GET_SIGNATURES
        val manager = context.packageManager
        val archive = manager.getPackageArchiveInfo(file.absolutePath, flags)
            ?: throw IllegalArgumentException("O Android não reconheceu o arquivo da atualização.")
        val installed = manager.getPackageInfo(context.packageName, flags)
        require(archive.packageName == context.packageName && archive.versionName == release.version &&
            versionCode(archive) > versionCode(installed)) { "O aplicativo recebido não é uma atualização deste AliançaDX." }
        val installedSigners = if (Build.VERSION.SDK_INT >= 28) installed.signingInfo?.apkContentsSigners else installed.signatures
        val candidateSigners = if (Build.VERSION.SDK_INT >= 28) {
            val info = archive.signingInfo
            if (info?.hasMultipleSigners() == true) info.apkContentsSigners else info?.signingCertificateHistory
        } else archive.signatures
        val multiple = Build.VERSION.SDK_INT >= 28 &&
            (installed.signingInfo?.hasMultipleSigners() == true || archive.signingInfo?.hasMultipleSigners() == true)
        require(!installedSigners.isNullOrEmpty() && !candidateSigners.isNullOrEmpty() &&
            (!multiple || installedSigners.size == candidateSigners.size) && installedSigners.all { signature ->
            candidateSigners.any { MessageDigest.isEqual(it.toByteArray(), signature.toByteArray()) }
        }) { "A assinatura recebida não corresponde ao aplicativo instalado. O arquivo foi descartado." }
    }

    override fun commit(file: File, release: AndroidReleaseUpdate.Result): Int {
        verify(file, release)
        check(context.packageManager.canRequestPackageInstalls()) { "Permita atualizações deste aplicativo nas configurações do Android." }
        val installer = context.packageManager.packageInstaller
        val params = PackageInstaller.SessionParams(PackageInstaller.SessionParams.MODE_FULL_INSTALL).apply {
            setAppPackageName(context.packageName)
            setSize(file.length())
            if (Build.VERSION.SDK_INT >= 31) {
                setRequireUserAction(PackageInstaller.SessionParams.USER_ACTION_NOT_REQUIRED)
                setInstallReason(PackageManager.INSTALL_REASON_USER)
            }
        }
        val id = installer.createSession(params)
        var committed = false
        try {
            installer.openSession(id).use { session ->
                session.openWrite("base.apk", 0, file.length()).use { output ->
                    file.inputStream().use { it.copyTo(output, 65536) }; session.fsync(output)
                }
                AndroidInstallState.begin(context, id, release.version)
                val intent = Intent(context, AndroidInstallReceiver::class.java).setAction(context.packageName + ".UPDATE_RESULT")
                val flags = PendingIntent.FLAG_UPDATE_CURRENT or if (Build.VERSION.SDK_INT >= 31) PendingIntent.FLAG_MUTABLE else 0
                val callback = PendingIntent.getBroadcast(context, id, intent, flags)
                session.commit(callback.intentSender)
                committed = true
            }
            return id
        } finally {
            if (!committed) { runCatching { installer.abandonSession(id) }; AndroidInstallState.clear(context) }
        }
    }

    @Suppress("DEPRECATION")
    private fun versionCode(info: PackageInfo): Long = if (Build.VERSION.SDK_INT >= 28) info.longVersionCode else info.versionCode.toLong()
}

/** Explicit non-exported receiver, bound to the one update session created by this app. */
class AndroidInstallReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action != context.packageName + ".UPDATE_RESULT") return
        val prefs = context.getSharedPreferences("aliancadx-update", Context.MODE_PRIVATE)
        val session = intent.getIntExtra(PackageInstaller.EXTRA_SESSION_ID, -1)
        if (session < 0 || session != prefs.getInt("session", -1)) return
        val status = intent.getIntExtra(PackageInstaller.EXTRA_STATUS, PackageInstaller.STATUS_FAILURE)
        val confirmation = if (status == PackageInstaller.STATUS_PENDING_USER_ACTION) {
            @Suppress("DEPRECATION")
            intent.getParcelableExtra<Intent>(Intent.EXTRA_INTENT)
        } else null
        AndroidInstallState.received(context, status, confirmation)
    }
}

internal object AndroidInstallState {
    private val main = Handler(Looper.getMainLooper())
    var observer: (() -> Unit)? = null
    var pendingConfirmation: Intent? = null
        private set
    fun begin(context: Context, session: Int, version: String) {
        pendingConfirmation = null
        context.getSharedPreferences("aliancadx-update", Context.MODE_PRIVATE).edit()
            .putInt("session", session).putString("version", version).putInt("status", Int.MIN_VALUE).commit()
    }
    fun received(context: Context, status: Int, confirmation: Intent?) {
        pendingConfirmation = confirmation
        context.getSharedPreferences("aliancadx-update", Context.MODE_PRIVATE).edit().putInt("status", status).commit()
        main.post { observer?.invoke() }
    }
    fun takeConfirmation(): Intent? = pendingConfirmation.also { pendingConfirmation = null }
    fun status(context: Context): Int = context.getSharedPreferences("aliancadx-update", Context.MODE_PRIVATE).getInt("status", Int.MAX_VALUE)
    fun hasSession(context: Context): Boolean = context.getSharedPreferences("aliancadx-update", Context.MODE_PRIVATE).contains("session")
    /** Restore status after Activity/process loss; an uncommitted session is never installed later. */
    fun restore(context: Context, installedVersion: String): String? {
        val prefs = context.getSharedPreferences("aliancadx-update", Context.MODE_PRIVATE)
        val id = prefs.getInt("session", -1)
        if (id < 0) return null
        if (prefs.getString("version", null) == installedVersion) { clear(context); return null }
        val state = status(context)
        val session = context.packageManager.packageInstaller.getSessionInfo(id)
        if (state == PackageInstaller.STATUS_PENDING_USER_ACTION && pendingConfirmation == null) {
            runCatching { context.packageManager.packageInstaller.abandonSession(id) }
            clear(context)
            return "A confirmação da instalação anterior foi interrompida. Toque em Atualizar agora para tentar novamente."
        }
        if (state == Int.MIN_VALUE && (session == null || !session.isSealed)) {
            runCatching { context.packageManager.packageInstaller.abandonSession(id) }
            clear(context)
            return "A preparação da atualização foi interrompida. A versão instalada foi preservada; tente novamente."
        }
        return null
    }
    fun clear(context: Context) {
        pendingConfirmation = null
        context.getSharedPreferences("aliancadx-update", Context.MODE_PRIVATE).edit().clear().commit()
    }
    fun failureMessage(status: Int): String = when (status) {
        PackageInstaller.STATUS_FAILURE_ABORTED -> "A instalação foi cancelada. Você pode tentar novamente."
        PackageInstaller.STATUS_FAILURE_STORAGE -> "O Android precisa de mais espaço livre para atualizar. Libere espaço e tente novamente."
        PackageInstaller.STATUS_FAILURE_BLOCKED -> "O Android ou o administrador bloqueou a atualização. Confira as permissões de instalação."
        PackageInstaller.STATUS_FAILURE_CONFLICT -> "O Android encontrou uma assinatura ou versão incompatível. A instalação atual foi preservada."
        PackageInstaller.STATUS_FAILURE_INCOMPATIBLE -> "Esta atualização não é compatível com este celular. A instalação atual foi preservada."
        else -> "O Android não concluiu a atualização (código $status). A instalação atual foi preservada. Tente novamente."
    }
}
