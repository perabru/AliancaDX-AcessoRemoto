package br.com.perabru.minianydesk

import android.app.Activity
import android.app.AlertDialog
import android.content.Intent
import android.content.res.Configuration
import android.os.Build
import android.os.Bundle
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.view.WindowInsets
import android.view.WindowManager
import android.widget.*
import org.json.JSONObject
import java.net.IDN
import java.text.Normalizer
import java.util.Locale

/** One Activity, four independent session owners. Only the selected PC receives input. */
class MainActivity : Activity() {
    private val workspace = SessionWorkspace<ControllerSession>(4)
    private val kit by lazy { UiKit(this) }
    private lateinit var tabs: LinearLayout
    private lateinit var tabRow: LinearLayout
    private var tabsDivider: View? = null
    private lateinit var sessionHost: FrameLayout
    private var foreground = false
    private var renderingTabs = false
    private val documentRequests = SessionDocumentRequests<ControllerSession>()
    private val updateDialogOwner = lazy { AndroidUpdateDialog(this, JsonWire.APP_VERSION).apply {
        mayInstall = { foreground && workspace.entries.none { it.second.busy } }
    } }
    internal val updatingApplication: Boolean get() = updateDialogOwner.isInitialized() && updateDialogOwner.value.installing
    private val updateDialog get() = updateDialogOwner.value

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        if (AndroidInstallState.hasSession(this)) updateDialog.restoreInstallation()
        window.setSoftInputMode(WindowManager.LayoutParams.SOFT_INPUT_ADJUST_RESIZE)
        kit.configureWindow(window)
        if (Build.VERSION.SDK_INT >= 30) window.setDecorFitsSystemWindows(false)
        val root = kit.column(0).apply { setBackgroundColor(UiKit.BG) }
        setContentView(root)
        root.setOnApplyWindowInsetsListener { view, insets ->
            if (Build.VERSION.SDK_INT >= 30) {
                val safe = insets.getInsets(WindowInsets.Type.systemBars() or WindowInsets.Type.displayCutout())
                val ime = insets.getInsets(WindowInsets.Type.ime()).bottom
                view.setPadding(safe.left, safe.top, safe.right, maxOf(safe.bottom, ime))
            } else view.setPadding(insets.systemWindowInsetLeft, insets.systemWindowInsetTop,
                insets.systemWindowInsetRight, insets.systemWindowInsetBottom)
            insets
        }
        tabRow = kit.row(4).apply {
            gravity = Gravity.CENTER_VERTICAL
            setPadding(dp(8), dp(4), dp(8), dp(4)); setBackgroundColor(UiKit.CARD)
        }
        tabs = kit.row(4)
        val scroll = HorizontalScrollView(this).apply {
            isHorizontalScrollBarEnabled = false
            addView(tabs, ViewGroup.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT))
        }
        tabRow.addView(scroll, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f))
        tabRow.addView(kit.button("+", UiKit.ButtonKind.SECONDARY) { newSession() }.apply {
            contentDescription = "Nova conexão, até quatro PCs ao mesmo tempo"
            setPadding(dp(10), dp(8), dp(10), dp(8)); minimumWidth = dp(48)
        }, LinearLayout.LayoutParams(dp(48), ViewGroup.LayoutParams.WRAP_CONTENT))
        tabRow.addView(kit.button("×", UiKit.ButtonKind.SECONDARY) { workspace.selectedId?.let { askClose(it) } }.apply {
            contentDescription = "Fechar somente a conexão selecionada"
            setPadding(dp(10), dp(8), dp(10), dp(8)); minimumWidth = dp(48)
        }, LinearLayout.LayoutParams(dp(48), ViewGroup.LayoutParams.WRAP_CONTENT))
        tabsDivider = kit.divider()
        root.addView(tabRow); root.addView(tabsDivider)
        sessionHost = FrameLayout(this)
        root.addView(sessionHost, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f))
        val restored = savedInstanceState?.getBundle("selected_session_state")
        newSession(restored)
    }

    private fun dp(value: Int) = (value * resources.displayMetrics.density + .5f).toInt()

    private fun newSession(saved: Bundle? = null) {
        if (workspace.size >= workspace.capacity) {
            AlertDialog.Builder(this).setTitle("Quatro conexões abertas")
                .setMessage("Você pode manter até quatro PCs conectados. Feche uma conexão para abrir outra.")
                .setPositiveButton("Entendi", null).show()
            return
        }
        val previousId = workspace.selectedId
        var created: Pair<Long, ControllerSession>? = null
        try {
            val entry = workspace.add { ControllerSession(this, it) } ?: return
            created = entry
            val (id, owner) = entry
            owner.create(saved)
            owner.view.visibility = View.GONE
            sessionHost.addView(owner.view, FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT))
            if (foreground) owner.onResume()
            selectSession(id)
        } catch (error: Exception) {
            created?.let { (id, owner) ->
                workspace.remove(id)
                runCatching { owner.destroy() }
                runCatching { sessionHost.removeView(owner.view) }
            }
            previousId?.let { selectSession(it) }
            AlertDialog.Builder(this).setTitle("Não foi possível abrir a conexão")
                .setMessage("Tente abrir novamente. As outras conexões continuam abertas.")
                .setPositiveButton("OK", null).show()
        }
        drawTabs()
    }

    internal fun selectSession(id: Long) {
        val owner = workspace[id] ?: return
        val previous = workspace.selected
        if (previous !== owner) {
            previous?.setSelected(false)
            previous?.view?.visibility = View.GONE
            workspace.select(id)
            owner.view.visibility = View.VISIBLE
            owner.setSelected(true)
        }
        drawTabs()
    }

    /** A saved card reuses its live owner or opens another slot; it never replaces a PC. */
    internal fun openSavedComputer(record: SavedComputer) {
        val current = try {
            ComputerBook(applicationContext).entries().firstOrNull { it.id == record.id }
                ?: throw IllegalArgumentException("O computador não está mais na lista. Atualize os salvos e tente novamente.")
        } catch (error: Exception) {
            AlertDialog.Builder(this).setTitle("Não foi possível abrir o acesso salvo")
                .setMessage(error.message ?: "O cadastro não pôde ser lido. Os dados foram preservados.")
                .setPositiveButton("Entendi", null).show()
            return
        }
        workspace.entries.firstOrNull { it.second.matchesSavedComputer(current) }?.let { (id, owner) ->
            selectSession(id); owner.openCurrentSession(); return
        }
        var owner = workspace.selected
        if (owner?.canPrepareSavedComputer() != true) {
            val previous = owner
            newSession()
            owner = workspace.selected
            if (owner === previous) return
        }
        owner?.prepareSavedComputer(current)
    }

    internal fun openNewConnection() {
        var owner = workspace.selected
        if (owner?.canPrepareSavedComputer() != true) {
            val previous = owner
            newSession()
            owner = workspace.selected
            if (owner === previous) return
        }
        owner?.openNewConnectionDraft()
    }

    internal fun sessionChanged(owner: ControllerSession) {
        if (!workspace.owns(owner.sessionId, owner) || !::tabs.isInitialized) return
        drawTabs()
    }

    private fun drawTabs() {
        if (!::tabs.isInitialized || renderingTabs) return
        renderingTabs = true
        try {
            tabs.removeAllViews()
            for ((id, owner) in workspace.entries) {
                val title = owner.sessionTitle.let { if (it.length > 20) it.take(19) + "…" else it }
                tabs.addView(kit.button("$title\n${owner.sessionSummary}", UiKit.ButtonKind.SECONDARY) { selectSession(id) }.apply {
                    contentDescription = "${owner.sessionTitle}, ${owner.sessionSummary}" +
                        if (workspace.selectedId == id) ", conexão selecionada" else ", toque para abrir"
                    kit.setButtonSelected(this, workspace.selectedId == id)
                    setOnLongClickListener { askClose(id); true }
                    setPadding(dp(12), dp(6), dp(12), dp(6)); textSize = 12f
                }, LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT))
            }
        } finally { renderingTabs = false }
    }

    internal fun setSessionChrome(hidden: Boolean) {
        if (::tabRow.isInitialized) tabRow.visibility = if (hidden) View.GONE else View.VISIBLE
        tabsDivider?.visibility = if (hidden) View.GONE else View.VISIBLE
    }

    internal fun showUpdates() {
        workspace.selected?.prepareForUpdateCheck()
        updateDialog.show()
    }

    internal fun showSessions() {
        val entries = workspace.entries
        val labels = entries.map { (_, owner) -> "${owner.sessionTitle} · ${owner.sessionSummary}" } + "＋ Nova conexão"
        AlertDialog.Builder(this).setTitle("Conexões abertas · ${workspace.size}/4")
            .setItems(labels.toTypedArray()) { _, index ->
                if (index < entries.size) selectSession(entries[index].first) else newSession()
            }.setNegativeButton("Voltar", null)
            .setPositiveButton("Buscar atualização") { _, _ -> showUpdates() }
            .setNeutralButton("Fechar esta conexão") { _, _ -> workspace.selectedId?.let { askClose(it) } }.show()
    }

    private fun askClose(id: Long) {
        val owner = workspace[id] ?: return
        if (!owner.busy) { closeSession(id); return }
        AlertDialog.Builder(this).setTitle("Fechar ${owner.sessionTitle}?")
            .setMessage("Somente esta conexão e suas transferências serão encerradas. Os outros PCs continuarão conectados.")
            .setNegativeButton("Continuar", null).setPositiveButton("Fechar conexão") { _, _ -> closeSession(id) }.show()
    }

    private fun closeSession(id: Long) {
        val owner = workspace[id] ?: return
        owner.setSelected(false)
        val selected = workspace.selectedId == id
        workspace.remove(id)
        documentRequests.cancel(owner)
        owner.destroy()
        sessionHost.removeView(owner.view)
        if (workspace.size == 0) newSession()
        else if (selected) selectSession(workspace.entries.first().first)
        drawTabs()
    }

    internal fun launchDocument(owner: ControllerSession, intent: Intent, originalCode: Int) {
        check(workspace.owns(owner.sessionId, owner)) { "Esta conexão foi encerrada." }
        val code = documentRequests.reserve(owner, originalCode)
        selectSession(owner.sessionId)
        try { startActivityForResult(intent, code) }
        catch (error: Exception) { documentRequests.complete(code); throw error }
    }

    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        super.onActivityResult(requestCode, resultCode, data)
        val pending = documentRequests.complete(requestCode) ?: return
        if (workspace.owns(pending.owner.sessionId, pending.owner))
            pending.owner.onActivityResult(pending.originalCode, resultCode, data)
    }

    override fun onResume() {
        super.onResume(); foreground = true
        workspace.entries.forEach { (_, owner) -> owner.onResume() }
        if (updateDialogOwner.isInitialized()) updateDialog.onResume()
    }
    override fun onPause() {
        foreground = false
        workspace.entries.forEach { (_, owner) -> owner.onPause() }
        super.onPause()
    }
    override fun onConfigurationChanged(newConfig: Configuration) {
        super.onConfigurationChanged(newConfig)
        workspace.entries.forEach { (_, owner) -> owner.onConfigurationChanged(newConfig) }
    }
    override fun onWindowFocusChanged(hasFocus: Boolean) {
        super.onWindowFocusChanged(hasFocus)
        workspace.entries.forEach { (_, owner) -> owner.onWindowFocusChanged(hasFocus) }
    }
    override fun onSaveInstanceState(outState: Bundle) {
        workspace.selected?.let { owner -> Bundle().also { owner.saveState(it); outState.putBundle("selected_session_state", it) } }
        super.onSaveInstanceState(outState)
    }
    @Deprecated("Uses the native back callback on supported Android versions")
    override fun onBackPressed() {
        if (workspace.selected?.handleBack() == true) return
        if (workspace.entries.any { it.second.busy }) {
            AlertDialog.Builder(this).setTitle("Sair do AliançaDX?")
                .setMessage("As conexões com os PCs e as transferências abertas serão encerradas.")
                .setNegativeButton("Continuar", null).setPositiveButton("Encerrar todas e sair") { _, _ -> finish() }.show()
        } else super.onBackPressed()
    }
    override fun onDestroy() {
        if (updateDialogOwner.isInitialized()) updateDialog.close()
        workspace.entries.forEach { (_, owner) -> owner.destroy() }
        documentRequests.clear()
        super.onDestroy()
    }
}

/** Pure rules used by the visible native UI; no network or Android calls. */
internal object MainUiRules {
    fun inputIsRelease(message: JSONObject): Boolean {
        if (message.optString("type") != "input") return false
        val action = message.optString("action")
        return action == "release" || ((action == "key" || action == "button") && message.opt("down") == false)
    }

    fun frameExpired(now: Long, lastDisplayed: Long, waitingSince: Long, timeout: Long = 6000L): Boolean {
        val since = if (lastDisplayed > 0L) lastDisplayed else waitingSince
        return since > 0L && now >= since && now - since >= timeout
    }
    private val marks = Regex("\\p{M}+")
    private fun searchable(value: String) = Normalizer.normalize(value, Normalizer.Form.NFD)
        .replace(marks, "").lowercase(Locale.ROOT)

    fun matchesComputerQuery(computer: SavedComputer, query: String): Boolean {
        val wanted = searchable(query.trim())
        if (wanted.isEmpty()) return true
        val haystack = "${computer.name} ${computer.address} ${IDN.toUnicode(computer.address)} ${computer.port}"
        return searchable(haystack).contains(wanted)
    }

    fun shouldOpenTransferPanel(wasActive: Boolean, isActive: Boolean, controller: Boolean,
        selectedPage: String, fullscreen: Boolean): Boolean =
        !wasActive && isActive && controller && selectedPage == "session" && !fullscreen
}
