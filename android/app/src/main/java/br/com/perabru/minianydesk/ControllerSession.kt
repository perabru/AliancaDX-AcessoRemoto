package br.com.perabru.minianydesk

import android.app.Activity
import android.content.ContextWrapper
import android.app.AlertDialog
import android.content.ClipData
import android.content.ClipboardManager
import android.content.Intent
import android.content.pm.ActivityInfo
import android.content.res.ColorStateList
import android.content.res.Configuration
import android.graphics.Color
import android.graphics.Rect
import android.util.DisplayMetrics
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.text.InputType
import android.text.method.PasswordTransformationMethod
import android.text.method.HideReturnsTransformationMethod
import android.view.Gravity
import android.view.View
import android.view.WindowInsets
import android.view.WindowManager
import android.view.ViewGroup
import android.view.inputmethod.InputMethodManager
import android.view.inputmethod.EditorInfo
import android.widget.*
import org.json.JSONObject
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicReference
import java.util.concurrent.atomic.AtomicInteger
import java.util.concurrent.atomic.AtomicLong
import java.util.concurrent.ArrayBlockingQueue
import java.util.concurrent.ThreadPoolExecutor
import java.util.concurrent.ThreadFactory
import java.text.DateFormat
import java.util.Date
import java.util.Locale
import java.text.Normalizer
import java.net.IDN
import java.io.IOException

/** AliançaDX Android Controller; preserves the compatible Desktop wire protocol. */
internal class ControllerSession(private val activity: MainActivity, val sessionId: Long) : ContextWrapper(activity) {
    private var selected = false
    val view: View get() = root
    val busy: Boolean get() = peer?.closed == false || connecting || recovering || transferActive
    val sessionTitle: String get() = "$sessionId · " + (peer?.computerName?.takeIf { it.isNotBlank() } ?: invitationName.takeIf { it.isNotBlank() }
        ?: connectingEndpoint?.first?.takeUnless { isMeshAddress(it) } ?: "Nova conexão").take(80)
    val sessionSummary: String get() = when { connecting -> "Conectando"; recovering -> "Reconectando";
        peer?.closed == false -> if (transferActive) "Conectado · transferindo arquivo" else "Conectado"; else -> "Pronto para conectar" }
    private val window get() = activity.window
    private val windowManager get() = activity.windowManager
    private val currentFocus get() = activity.currentFocus
    private val isFinishing get() = activity.isFinishing
    private val isDestroyed get() = activity.isDestroyed
    private var requestedOrientation: Int
        get() = activity.requestedOrientation
        set(value) { if (selected) activity.requestedOrientation = value }
    private fun hasWindowFocus() = selected && activity.hasWindowFocus()
    private fun startActivityForResult(intent: Intent, request: Int) = activity.launchDocument(this, intent, request)

    private val main = Handler(Looper.getMainLooper())
    private val prefs by lazy { getSharedPreferences("native_connection", MODE_PRIVATE) }
    private val kit by lazy { UiKit(this) }
    private val bg = UiKit.BG
    private val panel = UiKit.CARD
    private val ink = UiKit.TEXT
    private val muted = UiKit.MUTED
    private val accent = UiKit.EMERALD
    @Volatile private var peer: Peer? = null
    @Volatile private var transfers: FileTransfers? = null
    @Volatile private var transferBinding: Pair<Int, FileTransfers>? = null
    @Volatile private var sessionGeneration = 0
    @Volatile private var alive = true
    @Volatile private var resumed = false
    @Volatile private var controllerPrompts = 0
    private var connecting = false
    private lateinit var privateCode: EditText
    private lateinit var codePanel: LinearLayout
    private lateinit var localPanel: LinearLayout
    private lateinit var localModeToggle: Switch
    private lateinit var rememberCode: CheckBox
    private var localMode = false
    private var invitationName = ""
    private var activePairing: MeshPairing? = null
    @Volatile private var meshTransport: MeshTransport? = null
    private val codeVault by lazy { MeshCodeVault(applicationContext) }
    private var connectingStatus = ""
    private var fullScreenActions: LinearLayout? = null
    private var zoomStatus: TextView? = null
    private var restoringInvitation = false
    private lateinit var reconnectToggle: Switch
    private class ConnectionAttempt(val pairing: MeshPairing?, val endpoint: String, val port: Int,
                                    val secret: String, val user: String, val remember: Boolean) {
        override fun toString() = "ConnectionAttempt(endpoint=$endpoint, port=$port)"
    }
    private var recoveryCredential: ConnectionAttempt? = null
    private var recoveryDeadline = 0L
    private var recoveryAttempt = 0
    private var recovering = false
    private var hadAuthorizedSession = false
    private var recoveryTask: Runnable? = null
    private var recoveryExpiryTask: Runnable? = null
    private var sharing = false
    @Volatile private var networkSuspended = true
    private var ownerPaused = false
    private var remotePrompt = false
    private var fullscreen = false
    private val commandsVisibility = SessionCommandsVisibility()
    private val trafficWindow = SessionTrafficWindow()
    private var trafficPeer: Peer? = null
    private var trafficRate = SessionTrafficRate.ZERO
    private var trafficStatus: TextView? = null
    private var commandsReveal: Button? = null
    private var navigationDivider: View? = null
    private var toolbarDivider: View? = null
    private var keyboardVisible = false
    private var transferActive = false
    private var latestFrame: JSONObject? = null
    private val streamFeedback = StreamFeedback()
    private var streamResumeTask: Runnable? = null
    private val buffered = ArrayList<JSONObject>()
    private var bufferedBytes = 0L
    private val modifiers = HashSet<String>()
    private val modifierButtons = HashMap<String, Button>()
    private var pickerCallback: ((Uri?) -> Unit)? = null
    private val consentDialogs = HashSet<AlertDialog>()
    private var pendingMonitor: String? = null
    private val promptLock = Any()
    private val pendingScreen = AtomicReference<Pair<Int, JSONObject>?>(null)
    private val screenPosted = AtomicBoolean(false)
    private val pendingMessages = AtomicInteger(0)
    private val pendingBytes = AtomicLong(0)
    private val pendingBlocks = AtomicInteger(0)
    private val mailboxOverflowGeneration = AtomicInteger(-1)
    private val pendingLog = AtomicReference<Pair<Int, JSONObject>?>(null)
    private val logPosted = AtomicBoolean(false)
    private var connectCancelled = AtomicBoolean(false)
    private lateinit var root: LinearLayout
    private lateinit var chrome: LinearLayout
    private lateinit var settingsPanel: LinearLayout
    private lateinit var status: TextView
    private lateinit var fileStatus: TextView
    private lateinit var progress: ProgressBar
    private lateinit var sendFileButton: Button
    private lateinit var receiveFileButton: Button
    private lateinit var cancelFileButton: Button
    private lateinit var connectButton: Button
    private lateinit var disconnectButton: Button
    private lateinit var address: EditText
    private lateinit var port: EditText
    private lateinit var password: EditText
    private lateinit var name: EditText
    private var screen: RemoteScreenView? = null
    private var monitorSpinner: Spinner? = null
    private var monitorOptions = listOf(Monitor("all", "Todos os monitores"))
    private var settingMonitor = false
    private var pauseButton: Button? = null
    private var resumeButton: Button? = null
    private var textEntry: EditText? = null
    private var controllerTools: LinearLayout? = null
    private lateinit var content: FrameLayout
    private lateinit var navigation: LinearLayout
    private lateinit var computersPage: ScrollView
    private lateinit var connectPage: LinearLayout
    private lateinit var sessionPage: LinearLayout
    private lateinit var computersList: LinearLayout
    private lateinit var computerNotice: TextView
    private lateinit var addComputerButton: Button
    private lateinit var refreshComputerButton: Button
    private lateinit var sessionToolbar: LinearLayout
    private lateinit var filesPanel: LinearLayout
    private lateinit var cancelConnectButton: Button
    private var toolsOverlay: LinearLayout? = null
    private var filesOverlay: LinearLayout? = null
    private var filesToggle: Button? = null
    private var fullscreenExit: Button? = null
    private val quickClickButtons = ArrayList<Button>()
    private var quickMouseBar: View? = null
    private var sessionHint: TextView? = null
    private var sessionPlaceholder: View? = null
    private var sessionPlaceholderTitle: TextView? = null
    private var sessionPlaceholderBody: TextView? = null
    private var sessionPlaceholderProgress: ProgressBar? = null
    private var sessionPlaceholderButton: Button? = null
    private var sessionStatusRow: LinearLayout? = null
    private val navigationButtons = HashMap<String, Button>()
    private var selectedPage = "connect"
    private var book: ComputerBook? = null
    private var bookError: String? = null
    private val savedEntries = LinkedHashMap<String, SavedComputer>()
    private val presenceState = PresenceListModel()
    private data class Availability(val result: PresenceResult, val checkedAt: Long, val endpoint: String, val pin: String?)
    private val availability = HashMap<String, Availability>()
    private val seenSavedAt = HashMap<String, Long>()
    private var presenceGeneration = 0
    private var activeEndpoint: String? = null
    private var connectingEndpoint: Pair<String, Int>? = null
    private val presenceThreadIds = AtomicInteger(0)
    private val presenceWorkers = ThreadPoolExecutor(4, 4, 30L, TimeUnit.SECONDS,
        ArrayBlockingQueue<Runnable>(256), ThreadFactory { task ->
            Thread(task, "MiniAnyDesk-presence-${presenceThreadIds.incrementAndGet()}").apply { isDaemon = true }
        })
    private val presenceTick = object : Runnable {
        override fun run() {
            if (!alive || !resumed) return
            refreshComputers()
            main.postDelayed(this, 10_000)
        }
    }
    private var computerRenderScheduled = false
    private val renderComputersTask = Runnable {
        computerRenderScheduled = false
        if (alive && selectedPage == "computers") drawComputers()
    }
    private var passwordEndpoint: String? = null
    private var computerQuery = ""
    private var computerSearch: EditText? = null
    private var searchClearButton: Button? = null
    private var passwordVisible = false
    private var passwordToggle: Button? = null
    private var captureAvailable = true
    private var frameStale = false
    private var lastFrameDisplayed = 0L
    private var frameWaitingSince = 0L
    private var computerCount: TextView? = null
    private val commands by lazy {
        SessionCommands(
            current = { connection, generation -> alive && generation == sessionGeneration && peer === connection },
            failed = { connection, generation, reason -> ui {
                if (generation == sessionGeneration && peer === connection) disconnected(reason, generation)
            } }
        )
    }
    private val frameHealthTick = object : Runnable {
        override fun run() {
            if (!alive || !resumed) return
            if (selected && peer?.closed == false && sharing && captureAvailable &&
                !ownerPaused && !remotePrompt && controllerPrompts == 0 && !frameStale &&
                MainUiRules.frameExpired(SystemClock.elapsedRealtime(), lastFrameDisplayed, frameWaitingSince)) {
                frameStale = true; latestFrame = null; screen?.clearFrame()
                status.text = "Aguardando nova imagem · a conexão continua aberta"
                updateControls()
                requestStreamResume()
            }
            main.postDelayed(this, 1000)
        }
    }
    private val expandedSections = HashSet<String>()
    private val trafficTick = object : Runnable {
        override fun run() {
            if (!alive || !resumed) return
            refreshTrafficRate()
            main.postDelayed(this, 1_000)
        }
    }
    private val disclosureViews = HashMap<String, Pair<Button, LinearLayout>>()

    private data class Monitor(val id: String, val label: String) {
        override fun toString() = label
    }

    fun create(savedInstanceState: Bundle?) {
        computerQuery = savedInstanceState?.getString("computer_search").orEmpty()
        try { book = ComputerBook(applicationContext) } catch (error: Exception) { bookError = error.message ?: "O cadastro está indisponível." }
        migratePins()
        buildUi()
    }

    private fun buildUi() {
        root = column(0).apply { setBackgroundColor(bg) }
        root.setOnApplyWindowInsetsListener { view, insets ->
            if (Build.VERSION.SDK_INT >= 30) {
                val safe = insets.getInsets(WindowInsets.Type.systemBars() or WindowInsets.Type.displayCutout())
                val ime = insets.getInsets(WindowInsets.Type.ime()).bottom
                val visible = insets.isVisible(WindowInsets.Type.ime()) && ime > 0
                if (visible != keyboardVisible) { keyboardVisible = visible; updateSessionChrome() }
                // The Activity owns safe insets for the session tabs and viewer together.
            } else {
                val visible = insets.systemWindowInsetBottom > dp(120)
                if (visible != keyboardVisible) { keyboardVisible = visible; updateSessionChrome() }
                // Safe padding is applied once by the Activity.
            }
            insets
        }
        root.viewTreeObserver.addOnGlobalLayoutListener {
            if (Build.VERSION.SDK_INT < 30) {
                val visibleFrame = Rect()
                root.getWindowVisibleDisplayFrame(visibleFrame)
                val metrics = DisplayMetrics()
                @Suppress("DEPRECATION")
                windowManager.defaultDisplay.getRealMetrics(metrics)
                val visible = metrics.heightPixels - visibleFrame.bottom > dp(120)
                if (visible != keyboardVisible) { keyboardVisible = visible; updateSessionChrome() }
            }
            if (keyboardVisible) (currentFocus as? EditText)?.let { focused ->
                focused.requestRectangleOnScreen(Rect(0, 0, focused.width, focused.height), true)
            }
        }
        chrome = column(8).apply {
            setPadding(dp(18), dp(10), dp(18), dp(10)); setBackgroundColor(panel)
        }
        val heading = column(2)
        heading.addView(ImageView(this).apply {
            setImageResource(R.drawable.aliancadx_logo); scaleType = ImageView.ScaleType.FIT_CENTER
            contentDescription = "AliançaDX"
        }, LinearLayout.LayoutParams(dp(164), dp(38)))
        heading.addView(kit.eyebrow("Acesso Remoto"))
        chrome.addView(heading)
        status = kit.chip("Desconectado", muted).apply {
            maxLines = 3; gravity = Gravity.START or Gravity.CENTER_VERTICAL
            accessibilityLiveRegion = View.ACCESSIBILITY_LIVE_REGION_POLITE
        }
        chrome.addView(status)
        root.addView(chrome)
        content = FrameLayout(this)
        root.addView(content, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f))
        address = kit.edit("192.168.1.10 ou escritorio.local", prefs.getString("address", "").orEmpty(),
            InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_URI or InputType.TYPE_TEXT_FLAG_NO_SUGGESTIONS).apply {
                imeOptions = EditorInfo.IME_ACTION_NEXT
            }
        port = kit.edit("64321", prefs.getInt("port", 64321).toString(), InputType.TYPE_CLASS_NUMBER).apply {
            imeOptions = EditorInfo.IME_ACTION_NEXT; filters = arrayOf(android.text.InputFilter.LengthFilter(5))
        }
        password = kit.edit("Senha exibida no computador", "", InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_PASSWORD).apply {
            isSaveEnabled = false; importantForAutofill = View.IMPORTANT_FOR_AUTOFILL_NO_EXCLUDE_DESCENDANTS
            imeOptions = EditorInfo.IME_ACTION_DONE or EditorInfo.IME_FLAG_NO_PERSONALIZED_LEARNING
            filters = arrayOf(android.text.InputFilter.LengthFilter(256))
        }
        passwordVisible = false; passwordToggle = null
        name = edit("Como deseja se identificar", prefs.getString("name", Build.MODEL).orEmpty())
        name.filters = arrayOf(android.text.InputFilter.LengthFilter(80)); name.imeOptions = EditorInfo.IME_ACTION_DONE
        navigation = row(0).apply { setPadding(dp(6), dp(5), dp(6), dp(5)); setBackgroundColor(panel) }
        navigationButtons.clear()
        run {
            buildController()
            for ((id, title, icon) in listOf(Triple("computers", "Salvos", UiKit.Icon.DEVICES),
                Triple("connect", "Conectar", UiKit.Icon.CONNECT), Triple("session", "Sessão", UiKit.Icon.SCREEN))) {
                val item = kit.navigationButton(title, icon) { showPage(id) }
                item.contentDescription = if (id == "computers") "Computadores salvos" else title
                navigationButtons[id] = item; navigation.addView(item, weighted())
            }
            navigationDivider = kit.divider()
            root.addView(navigationDivider); root.addView(navigation)
            showPage(if (peer != null || connecting) "session" else selectedPage)
            renderComputers()
        }
        updateControls(); root.requestApplyInsets()
        if (bookError != null) main.post { if (alive) showError("Não foi possível abrir a lista de computadores. ${bookError.orEmpty()} O cadastro foi preservado; a conexão manual continua disponível.") }
    }

    private fun scrollPage(body: LinearLayout): ScrollView = ScrollView(this).apply {
        isFillViewport = true; clipToPadding = false
        body.setPadding(body.paddingLeft, body.paddingTop, body.paddingRight, maxOf(body.paddingBottom, dp(14)))
        addView(body, FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT))
    }

    private fun buildController() {
        val savedBody = column(16).apply { setPadding(dp(18), dp(8), dp(18), dp(20)) }
        savedBody.addView(kit.pageHeader("Seus computadores", "Acesse de novo com poucos toques."))
        val savedHeader = kit.responsiveRow(8, 132)
        addComputerButton = kit.iconButton("Novo acesso", UiKit.Icon.ADD, UiKit.ButtonKind.PRIMARY) { showPage("connect") }
        refreshComputerButton = kit.iconButton("Atualizar", UiKit.Icon.REFRESH, UiKit.ButtonKind.SECONDARY) { refreshComputers(true) }
        savedHeader.addView(addComputerButton, weighted()); savedHeader.addView(refreshComputerButton, weighted()); savedBody.addView(savedHeader)
        computerSearch = kit.edit("Nome, endereço ou porta", computerQuery).apply {
            isSaveEnabled = false
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_FLAG_NO_SUGGESTIONS
            imeOptions = EditorInfo.IME_ACTION_SEARCH
            addTextChangedListener(object : android.text.TextWatcher {
                override fun beforeTextChanged(s: CharSequence?, start: Int, count: Int, after: Int) {}
                override fun onTextChanged(s: CharSequence?, start: Int, before: Int, count: Int) {}
                override fun afterTextChanged(s: android.text.Editable?) {
                    computerQuery = s?.toString().orEmpty(); searchClearButton?.isEnabled = computerQuery.isNotEmpty(); requestComputerRender()
                }
            })
        }
        val searchCard = kit.card(14)
        searchCard.addView(kit.field("Buscar dispositivo", computerSearch!!))
        val searchMeta = kit.responsiveRow(6, 100)
        computerCount = label("Nenhum dispositivo salvo", 12f, muted)
        searchMeta.addView(computerCount, weighted())
        searchClearButton = button("Limpar busca", UiKit.ButtonKind.SECONDARY) { computerSearch?.setText("") }.apply { isEnabled = computerQuery.isNotEmpty() }
        searchMeta.addView(searchClearButton, weighted()); searchCard.addView(searchMeta); savedBody.addView(searchCard)
        computerNotice = label("Os computadores são salvos após a conexão. Na internet, a disponibilidade é confirmada ao conectar; na rede local, use Atualizar.", 12f, muted)
        savedBody.addView(computerNotice)
        computersList = column(12); savedBody.addView(computersList)
        computersPage = scrollPage(savedBody); content.addView(computersPage)

        connectPage = column(0)
        val connectionBody = column(16).apply { setPadding(dp(18), dp(8), dp(18), dp(18)) }
        connectionBody.addView(kit.pageHeader("Seu computador, aqui", "Abra o AliançaDX Host no PC e copie o convite. O app prepara a conexão para você."))
        val journey = kit.card(14).apply { background = kit.background(UiKit.SOFT_MINT, 18, UiKit.BORDER) }
        journey.addView(label("1\u00A0Cole · 2\u00A0Confira · 3\u00A0Aprove\u00A0no\u00A0PC", 13f, UiKit.EMERALD, true))
        connectionBody.addView(journey)
        settingsPanel = kit.card(18)
        settingsPanel.addView(kit.eyebrow("1 · Convite do computador"))
        codePanel = column(10)
        privateCode = kit.edit("MDA6-… ou convite completo", "", InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_FLAG_NO_SUGGESTIONS).apply {
            isSaveEnabled = false
            importantForAutofill = View.IMPORTANT_FOR_AUTOFILL_NO_EXCLUDE_DESCENDANTS
            imeOptions = EditorInfo.IME_ACTION_NEXT or EditorInfo.IME_FLAG_NO_PERSONALIZED_LEARNING
            filters = arrayOf(android.text.InputFilter.LengthFilter(8192))
            setSingleLine(false); maxLines = 4
            transformationMethod = PasswordTransformationMethod.getInstance()
            addTextChangedListener(object : android.text.TextWatcher {
                override fun beforeTextChanged(s: CharSequence?, start: Int, count: Int, after: Int) {}
                override fun onTextChanged(s: CharSequence?, start: Int, before: Int, count: Int) {}
                override fun afterTextChanged(s: android.text.Editable?) {
                    if (!restoringInvitation) { password.setText(""); invitationName = "" }
                    kit.setFieldError(privateCode, null)
                }
            })
        }
        codePanel.addView(kit.field("Código privado ou convite completo", privateCode))
        codePanel.addView(kit.iconButton("Colar convite", UiKit.Icon.COPY, UiKit.ButtonKind.SECONDARY) { pasteInvitation() })
        rememberCode = CheckBox(this).apply {
            text = "Lembrar código privado neste celular"
            textSize = 13f; setTextColor(ink); buttonTintList = ColorStateList.valueOf(UiKit.EMERALD)
            minHeight = dp(48); isChecked = false; isSaveEnabled = false
        }
        codePanel.addView(rememberCode)
        codePanel.addView(label("Opcional. O código é protegido no celular; a senha nunca é salva.", 12f, muted))
        settingsPanel.addView(codePanel)
        localPanel = column(10).apply { visibility = View.GONE }
        localPanel.addView(kit.field("IP ou nome do PC", address))
        localPanel.addView(kit.field("Porta do Host", port))
        localPanel.addView(label("Use o IP do Host quando o celular e o computador estiverem na mesma rede.", 12f, muted))
        settingsPanel.addView(localPanel)
        settingsPanel.addView(kit.divider())
        settingsPanel.addView(kit.eyebrow("2 · Senha do Host"))
        settingsPanel.addView(passwordField("Senha de acesso"))
        settingsPanel.addView(label("Colar convite preenche código e senha quando os dois estão incluídos. Se você recebeu somente o código, informe a senha atual do Host.", 12f, muted))
        connectionBody.addView(settingsPanel)
        val options = disclosure(connectionBody, "connection-settings", "Opções avançadas", "Seu nome e conexão pela rede local")
        options.body.addView(kit.field("Seu nome para o responsável no PC", name))
        localModeToggle = Switch(this).apply {
            text = "Conectar por IP na rede local"; setTextColor(ink); textSize = 14f
            minHeight = dp(48); isChecked = false
            setOnCheckedChangeListener { _, checked ->
                if (connecting || peer != null) return@setOnCheckedChangeListener
                localMode = checked
                codePanel.visibility = if (checked) View.GONE else View.VISIBLE
                localPanel.visibility = if (checked) View.VISIBLE else View.GONE
                password.setText(""); setPasswordVisible(false)
            }
        }
        options.body.addView(localModeToggle)
        reconnectToggle = Switch(this).apply {
            text = "Reconectar após queda de rede"; setTextColor(ink); textSize = 14f
            minHeight = dp(48); isChecked = prefs.getBoolean("reconnect", true)
            setOnCheckedChangeListener { _, enabled ->
                prefs.edit().putBoolean("reconnect", enabled).apply()
                if (!enabled) {
                    recoveryCredential = null
                    if (recovering) stopAll("Reconexão automática desativada.")
                }
            }
        }
        options.body.addView(reconnectToggle)
        options.body.addView(label("Tenta novamente por até 2 minutos após uma falha de rede. A senha fica temporariamente na memória; cada nova sessão exige aprovação no PC. Recusa, certificado alterado e encerramento pelo responsável interrompem as tentativas.", 12f, muted))
        val security = kit.card(14)
        security.addView(kit.eyebrow("3 · Aprovação no computador"))
        security.addView(label("Quem está no PC autoriza o acesso", 17f, ink, true))
        security.addView(label("O convite confere a identidade do computador antes de enviar a senha. O Host precisa estar aberto e aprovar sua solicitação. Na rede local, compare o certificado na primeira conexão.", 13f, muted))
        connectionBody.addView(security)
        val help = disclosure(connectionBody, "quick-help", "Como usar", "Três passos para começar e gestos da tela remota")
        help.body.addView(label("1. No PC, abra o AliançaDX Host e aguarde o código ficar pronto. Use Copiar convite e envie para este celular.", 14f, ink))
        help.body.addView(label("2. Aqui, toque em Colar convite e em Conectar ao PC. Não é necessário fazer login em uma VPN.", 14f, ink))
        help.body.addView(label("3. No PC, confira o nome do visitante e permita o acesso. Para terminar, toque em Encerrar.", 14f, ink))
        help.body.addView(label("Na sessão: toque e solte para clicar. Para arrastar, mantenha o dedo parado por meio segundo antes de mover. Use dois dedos para ampliar e mover a imagem. Abra Controles para teclado, atalhos e monitores, ou Arquivos para transferir.", 14f, muted))
        val about = disclosure(connectionBody, "about-privacy", "Sobre e privacidade", "Autor, permissões, dados e como encerrar o acesso")
        about.body.addView(label("AliançaDX Acesso Remoto · ${BuildConfig.VERSION_NAME}", 16f, ink, true))
        about.body.addView(label("Autor: Bruno Michel Pera. Este aplicativo permite ver e controlar um PC com autorização do responsável, além de trocar arquivos escolhidos por vocês. Não é necessário criar conta nem fazer login em uma VPN.", 14f, ink))
        about.body.addView(label("Acesso e proteção", 14f, ink, true))
        about.body.addView(label("Tela, comandos e arquivos trafegam com criptografia TLS entre o celular e o PC. O app confere a identidade do Host pelo convite e guarda a impressão digital do certificado para as próximas conexões (pinning). Por IP local, você precisa comparar o certificado na primeira conexão. A senha só é enviada após essa conferência; o responsável no PC precisa aprovar cada sessão.", 13f, muted))
        about.body.addView(label("Permissões e arquivos", 14f, ink, true))
        about.body.addView(label("O app solicita apenas acesso à internet e ao estado da rede. Para enviar ou receber um arquivo, usa o seletor do Android: você escolhe o arquivo ou o destino, sem liberar acesso geral ao armazenamento.", 13f, muted))
        about.body.addView(label("Dados neste celular", 14f, ink, true))
        about.body.addView(label("A lista de computadores, os dados de conexão e as impressões digitais dos certificados ficam no aparelho. O código privado só é guardado se você marcar Lembrar código privado neste celular; ele é criptografado com uma chave do Android Keystore. A senha não é gravada. Com a reconexão automática ativa, ela fica apenas na memória durante a sessão e as tentativas; após uma falha, o app tenta reconectar por até 2 minutos.", 13f, muted))
        about.body.addView(label("Conexão pela internet", 14f, ink, true))
        about.body.addView(label("A rede EasyTier pode usar retransmissores comunitários de terceiros. Esses serviços podem observar endereços IP e dados de conexão, mesmo com o conteúdo criptografado. A disponibilidade e os registros desses serviços dependem dos seus operadores.", 13f, muted))
        about.body.addView(label("Você pode interromper e remover", 14f, ink, true))
        about.body.addView(label("Toque em Encerrar para parar a sessão. Em Salvos, use Editar e Excluir para remover um computador e seu código privado guardado. Para apagar todos os dados locais, use as configurações do Android: Aplicativos → AliançaDX Acesso Remoto → Armazenamento → Limpar dados.", 13f, muted))
        connectPage.addView(scrollPage(connectionBody), LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f))
        connectPage.addView(kit.divider())
        val footer = column(8).apply { setPadding(dp(18), dp(12), dp(18), dp(12)); setBackgroundColor(panel) }
        connectButton = kit.iconButton("Conectar ao PC", UiKit.Icon.CONNECT, UiKit.ButtonKind.PRIMARY) { connectController() }
        cancelConnectButton = button("Cancelar conexão", UiKit.ButtonKind.DANGER) { stopAll() }.apply { visibility = View.GONE }
        footer.addView(connectButton); footer.addView(cancelConnectButton); connectPage.addView(footer)
        bindCredentialEndpoint(); content.addView(connectPage)

        sessionPage = column(0)
        sessionHint = label("Conecte para ver a tela.", 12f, ink).apply {
            setPadding(dp(12), dp(6), dp(12), dp(6)); maxLines = 3
            accessibilityLiveRegion = View.ACCESSIBILITY_LIVE_REGION_POLITE
        }
        sessionStatusRow = column(0).apply { addView(sessionHint) }; sessionPage.addView(sessionStatusRow)
        val viewer = FrameLayout(this).apply { setBackgroundColor(Color.rgb(9, 31, 22)) }
        screen = RemoteScreenView(this).apply {
            send = { message -> this@ControllerSession.send(message) }
            onFrameInfo = { metadata ->
                if (selected && !networkSuspended && sharing && captureAvailable && !ownerPaused && !remotePrompt) {
                    val restored = latestFrame == null || frameStale
                    latestFrame = metadata; lastFrameDisplayed = SystemClock.elapsedRealtime(); frameStale = false
                    updateMonitors(metadata)
                    if (restored) updateControls()
                }
            }
            onFramePresented = { metadata, decodeMs, renderMs ->
                if (selected && !networkSuspended && sharing && captureAvailable && !ownerPaused && !remotePrompt)
                    streamFeedback.presented(metadata, decodeMs, renderMs)?.let { this@ControllerSession.send(it) }
            }
            onInputReleased = { resetModifiers() }
            onFrameError = { message ->
                status.text = message; sessionHint?.text = message
                requestStreamResume()
            }
        }
        viewer.addView(screen, FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT))
        val placeholderCard = kit.card(20).apply {
            gravity = Gravity.CENTER_HORIZONTAL
            sessionPlaceholderProgress = ProgressBar(this@ControllerSession).apply {
                indeterminateTintList = ColorStateList.valueOf(UiKit.EMERALD)
            }
            addView(sessionPlaceholderProgress, LinearLayout.LayoutParams(dp(34), dp(34)).apply { gravity = Gravity.CENTER_HORIZONTAL })
            sessionPlaceholderTitle = label("Seu PC aparece aqui", 20f, ink, true).apply { gravity = Gravity.CENTER }
            sessionPlaceholderBody = label("Cole o convite do Host na aba Conectar para começar.", 14f, muted).apply { gravity = Gravity.CENTER }
            addView(sessionPlaceholderTitle); addView(sessionPlaceholderBody)
            sessionPlaceholderButton = button("Conectar ao PC") { if (connecting) stopAll("Conexão cancelada.") else showPage("connect") }
            addView(sessionPlaceholderButton)
        }
        sessionPlaceholder = ScrollView(this).apply {
            isFillViewport = true; setPadding(dp(24), dp(16), dp(24), dp(16))
            addView(column(0).apply {
                gravity = Gravity.CENTER
                addView(placeholderCard)
            }, ViewGroup.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT))
        }
        viewer.addView(sessionPlaceholder, FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT))
        buildTools(viewer)
        val fileBody = column(14)
        fileBody.addView(kit.pageHeader("Arquivos", "Envie ou receba com a autorização dos dois dispositivos."))
        buildFiles(fileBody)
        filesOverlay = column(10).apply {
            setPadding(dp(16), dp(12), dp(16), dp(12)); setBackgroundColor(bg); visibility = View.GONE; isClickable = true
            addView(kit.iconButton("Voltar à tela", UiKit.Icon.CLOSE, UiKit.ButtonKind.SECONDARY) { toggleFiles(false) })
            addView(scrollPage(fileBody), LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f))
        }
        viewer.addView(filesOverlay, FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT))
        fullScreenActions = FullscreenCommandsBar(this, { activity.showSessions() }, { toggleTools() },
            { setCommandsHidden(true) }, { setFullscreen(false) }).apply { visibility = View.GONE }
        fullscreenExit = (fullScreenActions as FullscreenCommandsBar).exitButton
        viewer.addView(fullScreenActions, FrameLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT,
            ViewGroup.LayoutParams.WRAP_CONTENT, Gravity.TOP or Gravity.END).apply {
            topMargin = dp(10); rightMargin = dp(12)
        })
        val quickMouse = row(10).apply {
            setPadding(dp(2), dp(2), dp(2), dp(2))
            // Only this compact cluster intercepts touch. The rest of the image remains usable.
            isClickable = true
            importantForAccessibility = View.IMPORTANT_FOR_ACCESSIBILITY_NO
            clipChildren = false
            clipToPadding = false
        }
        quickMouseBar = quickMouse
        for ((rightButton, action) in listOf<Pair<Boolean, () -> Unit>>(
            false to { screen?.sendDoubleClick(); Unit },
            true to { screen?.sendButtonTap("right"); Unit })) {
            val control = MouseControlButton(this, rightButton) { screen?.releaseInput() }.apply {
                setOnClickListener { action() }
            }
            quickClickButtons.add(control)
            quickMouse.addView(control, LinearLayout.LayoutParams(control.preferredWidthPx(), control.preferredHeightPx()))
        }
        viewer.addView(quickMouse, FrameLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT,
            ViewGroup.LayoutParams.WRAP_CONTENT, Gravity.BOTTOM or Gravity.END).apply {
            rightMargin = dp(14); bottomMargin = dp(10)
        })
        trafficStatus = label(trafficRate.compactLabel(), 10f, Color.WHITE).apply {
            setSingleLine(false); maxLines = 2
            setPadding(dp(9), dp(6), dp(9), dp(6))
            background = kit.background(Color.argb(145, 7, 31, 22), 18, Color.argb(80, 233, 255, 244))
            isClickable = true
            setOnTouchListener { _, event ->
                if (event.actionMasked == android.view.MotionEvent.ACTION_DOWN) screen?.releaseInput()
                false
            }
            tooltipText = "Tráfego real desta sessão nos últimos 3 segundos. Não mede a capacidade da internet."
        }
        viewer.addView(trafficStatus, FrameLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT,
            ViewGroup.LayoutParams.WRAP_CONTENT, Gravity.TOP or Gravity.START).apply {
            leftMargin = dp(10); topMargin = dp(10); bottomMargin = dp(10)
        })
        commandsReveal = button("‹", UiKit.ButtonKind.SECONDARY) { setCommandsHidden(false) }.apply {
            contentDescription = "Mostrar comandos"
            tooltipText = "Mostrar comandos"
            textSize = 25f; isAllCaps = false
            minWidth = dp(48); minimumWidth = dp(48); minHeight = dp(56); minimumHeight = dp(56)
            setPadding(0, 0, 0, dp(3)); setTextColor(Color.WHITE)
            backgroundTintList = null
            background = android.graphics.drawable.RippleDrawable(
                ColorStateList.valueOf(Color.argb(70, 255, 255, 255)),
                kit.background(Color.argb(125, 35, 40, 43), 16, Color.argb(90, 255, 255, 255)),
                kit.background(Color.WHITE, 16, Color.TRANSPARENT))
            visibility = View.GONE
            // This real, clickable sibling consumes its entire gesture. Showing
            // chrome can never synthesize a click on the remote canvas behind it.
            setOnTouchListener { _, event ->
                if (event.actionMasked == android.view.MotionEvent.ACTION_DOWN) screen?.releaseInput()
                false
            }
        }
        viewer.addView(commandsReveal, FrameLayout.LayoutParams(dp(48), dp(56), Gravity.CENTER_VERTICAL or Gravity.END).apply {
            rightMargin = dp(4)
        })
        sessionPage.addView(viewer, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f))
        sessionToolbar = row(0).apply { setPadding(dp(4), dp(4), dp(4), dp(4)); setBackgroundColor(panel) }
        sessionToolbar.addView(kit.navigationButton("Controles", UiKit.Icon.CONTROL) { toggleTools() }, weighted())
        filesToggle = kit.navigationButton("Arquivos", UiKit.Icon.FILE) {
            toggleFiles()
        }
        sessionToolbar.addView(filesToggle, weighted())
        sessionToolbar.addView(kit.navigationButton("Tela cheia", UiKit.Icon.FULLSCREEN) { setFullscreen(true) }, weighted())
        disconnectButton = kit.navigationButton("Encerrar", UiKit.Icon.CLOSE) { stopAll() }.apply { setTextColor(Color.rgb(185, 28, 28)) }
        sessionToolbar.addView(disconnectButton, weighted())
        toolbarDivider = kit.divider(); sessionPage.addView(toolbarDivider); sessionPage.addView(sessionToolbar)
        content.addView(sessionPage, FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT))
    }

    private fun buildTools(viewer: FrameLayout) {
        val overlay = column(12).apply { background = kit.background(panel, 16); setPadding(dp(14), dp(14), dp(14), dp(14)); visibility = View.GONE; isClickable = true }
        toolsOverlay = overlay
        val header = kit.responsiveRow(8, 100)
        header.addView(label("Controles da sessão", 18f, ink, true), LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f))
        header.addView(button("Fechar", UiKit.ButtonKind.SECONDARY) { toggleTools(false) }, LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT))
        overlay.addView(header)
        val tools = column(14); controllerTools = tools
        monitorSpinner = Spinner(this).apply {
            minimumHeight = dp(48)
            adapter = ArrayAdapter(this@ControllerSession, android.R.layout.simple_spinner_dropdown_item, monitorOptions)
            onItemSelectedListener = object : AdapterView.OnItemSelectedListener {
                override fun onNothingSelected(parent: AdapterView<*>?) {}
                override fun onItemSelected(parent: AdapterView<*>?, view: View?, position: Int, id: Long) {
                    if (!settingMonitor && peer != null && latestFrame != null && position < monitorOptions.size) {
                        val selected = monitorOptions[position].id
                        if (selected != latestFrame?.optString("monitorId", "all")) {
                            screen?.releaseInput(); screen?.clearFrame(); latestFrame = null; pendingMonitor = selected
                            frameStale = false; lastFrameDisplayed = 0L; frameWaitingSince = SystemClock.elapsedRealtime()
                            send(JSONObject().put("type", "monitor").put("id", selected)); updateControls()
                        }
                    }
                }
            }
        }
        tools.addView(label("Toque e solte: clicar · segure meio segundo e mova: arrastar · 2 dedos: ampliar e mover", 13f, muted))
        val zoomControls = kit.responsiveRow(6, 85)
        zoomControls.addView(button("−", UiKit.ButtonKind.SECONDARY) { screen?.zoomOut() }, weighted())
        zoomControls.addView(button("Ajustar", UiKit.ButtonKind.SECONDARY) { screen?.resetZoom() }, weighted())
        zoomControls.addView(button("+", UiKit.ButtonKind.SECONDARY) { screen?.zoomIn() }, weighted())
        tools.addView(zoomControls)
        zoomStatus = label("Zoom 100%", 12f, muted)
        screen?.onZoomChanged = { zoom -> zoomStatus?.text = "Zoom $zoom" }
        tools.addView(zoomStatus)
        tools.addView(kit.field("Monitor", monitorSpinner!!))
        val sharingRow = kit.responsiveRow(8, 120)
        pauseButton = button("Pausar tela", UiKit.ButtonKind.SECONDARY) { requestSharing(false) }
        resumeButton = button("Retomar tela", UiKit.ButtonKind.SECONDARY) { requestSharing(true) }
        sharingRow.addView(pauseButton, weighted()); sharingRow.addView(resumeButton, weighted()); tools.addView(sharingRow)
        tools.addView(kit.iconButton("Arquivos da sessão", UiKit.Icon.FILE, UiKit.ButtonKind.SECONDARY) {
            toggleFiles(true)
        })
        tools.addView(label("Mouse", 14f, ink, true))
        val pointer = kit.responsiveRow(6, 90)
        pointer.addView(button("Duplo", UiKit.ButtonKind.SECONDARY) { screen?.sendDoubleClick() }, weighted())
        pointer.addView(button("Direito", UiKit.ButtonKind.SECONDARY) { screen?.sendButtonTap("right") }, weighted())
        pointer.addView(button("Rolar ↑", UiKit.ButtonKind.SECONDARY) { screen?.sendWheel(3) }, weighted())
        pointer.addView(button("Rolar ↓", UiKit.ButtonKind.SECONDARY) { screen?.sendWheel(-3) }, weighted()); tools.addView(pointer)
        tools.addView(label("Teclado e atalhos", 14f, ink, true))
        val modifiersRow = kit.responsiveRow(6, 65)
        for ((title, key) in listOf("Ctrl" to "ctrl", "Alt" to "alt", "Shift" to "shift", "Win" to "win")) {
            val control = button(title, UiKit.ButtonKind.SECONDARY) {
                if (modifiers.remove(key)) screen?.sendKey(key, false)
                else if (screen?.sendKey(key, true) == true) modifiers.add(key)
                modifierButtons[key]?.text = if (key in modifiers) "$title ●" else title
            }.apply { setPadding(dp(4), dp(12), dp(4), dp(12)) }
            modifierButtons[key] = control; modifiersRow.addView(control, weighted())
        }
        tools.addView(modifiersRow)
        val keys = kit.responsiveRow(6, 65)
        for ((title, key) in listOf("Esc" to "esc", "Tab" to "tab", "Enter" to "enter", "Apagar" to "backspace")) {
            keys.addView(button(title, UiKit.ButtonKind.SECONDARY) { screen?.sendKey(key, true); screen?.sendKey(key, false) }.apply { setPadding(dp(4), dp(12), dp(4), dp(12)) }, weighted())
        }
        tools.addView(keys)
        textEntry = edit("Digite o texto", "").apply {
            isSaveEnabled = false; importantForAutofill = View.IMPORTANT_FOR_AUTOFILL_NO_EXCLUDE_DESCENDANTS
            imeOptions = EditorInfo.IME_ACTION_DONE or EditorInfo.IME_FLAG_NO_PERSONALIZED_LEARNING
        }
        tools.addView(kit.field("Texto para o dispositivo remoto", textEntry!!))
        tools.addView(button("Enviar texto") { sendRemoteText() })
        tools.addView(label("Para o PC, use texto básico sem acentos. Atalhos como Ctrl, Alt e Shift também estão disponíveis.", 12f, muted))
        overlay.addView(scrollPage(tools), LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f))
        viewer.addView(overlay, FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT))
    }

    private fun toggleTools(show: Boolean? = null) {
        screen?.releaseInput()
        filesOverlay?.visibility = View.GONE
        toolsOverlay?.visibility = if (show ?: (toolsOverlay?.visibility != View.VISIBLE)) View.VISIBLE else View.GONE
        updateSessionChrome()
        if (toolsOverlay?.visibility == View.GONE) {
            if (selected) getSystemService(InputMethodManager::class.java).hideSoftInputFromWindow(textEntry?.windowToken, 0)
            if (selected) screen?.requestFocus()
        }
    }

    private fun toggleFiles(show: Boolean? = null) {
        screen?.releaseInput(); toolsOverlay?.visibility = View.GONE
        filesOverlay?.visibility = if (show ?: (filesOverlay?.visibility != View.VISIBLE)) View.VISIBLE else View.GONE
        if (selected) getSystemService(InputMethodManager::class.java).hideSoftInputFromWindow(root.windowToken, 0)
        updateSessionChrome()
    }

    private fun sendRemoteText() {
        val text = textEntry?.text?.toString().orEmpty()
        if (text.isEmpty()) return
        if (text.length > 1024) { showError("Use até 1024 caracteres por envio."); return }
        if (!inputReady() || !hasWindowFocus()) return
        if (screen?.sendTextASCII(text) != true) {
            showError("Para o Windows, use texto básico sem acentos. Os atalhos também estão disponíveis.")
            return
        }
        textEntry?.setText("")
    }

    private fun showPage(id: String) {
        if (!::computersPage.isInitialized) return
        val previousPage = selectedPage
        if (fullscreen) setFullscreen(false)
        screen?.releaseInput(); resetModifiers(); toolsOverlay?.visibility = View.GONE; filesOverlay?.visibility = View.GONE
        if (selected) getSystemService(InputMethodManager::class.java).hideSoftInputFromWindow(root.windowToken, 0)
        selectedPage = if (id in listOf("computers", "connect", "session")) id else "computers"
        if (selectedPage != "connect") setPasswordVisible(false)
        updateSessionChrome()
        computersPage.visibility = if (selectedPage == "computers") View.VISIBLE else View.GONE
        connectPage.visibility = if (selectedPage == "connect") View.VISIBLE else View.GONE
        sessionPage.visibility = if (selectedPage == "session") View.VISIBLE else View.GONE
        for ((page, control) in navigationButtons) {
            kit.setButtonSelected(control, page == selectedPage)
        }
        if (selectedPage == "computers") renderComputers()
        updateControls()
        if (selectedPage == "session" && previousPage != "session") requestStreamResume()
    }

    /** A discarded frame or an explicitly resumed viewer may reopen the pacing window. */
    private fun requestStreamResume() {
        val connection = peer ?: return
        if (!alive || !resumed || !selected || selectedPage != "session" || connection.closed || !sharing ||
            !captureAvailable || networkSuspended || ownerPaused || remotePrompt || controllerPrompts > 0) return
        val now = SystemClock.uptimeMillis()
        val delay = streamFeedback.resumeDelayMs(now)
        if (delay > 0) {
            if (streamResumeTask == null) {
                val generation = sessionGeneration
                val task = Runnable {
                    streamResumeTask = null
                    if (generation == sessionGeneration && peer === connection) requestStreamResume()
                }
                streamResumeTask = task
                // One deferred retry of this event, not a periodic network polling loop.
                main.postDelayed(task, delay + 25)
            }
            return
        }
        streamResumeTask?.let { main.removeCallbacks(it) }; streamResumeTask = null
        streamFeedback.resume(now)?.let { commands.offer(connection, sessionGeneration, it) }
    }

    private fun passwordField(title: String): LinearLayout = column(8).apply {
        addView(kit.field(title, password))
        passwordToggle = kit.iconButton("Mostrar senha", UiKit.Icon.LOCK, UiKit.ButtonKind.SECONDARY) { setPasswordVisible(!passwordVisible) }
        addView(passwordToggle)
        setPasswordVisible(false)
    }

    private fun setPasswordVisible(visible: Boolean) {
        passwordVisible = visible
        if (!::password.isInitialized) return
        val cursor = password.selectionStart.coerceAtLeast(0)
        password.transformationMethod = if (visible) HideReturnsTransformationMethod.getInstance() else PasswordTransformationMethod.getInstance()
        password.setSelection(cursor.coerceAtMost(password.text.length))
        passwordToggle?.text = if (visible) "Ocultar senha" else "Mostrar senha"
        passwordToggle?.contentDescription = if (visible) "Ocultar a senha" else "Mostrar a senha"
    }

    private data class Disclosure(val body: LinearLayout, val summary: TextView)

    private fun disclosure(parent: LinearLayout, id: String, title: String, summary: String): Disclosure {
        val card = kit.card(14)
        val body = column(12).apply { visibility = if (id in expandedSections) View.VISIBLE else View.GONE }
        val caption = label(summary, 13f, muted)
        val toggle = button("$title ${if (id in expandedSections) "−" else "+"}", UiKit.ButtonKind.SECONDARY) {}
        fun updateLabel() {
            val expanded = body.visibility == View.VISIBLE
            toggle.text = "$title ${if (expanded) "−" else "+"}"
            toggle.contentDescription = "$title. ${if (expanded) "Recolher" else "Expandir"} detalhes"
            if (Build.VERSION.SDK_INT >= 30) toggle.stateDescription = if (expanded) "Expandido" else "Recolhido"
        }
        toggle.setOnClickListener {
            screen?.releaseInput()
            if (body.visibility == View.VISIBLE) { expandedSections.remove(id); body.visibility = View.GONE }
            else { expandedSections.add(id); body.visibility = View.VISIBLE }
            updateLabel()
        }
        disclosureViews[id] = toggle to body
        updateLabel(); card.addView(toggle); card.addView(caption); card.addView(body); parent.addView(card)
        return Disclosure(body, caption)
    }

    private fun buildFiles(parent: LinearLayout) {
        filesPanel = kit.card(14)
        filesPanel.addView(label("Transferir arquivos", 16f, ink, true))
        val files = kit.responsiveRow(6, 100)
        sendFileButton = button("Enviar", UiKit.ButtonKind.SECONDARY) { pickLocalSource() }
        receiveFileButton = button("Receber", UiKit.ButtonKind.SECONDARY) { transfers?.receiveRequest() }
        cancelFileButton = button("Cancelar", UiKit.ButtonKind.DANGER) { transfers?.cancel() }
        files.addView(sendFileButton, weighted()); files.addView(receiveFileButton, weighted()); files.addView(cancelFileButton, weighted())
        filesPanel.addView(files)
        progress = ProgressBar(this, null, android.R.attr.progressBarStyleHorizontal).apply { max = 1000; progressTintList = ColorStateList.valueOf(accent) }
        filesPanel.addView(progress, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(7)))
        fileStatus = label("Até 100 MiB por arquivo. Confirmação nos dois dispositivos.", 13f, muted).apply { accessibilityLiveRegion = View.ACCESSIBILITY_LIVE_REGION_POLITE }
        filesPanel.addView(fileStatus)
        parent.addView(filesPanel)
    }

    private fun migratePins() {
        val snapshot = prefs.all
        val editor = prefs.edit().remove("password")
        snapshot.keys.filter { it.startsWith("pin:") }.forEach { editor.remove(it) }
        ConnectionPins.migrate(snapshot).forEach { (key, pin) -> editor.putString(key, pin) }
        editor.apply()
    }

    private fun pinPreference(endpoint: String, number: Int) = ConnectionPins.preferenceKey(endpoint, number)

    private fun bindCredentialEndpoint() {
        passwordEndpoint = null
        fun current(): String? = try { pinPreference(address.text.toString().trim(), port.text.toString().toInt()) } catch (_: Exception) { null }
        val endpointWatcher = object : android.text.TextWatcher {
            override fun beforeTextChanged(s: CharSequence?, start: Int, count: Int, after: Int) {}
            override fun onTextChanged(s: CharSequence?, start: Int, before: Int, count: Int) {}
            override fun afterTextChanged(s: android.text.Editable?) {
                if (passwordEndpoint != current()) {
                    if (password.text.isNotEmpty()) password.setText("")
                    setPasswordVisible(false)
                }
            }
        }
        address.addTextChangedListener(endpointWatcher); port.addTextChangedListener(endpointWatcher)
        password.addTextChangedListener(object : android.text.TextWatcher {
            override fun beforeTextChanged(s: CharSequence?, start: Int, count: Int, after: Int) {}
            override fun onTextChanged(s: CharSequence?, start: Int, before: Int, count: Int) {}
            override fun afterTextChanged(s: android.text.Editable?) { passwordEndpoint = if (s.isNullOrEmpty()) null else current() }
        })
    }

    private fun readPin(endpoint: String, number: Int): String? = prefs.getString(pinPreference(endpoint, number), null)

    private fun syncComputers() {
        val records = try { book?.entries().orEmpty() } catch (error: Exception) {
            bookError = error.message ?: "Não foi possível ler o cadastro."; book = null; emptyList()
        }
        for (record in records) {
            val old = savedEntries[record.id]
            if (old != null && (old.address != record.address || old.port != record.port)) {
                availability.remove(record.id); seenSavedAt.remove(record.id)
            }
            val cached = availability[record.id]
            if (cached != null && (cached.endpoint != ComputerBook.endpointKey(record.address, record.port) || cached.pin != readPin(record.address, record.port))) availability.remove(record.id)
        }
        savedEntries.clear(); records.forEach { savedEntries[it.id] = it }
        presenceState.sync(records.map { PresenceListModel.Item(it.id, it.name, ComputerBook.endpointKey(it.address, it.port)) })
        availability.keys.retainAll(savedEntries.keys); seenSavedAt.keys.retainAll(savedEntries.keys)
    }

    private fun renderComputers() {
        if (!::computersList.isInitialized) return
        syncComputers(); requestComputerRender()
    }

    private fun requestComputerRender() {
        if (!alive || !resumed || selectedPage != "computers" || computerRenderScheduled) return
        computerRenderScheduled = true; main.postDelayed(renderComputersTask, 150)
    }

    private fun drawComputers() {
        if (!::computersList.isInitialized) return
        val allRecords = savedEntries.values.toList()
        val records = allRecords.filter { MainUiRules.matchesComputerQuery(it, computerQuery) }
        computerCount?.text = if (computerQuery.isBlank()) "${allRecords.size} dispositivo(s) salvo(s)" else "${records.size} de ${allRecords.size} encontrado(s)"
        computersList.removeAllViews()
        addComputerButton.isEnabled = book != null && allRecords.size < 256
        refreshComputerButton.isEnabled = book != null && allRecords.isNotEmpty()
        if (book == null) {
            computerNotice.text = "Cadastro indisponível. ${bookError.orEmpty()} Os dados existentes foram preservados."
            val empty = kit.card()
            empty.addView(label("A conexão manual está disponível", 18f, ink, true))
            empty.addView(label("Você pode conectar pelo IP enquanto restaura o cadastro.", 14f, muted))
            empty.addView(button("Abrir conexão manual") { showPage("connect") }); computersList.addView(empty)
            return
        }
        if (records.isEmpty() && allRecords.isNotEmpty()) {
            val empty = kit.card()
            empty.addView(label("Nenhum computador encontrado", 20f, ink, true))
            empty.addView(label("Tente outro nome, endereço ou porta. A busca não altera seus cadastros.", 14f, muted))
            empty.addView(button("Limpar busca", UiKit.ButtonKind.SECONDARY) { computerSearch?.setText("") })
            computersList.addView(empty); return
        }
        if (allRecords.isEmpty()) {
            val empty = kit.card()
            empty.addView(label("Sua primeira conexão começa aqui", 20f, ink, true))
            empty.addView(label("Conecte pelo convite do Host. O computador aparecerá aqui após o primeiro acesso.", 14f, muted))
            empty.addView(button("Fazer primeira conexão", UiKit.ButtonKind.PRIMARY) { showPage("connect") })
            computersList.addView(empty); return
        }
        for (record in records) {
            val endpoint = ComputerBook.endpointKey(record.address, record.port)
            val active = peer?.closed == false && endpoint == activeEndpoint
            val result = availability[record.id]?.result
            val state = if (active) "online" else result?.state.orEmpty()
            val title = if (active) "Conectado" else if (state == "online" && result?.busy == true) "Online · ocupado" else when (state) {
                "online" -> "Online"; "offline" -> "Offline"; "unverified" -> "Não verificado"; "saved" -> "Salvo · internet"
                else -> if (presenceState.isPending(record.id)) "Consultando…" else "Sem consulta"
            }
            val color = when (state) { "online" -> Color.rgb(21, 128, 61); "offline" -> Color.rgb(185, 28, 28); "unverified" -> Color.rgb(161, 98, 7); else -> muted }
            val card = kit.card(18)
            val identity = row(12)
            identity.addView(ImageView(this).apply {
                setImageDrawable(UiIconDrawable(UiKit.Icon.DEVICES, ink, dp(30)))
                setPadding(dp(10), dp(10), dp(10), dp(10)); background = kit.background(UiKit.SOFT_MINT, 14)
                importantForAccessibility = View.IMPORTANT_FOR_ACCESSIBILITY_NO
            }, LinearLayout.LayoutParams(dp(50), dp(50)))
            identity.addView(label(record.name, 19f, ink, true), LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f))
            card.addView(identity)
            card.addView(kit.chip(title, color).apply { contentDescription = "Disponibilidade de ${record.name}: $title" })
            card.addView(label(if (isMeshAddress(record.address)) "Internet · código privado do Host" else "Rede local: $endpoint", 14f, muted))
            val seen = record.lastSeen?.let { DateFormat.getDateTimeInstance(DateFormat.SHORT, DateFormat.SHORT).format(Date(it)) } ?: "Ainda não visto online"
            card.addView(label("Última vez online: $seen", 12f, muted))
            result?.detail?.takeIf { it.isNotEmpty() }?.let { card.addView(label(it, 12f, muted).apply { maxLines = 3 }) }
            val actions = kit.responsiveRow(8, 132)
            actions.addView(button("Conectar") { connectSavedComputer(record) }.apply { isEnabled = !connecting && peer == null; contentDescription = "Conectar a ${record.name}" }, weighted())
            actions.addView(button("Editar", UiKit.ButtonKind.SECONDARY) { computerEditor(record) }, weighted())
            card.addView(actions); computersList.addView(card)
        }
    }

    private fun computerEditor(existing: SavedComputer? = null) {
        if (book == null) { showError(bookError ?: "Cadastro indisponível."); return }
        val fields = kit.column(14).apply { setPadding(dp(20), dp(8), dp(20), dp(8)) }
        val alias = edit("Ex.: Escritório", existing?.name.orEmpty()).apply { filters = arrayOf(android.text.InputFilter.LengthFilter(80)) }
        val endpoint = edit("IP ou nome DNS", existing?.address.orEmpty())
        val number = kit.edit("64321", (existing?.port ?: 64321).toString(), InputType.TYPE_CLASS_NUMBER)
        fields.addView(kit.field("Nome do computador", alias))
        if (existing == null || !isMeshAddress(existing.address)) {
            fields.addView(kit.field("IP ou nome do PC", endpoint)); fields.addView(kit.field("Porta", number))
        } else fields.addView(label("Acesso pela internet. Para atualizar o código privado, abra uma nova conexão com o convite do Host.", 13f, muted))
        val generation = sessionGeneration
        controllerGuard(true)
        val builder = AlertDialog.Builder(this).setTitle(if (existing == null) "Adicionar computador" else "Editar computador")
            .setView(scrollPage(fields)).setNegativeButton("Cancelar", null).setPositiveButton("Salvar", null)
        if (existing != null) builder.setNeutralButton("Excluir") { _, _ -> deleteComputer(existing) }
        val dialog = builder.create()
        consentDialogs.add(dialog)
        dialog.setOnDismissListener { consentDialogs.remove(dialog); if (generation == sessionGeneration) controllerGuard(false) }
        dialog.setOnShowListener {
            dialog.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener {
                try {
                    val currentBook = book ?: throw IllegalArgumentException(bookError ?: "Cadastro indisponível.")
                    val selectedPort = number.text.toString().toIntOrNull() ?: throw IllegalArgumentException("Informe uma porta entre 1 e 65535.")
                    if (existing == null) currentBook.add(alias.text.toString(), endpoint.text.toString(), selectedPort)
                    else currentBook.update(existing.id, alias.text.toString(), endpoint.text.toString(), selectedPort)
                    dialog.dismiss(); renderComputers(); refreshComputers(true)
                } catch (error: Exception) { showError(error.message ?: "Não foi possível salvar o computador.") }
            }
        }
        dialog.show()
    }

    private fun deleteComputer(record: SavedComputer) {
        val generation = sessionGeneration; controllerGuard(true)
        val dialog = AlertDialog.Builder(this).setTitle("Excluir computador?")
            .setMessage("${record.name} será removido da lista. Você pode cadastrá-lo novamente.")
            .setNegativeButton("Cancelar", null).setPositiveButton("Excluir") { _, _ ->
                try { codeVault.forget(record.address); book?.remove(record.id); renderComputers() } catch (error: Exception) { showError(error.message ?: "Não foi possível excluir.") }
            }.create()
        consentDialogs.add(dialog)
        dialog.setOnDismissListener { consentDialogs.remove(dialog); if (generation == sessionGeneration) controllerGuard(false) }
        dialog.show(); dialog.getButton(AlertDialog.BUTTON_NEGATIVE).requestFocus()
    }

    private fun isMeshAddress(value: String) = value.matches(Regex("mesh-[0-9a-f]{24}\\.invalid"))

    private fun connectSavedComputer(record: SavedComputer) {
        if (connecting || peer != null) return
        showPage("connect")
        password.setText(""); setPasswordVisible(false)
        if (isMeshAddress(record.address)) {
            localModeToggle.isChecked = false
            try {
                val pairing = codeVault.load(record.address)
                privateCode.setText(pairing?.code.orEmpty())
                rememberCode.isChecked = pairing != null
                status.text = if (pairing != null) "${record.name}: informe a senha atual do Host." else "${record.name}: cole o convite atualizado do Host."
                if (pairing == null) privateCode.requestFocus() else password.requestFocus()
            } catch (error: Exception) { privateCode.setText(""); rememberCode.isChecked = false; showError(error.message ?: "Não foi possível ler o código protegido. Cole um novo convite.") }
        } else {
            localModeToggle.isChecked = true
            address.setText(record.address); port.setText(record.port.toString()); password.requestFocus()
        }
    }

    private fun markSeen(id: String, force: Boolean = false) {
        val now = SystemClock.elapsedRealtime()
        if (!force && now - (seenSavedAt[id] ?: -60_000) < 60_000) return
        try { book?.markSeen(id)?.let { savedEntries[id] = it }; seenSavedAt[id] = now }
        catch (error: Exception) { if (::computerNotice.isInitialized) computerNotice.text = "Status atualizado, mas a última vez online não foi salva: ${error.message.orEmpty()}" }
    }

    private fun rememberConnectedComputer(connection: Peer) {
        val endpoint = connectingEndpoint ?: return
        activeEndpoint = ComputerBook.endpointKey(endpoint.first, endpoint.second)
        presenceState.invalidateEndpoint(activeEndpoint!!)
        try {
            val currentBook = book ?: return
            val record = currentBook.findEndpoint(endpoint.first, endpoint.second) ?: currentBook.add(
                connection.computerName.trim().take(80).ifEmpty { invitationName.ifEmpty { if (isMeshAddress(endpoint.first)) "Meu computador" else endpoint.first.take(80) } }, endpoint.first, endpoint.second)
            markSeen(record.id, true)
            availability[record.id] = Availability(PresenceResult("online", "Sessão ativa neste Controlador."), SystemClock.elapsedRealtime(), activeEndpoint!!, readPin(endpoint.first, endpoint.second))
        } catch (error: Exception) { computerNotice.text = "Conectado, mas não foi possível salvar o computador: ${error.message.orEmpty()}" }
        syncComputers()
    }

    private fun refreshComputers(force: Boolean = false, only: Set<String>? = null) {
        if (!alive || !resumed || book == null) return
        renderComputers()
        if (book == null) return
        val now = SystemClock.elapsedRealtime()
        for (record in savedEntries.values) {
            if (only != null && record.id !in only) continue
            val endpoint = ComputerBook.endpointKey(record.address, record.port)
            if (peer?.closed == false && activeEndpoint == endpoint) {
                availability[record.id] = Availability(PresenceResult("online", "Sessão ativa neste Controlador."), now, endpoint, readPin(record.address, record.port))
                markSeen(record.id); presenceState.cancelDeferred(record.id); continue
            }
            if (isMeshAddress(record.address)) {
                availability[record.id] = Availability(PresenceResult("saved", "A disponibilidade pela internet é confirmada ao conectar."), now, endpoint, readPin(record.address, record.port))
                presenceState.cancelDeferred(record.id)
                continue
            }
            if (!force && !presenceState.isPending(record.id) && now - (availability[record.id]?.checkedAt ?: -10_000) < 10_000) continue
            val pin = readPin(record.address, record.port)
            val token = presenceState.begin(record.id, pin, presenceGeneration, force) ?: continue
            try {
                presenceWorkers.execute {
                    val result = if (alive && resumed) try {
                        PresenceProbe.probe(record.address, record.port, pin, 3000)
                    } catch (error: Exception) { PresenceResult("offline", error.message ?: "Não foi possível consultar o Hospedeiro.") } else null
                    ui { finishPresence(token, result) }
                }
            } catch (_: java.util.concurrent.RejectedExecutionException) {
                presenceState.finish(token, pin, presenceGeneration, false)
            }
        }
        requestComputerRender()
    }

    private fun finishPresence(token: PresenceListModel.Token, result: PresenceResult?) {
        syncComputers()
        val record = savedEntries[token.id]
        val currentPin = record?.let { readPin(it.address, it.port) }
        val decision = presenceState.finish(token, currentPin, presenceGeneration, resumed)
        if (decision.accepted && result != null && record != null && activeEndpoint != token.endpoint) {
            availability[token.id] = Availability(result, SystemClock.elapsedRealtime(), token.endpoint, token.pin)
            if (result.state == "online") markSeen(token.id)
        }
        requestComputerRender()
        if (decision.refresh && alive && resumed) refreshComputers(true, setOf(token.id))
    }

    private fun invalidateActivePresence() {
        activeEndpoint?.let { endpoint ->
            val ids = presenceState.invalidateEndpoint(endpoint)
            ids.forEach { availability.remove(it) }
        }
        activeEndpoint = null; connectingEndpoint = null
    }

    private fun pasteInvitation() {
        if (connecting || peer != null) return
        val clipboard = getSystemService(ClipboardManager::class.java)
        val clip = clipboard.primaryClip
        val text = if (clip != null && clip.itemCount > 0) clip.getItemAt(0).coerceToText(this)?.toString().orEmpty() else ""
        if (text.isBlank()) { showError("Copie o convite no computador antes de tocar em Colar convite."); return }
        try {
            MeshPairing.parseInvitation(text)
            localModeToggle.isChecked = false
            restoringInvitation = true
            try { privateCode.setText(text.take(8192)); password.setText(""); applyInvitationDetails(text) }
            finally { restoringInvitation = false }
            status.text = "Convite colado. Confira a senha e toque em Conectar ao PC."
            if (selected) getSystemService(InputMethodManager::class.java).hideSoftInputFromWindow(root.windowToken, 0)
            privateCode.clearFocus()
        } catch (error: Exception) { showFieldError(privateCode, error.message ?: "O convite não contém um código AliançaDX válido.") }
    }

    private fun applyInvitationDetails(text: String) {
        val secret = Regex("(?m)^Senha:[ \t]*([^\r\n]*)$").find(text)?.groupValues?.get(1)
        if (secret != null && secret.length in 8..256 && password.text.isEmpty()) password.setText(secret)
        invitationName = Regex("(?m)^Computador:[ \t]*([^\r\n]*)$").find(text)?.groupValues?.get(1)?.trim()?.take(80).orEmpty()
    }

    private fun connectController() {
        if (peer != null || connecting) return
        listOf(address, port, password, name, privateCode).forEach { kit.setFieldError(it, null) }
        val pairing = if (localMode) null else try {
            MeshPairing.parseInvitation(privateCode.text.toString()).also {
                require(it.fingerprint.matches(Regex("[0-9A-F]{64}"))) { "Copie um convite atualizado no Host para conferir a identidade do PC." }
                applyInvitationDetails(privateCode.text.toString())
            }
        } catch (error: Exception) { showFieldError(privateCode, error.message ?: "Cole o código completo do Host."); return }
        val number = pairing?.port ?: port.text.toString().toIntOrNull()
        if (number == null || number !in 1..65535) { showFieldError(port, "Informe uma porta entre 1 e 65535."); return }
        val endpoint = pairing?.address ?: try { ComputerBook.canonicalAddress(address.text.toString().trim()) }
            catch (error: Exception) { showFieldError(address, error.message ?: "Informe um IP ou nome válido."); return }
        if (localMode && isMeshAddress(endpoint)) { showFieldError(address, "Esse é um identificador de conexão pela internet. Desative a conexão por IP e cole o convite."); return }
        val secret = password.text.toString()
        if (secret.length !in 8..256) { showFieldError(password, "Informe a senha atual do Host, com 8 a 256 caracteres."); return }
        val user = name.text.toString().trim().take(80).ifEmpty { Build.MODEL.take(80) }
        val remember = rememberCode.isChecked && pairing != null
        // Keep only the canonical code in the field; a pasted invitation can contain a password.
        if (pairing != null) {
            restoringInvitation = true
            try { privateCode.setText(pairing.code) } finally { restoringInvitation = false }
        }
        val editor = prefs.edit().putString("name", user)
        if (pairing == null) editor.putString("address", endpoint).putInt("port", number)
        editor.apply()
        clearRecovery()
        val attempt = ConnectionAttempt(pairing, endpoint, number, secret, user, remember)
        if (reconnectToggle.isChecked) recoveryCredential = attempt
        startConnectionAttempt(attempt, false)
    }

    private fun startConnectionAttempt(attempt: ConnectionAttempt, retry: Boolean) {
        if (!alive || isFinishing || isDestroyed) { clearRecovery(); return }
        val pairing = attempt.pairing
        val endpoint = attempt.endpoint
        val number = attempt.port
        val secret = attempt.secret
        val user = attempt.user
        val remember = attempt.remember
        sessionGeneration += 1
        val generation = sessionGeneration
        streamFeedback.reset(); screen?.clearFrame(); latestFrame = null
        connectCancelled = AtomicBoolean(false)
        val cancellation = connectCancelled
        connectingEndpoint = endpoint to number; activePairing = pairing
        commands.clear(); networkSuspended = true; captureAvailable = true; frameStale = false
        lastFrameDisplayed = 0L; frameWaitingSince = SystemClock.elapsedRealtime()
        connecting = true; clearBuffered(); showPage("session")
        connectingStatus = if (retry) "Reconectando ao PC · tentativa ${recoveryAttempt}…"
            else if (pairing == null) "Conectando ao PC pela rede local…" else "Preparando conexão pela internet…"
        status.text = connectingStatus; updateControls()
        val transport = if (pairing != null) MeshTransport(applicationContext) else null
        meshTransport = transport
        Thread({
            try {
                if (cancellation.get() || generation != sessionGeneration || !alive) throw IOException("Conexão cancelada.")
                val target = if (pairing != null && transport != null) {
                    transport.start(pairing) { event -> ui {
                        if (generation == sessionGeneration && connecting) {
                            connectingStatus = if (retry) "Reconectando · ${event.message}" else event.message
                            status.text = connectingStatus; updateControls()
                        }
                    } }
                    val timeout = if (retry) ConnectionRecovery.remaining(SystemClock.elapsedRealtime(), recoveryDeadline).coerceAtMost(60_000) else 60_000
                    if (timeout <= 0) throw IOException("O prazo de reconexão terminou.")
                    transport.awaitReady(timeout, cancellation)
                } else MeshEndpoint(endpoint, number)
                if (cancellation.get() || generation != sessionGeneration || !alive) throw IOException("Conexão cancelada.")
                ui { if (generation == sessionGeneration) { connectingStatus = "Identificando o PC e aguardando aprovação no Host…"; status.text = connectingStatus; updateControls() } }
                val connection = AndroidNet.connect(target.address, target.port, secret, user,
                    { fingerprint ->
                        if (pairing != null) {
                            val actual = fingerprint.replace(":", "").uppercase(Locale.ROOT)
                            if (actual != pairing.fingerprint) throw IOException("A identidade do PC não corresponde ao convite. Peça um novo convite ao responsável.")
                            val previous = readPin(endpoint, number)
                            val current = !cancellation.get() && generation == sessionGeneration && alive
                            if (retry && previous != actual) throw SecurityException("O certificado do PC mudou. A reconexão automática foi interrompida; confira a identidade e conecte novamente.")
                            if (previous == null && current) {
                                prefs.edit().putString(pinPreference(endpoint, number), actual).apply()
                                true
                            } else if (previous == actual) current
                            else approveCertificate(endpoint, number, fingerprint, generation)
                        } else if (retry) {
                            val expected = readPin(endpoint, number)
                            val actual = fingerprint.replace(":", "").uppercase(Locale.ROOT)
                            if (expected == null || actual != expected) throw SecurityException("O certificado do PC mudou. A reconexão automática foi interrompida; confira a identidade e conecte novamente.")
                            !cancellation.get() && generation == sessionGeneration && alive
                        } else approveCertificate(endpoint, number, fingerprint, generation)
                    },
                    { message -> receiveNetwork(message, generation) },
                    { reason -> ui { disconnected(reason, generation) } }, cancellation,
                    if (retry) ConnectionRecovery.remaining(SystemClock.elapsedRealtime(), recoveryDeadline).coerceAtLeast(1) else 180_000)
                main.post {
                    if (generation != sessionGeneration || !alive || cancellation.get()) { connection.close(); transport?.close() }
                    else if (connection.closed) disconnected("O Host encerrou a conexão.", generation)
                    else {
                        peer = connection; connecting = false; sharing = true; ownerPaused = false; remotePrompt = false
                        resetTrafficRate(connection)
                        recovering = false; hadAuthorizedSession = true; recoveryDeadline = 0L; recoveryAttempt = 0
                        recoveryTask?.let { main.removeCallbacks(it) }; recoveryTask = null
                        recoveryExpiryTask?.let { main.removeCallbacks(it) }; recoveryExpiryTask = null
                        if (!reconnectToggle.isChecked) recoveryCredential = null
                        networkSuspended = false; captureAvailable = true; frameWaitingSince = SystemClock.elapsedRealtime()
                        if (pairing != null) {
                            try {
                                if (remember) codeVault.save(pairing) else codeVault.forget(pairing.address)
                            } catch (error: Exception) { showError("Conectado, mas não foi possível atualizar o código salvo: ${error.message.orEmpty()}") }
                        }
                        rememberConnectedComputer(connection)
                        installTransfers(connection, generation); status.text = "Conectado a ${connection.computerName.ifBlank { "seu PC" }}"
                        // Older Hosts ignore this optional capability; v1 Hosts pace from actual draws.
                        commands.offer(connection, generation, StreamFeedback.ready())
                        password.setText(""); setPasswordVisible(false)
                        showPage("session"); updateControls()
                        val pending = buffered.toList(); clearBuffered(); pending.forEach { receive(it, generation) }
                    }
                }
            } catch (error: Exception) {
                transport?.close()
                ui { if (generation == sessionGeneration) disconnected(error.message ?: "Não foi possível conectar.", generation) }
            }
        }, "MiniAnyDesk-connect").start()
    }

    private fun approveCertificate(endpoint: String, number: Int, fingerprint: String, generation: Int): Boolean {
        val pin = fingerprint.replace(":", "").uppercase()
        if (!pin.matches(Regex("[0-9A-F]{64}"))) return false
        val key = pinPreference(endpoint, number)
        val old = prefs.getString(key, null)
        if (old == pin) return generation == sessionGeneration && alive
        val finished = CountDownLatch(1)
        val approved = AtomicBoolean(false)
        val expired = AtomicBoolean(false)
        val dialogRef = AtomicReference<AlertDialog?>(null)
        main.post {
            if (!alive || isFinishing || isDestroyed || generation != sessionGeneration || !resumed || expired.get()) { finished.countDown(); return@post }
            activity.selectSession(sessionId)
            controllerGuard(true)
            val formatted = pin.chunked(2).joinToString(":")
            val text = if (old == null) "Compare este código com o exibido no Hospedeiro:\n\n$formatted\n\nOs códigos são iguais e você confia neste dispositivo?"
            else "O certificado de $endpoint:$number mudou.\n\nANTERIOR:\n${old.chunked(2).joinToString(":")}\n\nNOVO:\n$formatted\n\nConfirme a mudança com o responsável antes de confiar."
            var completed = false
            val dialog = AlertDialog.Builder(this).setTitle(if (old == null) "Conferir identidade · $sessionTitle" else "Certificado alterado · $sessionTitle")
                .setMessage(text).setNegativeButton("Cancelar", null)
                .setPositiveButton("Os códigos são iguais") { _, _ ->
                    if (generation == sessionGeneration && alive && !expired.get()) { prefs.edit().putString(key, pin).apply(); approved.set(true) }
                }.create()
            dialogRef.set(dialog)
            consentDialogs.add(dialog)
            dialog.setOnDismissListener { consentDialogs.remove(dialog); if (!completed) { completed = true; if (generation == sessionGeneration) controllerGuard(false); finished.countDown() } }
            dialog.show()
            dialog.getButton(AlertDialog.BUTTON_POSITIVE).filterTouchesWhenObscured = true
            dialog.getButton(AlertDialog.BUTTON_NEGATIVE).requestFocus()
        }
        val deadline = System.nanoTime() + TimeUnit.SECONDS.toNanos(90)
        while (!finished.await(100, TimeUnit.MILLISECONDS)) {
            if (!alive || generation != sessionGeneration || System.nanoTime() > deadline) {
                expired.set(true); main.post { dialogRef.get()?.dismiss() }; return false
            }
        }
        return approved.get() && alive && generation == sessionGeneration
    }

    private fun receive(message: JSONObject, generation: Int) {
        if (generation != sessionGeneration || !alive) return
        if (peer == null) {
            if (connecting && buffered.size < 128) {
                if (message.optString("type") == "screen") {
                    buffered.filter { it.optString("type") == "screen" }.forEach { bufferedBytes -= it.toString().toByteArray(Charsets.UTF_8).size }
                    buffered.removeAll { it.optString("type") == "screen" }
                }
                val bytes = message.toString().toByteArray(Charsets.UTF_8).size
                if (bufferedBytes + bytes > 8L * 1024 * 1024) { stopAll("Limite de mensagens de conexão excedido."); return }
                bufferedBytes += bytes
                buffered.add(message)
            }
            return
        }
        if (transfers?.handle(message) == true) return
        when (message.optString("type")) {
            "session_end" -> stopAll(message.optString("reason", "Sessão encerrada pelo responsável no PC.").take(400))
            "screen" -> if (selected && sharing && captureAvailable && !networkSuspended && !ownerPaused && !remotePrompt) {
                val selected = message.optString("monitorId", "all").let { if (it == "0") "all" else it }
                if (pendingMonitor == null || pendingMonitor == selected) { pendingMonitor = null; screen?.showFrame(message) }
            }
            "state" -> {
                ownerPaused = message.optBoolean("localPaused")
                remotePrompt = message.optBoolean("promptActive")
                captureAvailable = message.optBoolean("captureAvailable", true)
                val wasSharing = sharing
                sharing = message.optBoolean("sharing") && !ownerPaused && captureAvailable
                networkSuspended = !sharing || remotePrompt || pendingBlocks.get() > 0
                if (!sharing || remotePrompt) { pendingScreen.set(null); latestFrame = null; screen?.clearFrame() }
                if (sharing && !remotePrompt && (!wasSharing || lastFrameDisplayed == 0L)) {
                    frameStale = false; lastFrameDisplayed = 0L; frameWaitingSince = SystemClock.elapsedRealtime()
                }
                status.text = when { ownerPaused -> "Pausado pelo responsável no Hospedeiro"; remotePrompt -> "Aguardando confirmação no Hospedeiro"; !captureAvailable -> "Captura temporariamente indisponível · aguardando o Hospedeiro"; !sharing -> "Compartilhamento pausado"; else -> "Conectado · aguardando imagem" }
                updateControls()
            }
            "error", "log" -> {
                val diagnostic = message.optString("message").take(400)
                status.text = diagnostic
                if (selectedPage == "session") sessionHint?.text = diagnostic
            }
        }
    }

    private fun send(message: JSONObject) {
        if (message.optString("type") == "input" && !MainUiRules.inputIsRelease(message) &&
            (!selected || !sharing || !captureAvailable || frameStale || networkSuspended || ownerPaused || remotePrompt || controllerPrompts > 0)) return
        val connection = peer ?: return
        commands.offer(connection, sessionGeneration, message)
    }

    private fun requestSharing(enabled: Boolean) {
        if (enabled && (ownerPaused || remotePrompt || !captureAvailable)) return
        screen?.releaseInput()
        send(JSONObject().put("type", "sharing").put("enabled", enabled))
        if (!enabled) { sharing = false; latestFrame = null; screen?.clearFrame() }
        updateControls()
    }

    private fun updateMonitors(frame: JSONObject) {
        val options = ArrayList<Monitor>(); options.add(Monitor("all", "Todos os monitores"))
        val monitors = frame.optJSONArray("monitors")
        for (index in 0 until minOf(monitors?.length() ?: 0, 64)) {
            val monitor = monitors?.optJSONObject(index) ?: continue
            val id = monitor.optString("id")
            if (id.toIntOrNull() == null || id == "0") continue
            options.add(Monitor(id, "Monitor $id (${monitor.optInt("width")} × ${monitor.optInt("height")})"))
        }
        val spinner = monitorSpinner ?: return
        val selected = frame.optString("monitorId", "all").let { if (it == "0") "all" else it }
        val selectedIndex = options.indexOfFirst { it.id == selected }.coerceAtLeast(0)
        if (options == monitorOptions && spinner.selectedItemPosition == selectedIndex) return
        settingMonitor = true
        if (options != monitorOptions) { monitorOptions = options; spinner.adapter = ArrayAdapter(this, android.R.layout.simple_spinner_dropdown_item, options) }
        spinner.setSelection(selectedIndex, false)
        spinner.post { settingMonitor = false }
    }

    private fun receiveNetwork(message: JSONObject, generation: Int) {
        if (!alive || generation != sessionGeneration) return
        val files = transferBinding
        if (files?.first == generation && files.second.handle(message)) return
        if (message.optString("type") == "screen") {
            pendingScreen.set(generation to message)
            postScreen()
            return
        }
        if (message.optString("type") == "log") {
            pendingLog.set(generation to message)
            postLog(); return
        }
        val blocking = message.optString("type") == "state" && (!message.optBoolean("sharing") || message.optBoolean("promptActive") ||
            message.optBoolean("localPaused") || !message.optBoolean("captureAvailable", true))
        if (blocking) { pendingBlocks.incrementAndGet(); networkSuspended = true; pendingScreen.set(null) }
        val bytes = message.toString().toByteArray(Charsets.UTF_8).size.toLong()
        val count = pendingMessages.incrementAndGet()
        val total = pendingBytes.addAndGet(bytes)
        if (count > 128 || total > 8L * 1024 * 1024) {
            pendingMessages.decrementAndGet(); pendingBytes.addAndGet(-bytes)
            if (blocking) pendingBlocks.decrementAndGet()
            reportMailboxOverflow(generation)
            return
        }
        main.post {
            try { if (alive) receive(message, generation) }
            finally {
                pendingMessages.decrementAndGet(); pendingBytes.addAndGet(-bytes)
                if (blocking) {
                    pendingBlocks.decrementAndGet()
                    // An old reader may have crossed the generation change before posting.
                    // Derive the gate from the current session after every barrier completes.
                    networkSuspended = !sharing || !captureAvailable || ownerPaused || remotePrompt || pendingBlocks.get() > 0
                    if (alive) updateControls()
                }
            }
        }
    }

    private fun reportMailboxOverflow(generation: Int) {
        while (true) {
            val previous = mailboxOverflowGeneration.get()
            if (generation <= previous) return
            if (mailboxOverflowGeneration.compareAndSet(previous, generation)) break
        }
        main.post {
            if (alive && generation == sessionGeneration) {
                peer?.close("Limite de mensagens pendentes excedido.")
                disconnected("A conexão enviou mensagens demais. Tente conectar novamente.", generation)
            }
            networkSuspended = !sharing || !captureAvailable || ownerPaused || remotePrompt || pendingBlocks.get() > 0
            if (alive) updateControls()
        }
    }

    private fun postLog() {
        if (!logPosted.compareAndSet(false, true)) return
        main.post {
            val item = pendingLog.getAndSet(null); logPosted.set(false)
            if (alive && item != null && item.first == sessionGeneration) receive(item.second, item.first)
            if (pendingLog.get() != null) postLog()
        }
    }

    private fun postScreen() {
        if (!screenPosted.compareAndSet(false, true)) return
        main.post {
            val frame = pendingScreen.getAndSet(null)
            screenPosted.set(false)
            if (alive && frame != null) receive(frame.second, frame.first)
            if (pendingScreen.get() != null) postScreen()
        }
    }

    fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        if (requestCode == REQUEST_SOURCE || requestCode == REQUEST_DESTINATION) {
            val callback = pickerCallback
            pickerCallback = null
            callback?.invoke(if (resultCode == Activity.RESULT_OK) data?.data else null)
            updateControls()
        }
    }

    private fun installTransfers(connection: Peer, generation: Int) {
        val installed = FileTransfers(applicationContext, connection,
            dispatch = { task -> main.post { task() } },
            askReceive = { offer, callback ->
                if (!validPrompt(connection, generation)) callback(null)
                else confirmFile("Receber arquivo?", "${offer.optString("name")}\n${offer.optLong("size")} bytes\n\nEscolha onde salvar uma nova cópia.",
                    callback) { launchDestination(offer, callback) }
            },
            onStatus = { event ->
                if (event.cleanupRequired && alive) {
                    Toast.makeText(applicationContext, event.message, Toast.LENGTH_LONG).show()
                }
                if (generation == sessionGeneration && peer === connection && alive) {
                    val wasActive = transferActive
                    transferActive = event.active
                    fileStatus.text = event.message
                    progress.progress = (event.progress.coerceIn(0f, 1f) * 1000).toInt()
                    filesToggle?.text = if (event.active) "Arquivos ●" else "Arquivos"
                    if (MainUiRules.shouldOpenTransferPanel(wasActive, event.active, true, selectedPage, fullscreen)) {
                        toggleFiles(true)
                    }
                    updateControls()
                }
            },
            onRequestFile = { callback ->
                if (!validPrompt(connection, generation)) callback(null)
                else confirmFile("O visitante pediu um arquivo", "Você decide se deseja enviar e qual arquivo compartilhar.",
                    callback) { launchSource(callback) }
            },
            promptGuard = { enabled ->
                if (generation == sessionGeneration) controllerGuard(enabled)
            })
        transfers = installed; transferBinding = generation to installed
    }

    private fun validPrompt(connection: Peer, generation: Int) = alive && resumed &&
        peer === connection && generation == sessionGeneration && pickerCallback == null && !isFinishing

    private fun confirmFile(title: String, message: String, callback: (Uri?) -> Unit, accept: () -> Unit) {
        activity.selectSession(sessionId)
        var accepted = false
        AlertDialog.Builder(this).setTitle("$title · $sessionTitle").setMessage(message)
            .setNegativeButton("Recusar", null)
            .setPositiveButton("Escolher arquivo") { _, _ -> accepted = true }
            .create().apply {
                consentDialogs.add(this)
                setOnDismissListener {
                    consentDialogs.remove(this)
                    if (accepted && alive && resumed) accept() else callback(null)
                }
                show()
                getButton(AlertDialog.BUTTON_POSITIVE).filterTouchesWhenObscured = true
                getButton(AlertDialog.BUTTON_NEGATIVE).requestFocus()
            }
    }

    private fun pickLocalSource() {
        val current = transfers ?: return
        if (pickerCallback != null || transferActive) return
        val generation = sessionGeneration
        controllerGuard(true)
        launchSource { uri ->
            try { if (uri != null && transfers === current && generation == sessionGeneration) current.sendFile(uri) }
            finally {
                if (generation == sessionGeneration) controllerGuard(false)
            }
        }
    }

    private fun launchSource(callback: (Uri?) -> Unit) {
        launchPicker(Intent(Intent.ACTION_OPEN_DOCUMENT).addCategory(Intent.CATEGORY_OPENABLE).setType("*/*"), REQUEST_SOURCE, callback)
    }

    private fun launchDestination(offer: JSONObject, callback: (Uri?) -> Unit) {
        // CREATE_DOCUMENT gives a new document; never use OPEN_DOCUMENT as the receive destination.
        launchPicker(Intent(Intent.ACTION_CREATE_DOCUMENT).addCategory(Intent.CATEGORY_OPENABLE)
            .setType("application/octet-stream").putExtra(Intent.EXTRA_TITLE, offer.optString("name", "arquivo")), REQUEST_DESTINATION, callback)
    }

    private fun launchPicker(intent: Intent, request: Int, callback: (Uri?) -> Unit) {
        if (!alive || pickerCallback != null) { callback(null); return }
        pickerCallback = callback
        try { startActivityForResult(intent, request); updateControls() }
        catch (error: Exception) { pickerCallback = null; callback(null); showError(error.message ?: "Não há seletor de arquivos disponível.") }
    }

    private fun controllerGuard(enabled: Boolean) {
        synchronized(promptLock) { controllerPrompts = (controllerPrompts + if (enabled) 1 else -1).coerceAtLeast(0) }
        ui { screen?.releaseInput(); updateControls(); if (!enabled) requestStreamResume() }
    }

    private fun inputReady() = selected && resumed && selectedPage == "session" && peer?.closed == false && latestFrame != null &&
        sharing && captureAvailable && !frameStale && !networkSuspended && !ownerPaused && !remotePrompt && controllerPrompts == 0

    private fun updateControls() {
        if (!::connectButton.isInitialized || !alive) return
        val connected = peer?.closed == false
        val stateColor = when {
            ownerPaused || remotePrompt -> UiKit.DEEP_GREEN
            connected -> Color.rgb(21, 128, 61)
            connecting -> UiKit.EMERALD
            else -> muted
        }
        status.setTextColor(stateColor)
        status.background = kit.background(Color.argb(19, Color.red(stateColor), Color.green(stateColor), Color.blue(stateColor)), 24, Color.TRANSPARENT)
        if (selected) {
            if (connected && resumed && selectedPage == "session") window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
            else window.clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        }
        activity.sessionChanged(this)
        val idle = !connected && !connecting
        connectButton.isEnabled = idle; disconnectButton.isEnabled = connected || connecting
        address.isEnabled = idle; name.isEnabled = idle; port.isEnabled = idle
        password.isEnabled = idle; privateCode.isEnabled = idle; rememberCode.isEnabled = idle; localModeToggle.isEnabled = idle; reconnectToggle.isEnabled = idle
        passwordToggle?.isEnabled = idle
        screen?.setInputAllowed(inputReady())
        quickClickButtons.forEach { it.isEnabled = inputReady() }
        monitorSpinner?.isEnabled = inputReady()
        modifierButtons.values.forEach { it.isEnabled = inputReady() }
        sendFileButton.isEnabled = connected && !transferActive && pickerCallback == null
        receiveFileButton.isEnabled = connected && !transferActive && pickerCallback == null
        cancelFileButton.isEnabled = connected && transferActive
        if (::cancelConnectButton.isInitialized) cancelConnectButton.visibility = if (connecting) View.VISIBLE else View.GONE
        sessionHint?.text = when {
            connecting -> connectingStatus
            ownerPaused -> "O responsável pausou a tela no PC."
            remotePrompt -> "Aguardando confirmação no PC."
            connected && !captureAvailable -> "Captura temporariamente indisponível. Aguardando o PC."
            connected && frameStale -> "Sem imagem recente · conexão aberta. Aguardando nova captura."
            connected && !sharing -> "Tela pausada. Abra Controles para retomar."
            connected && latestFrame == null -> "Conectado · aguardando a primeira imagem."
            connected -> "Toque: clicar · botões: duplo ou direito"
            else -> "Conecte ao PC para ver e controlar a tela."
        }
        sessionPlaceholder?.visibility = if (connecting || !connected || latestFrame == null) View.VISIBLE else View.GONE
        sessionPlaceholderTitle?.text = when {
            connecting -> "Conectando ao seu PC"
            ownerPaused || (connected && !sharing) -> "Tela pausada"
            remotePrompt -> "Confirmação no PC"
            connected -> "Aguardando a imagem"
            else -> "Seu PC aparece aqui"
        }
        sessionPlaceholderBody?.text = when {
            connecting -> connectingStatus
            connected -> sessionHint?.text
            else -> "Cole o convite do Host na aba Conectar para começar."
        }
        sessionPlaceholderProgress?.visibility = if (connecting || (connected && sharing && !ownerPaused && !remotePrompt)) View.VISIBLE else View.GONE
        sessionPlaceholderButton?.visibility = if (!connected) View.VISIBLE else View.GONE
        sessionPlaceholderButton?.text = if (connecting) "Cancelar conexão" else "Conectar ao PC"
        pauseButton?.isEnabled = connected && sharing
        resumeButton?.isEnabled = connected && !sharing && !ownerPaused && !remotePrompt && captureAvailable
        updateSessionChrome()
    }

    private fun disconnected(reason: String, generation: Int) {
        if (generation != sessionGeneration || !alive || (peer == null && !connecting)) return
        if (beginRecovery(reason)) return
        stopAll(reason)
        showPage("connect")
    }

    private fun clearRecovery() {
        recoveryTask?.let { main.removeCallbacks(it) }; recoveryTask = null
        recoveryExpiryTask?.let { main.removeCallbacks(it) }; recoveryExpiryTask = null
        recoveryCredential = null; recoveryDeadline = 0L; recoveryAttempt = 0
        recovering = false; hadAuthorizedSession = false
    }

    private fun beginRecovery(reason: String): Boolean {
        val attempt = recoveryCredential ?: return false
        if (!hadAuthorizedSession || !reconnectToggle.isChecked || !resumed || isFinishing || isDestroyed || !ConnectionRecovery.retryable(reason)) return false
        val now = SystemClock.elapsedRealtime()
        if (!recovering) {
            recoveryDeadline = ConnectionRecovery.deadline(now)
            recoveryAttempt = 0
            recovering = true
            val expiry = Runnable {
                if (recovering && alive) stopAll("Não foi possível reconectar em 2 minutos. Confira o Host e toque em Conectar novamente.")
            }
            recoveryExpiryTask = expiry
            main.postDelayed(expiry, ConnectionRecovery.remaining(now, recoveryDeadline))
        }
        val remaining = ConnectionRecovery.remaining(now, recoveryDeadline)
        val delay = ConnectionRecovery.delayMs(recoveryAttempt)
        if (remaining <= delay) return false
        closeSessionTransport()
        connecting = true
        recoveryAttempt++
        connectingStatus = "A rede caiu. Tentativa $recoveryAttempt em ${delay / 1000}s · nova aprovação será pedida no PC."
        status.text = connectingStatus
        showPage("session"); updateControls()
        val epoch = sessionGeneration
        val task = Runnable {
            recoveryTask = null
            if (alive && resumed && !isFinishing && !isDestroyed && recovering && epoch == sessionGeneration &&
                ConnectionRecovery.remaining(SystemClock.elapsedRealtime(), recoveryDeadline) > 0) {
                startConnectionAttempt(attempt, true)
            } else if (recovering) stopAll("Reconexão interrompida. Abra o aplicativo e conecte novamente.")
        }
        recoveryTask?.let { main.removeCallbacks(it) }; recoveryTask = task
        main.postDelayed(task, delay)
        return true
    }

    private fun closeSessionTransport() {
        commandsVisibility.restore()
        resetTrafficRate(null)
        streamResumeTask?.let { main.removeCallbacks(it) }; streamResumeTask = null
        invalidateActivePresence()
        screen?.releaseInput()
        sessionGeneration++
        commands.clear(); pendingLog.set(null); networkSuspended = true
        consentDialogs.toList().forEach { it.dismiss() }
        connectCancelled.set(true); connecting = false
        val oldPeer = peer; peer = null
        val oldTransfers = transfers; transfers = null; transferBinding = null
        val oldMesh = meshTransport; meshTransport = null; activePairing = null
        oldTransfers?.close(); oldPeer?.close()
        if (oldMesh != null) Thread({ oldMesh.close() }, "MiniAnyDesk-mesh-close").start()
        controllerPrompts = 0; transferActive = false; clearBuffered()
        sharing = false; ownerPaused = false; remotePrompt = false; latestFrame = null; pendingMonitor = null; pendingScreen.set(null)
        captureAvailable = true; frameStale = false; lastFrameDisplayed = 0L; frameWaitingSince = 0L
        screen?.clearFrame(); resetModifiers()
        if (::progress.isInitialized) progress.progress = 0
        if (::fileStatus.isInitialized) fileStatus.text = "Transferências encerradas. Inicie um novo envio após reconectar."
    }

    private fun stopAll(message: String = "Sessão encerrada") {
        clearRecovery()
        closeSessionTransport()
        connectingStatus = ""
        // A late document-picker result still reaches the closed transfer for cleanup.
        if (!alive) { val callback = pickerCallback; pickerCallback = null; callback?.invoke(null) }
        if (::password.isInitialized) { setPasswordVisible(false); password.setText("") }
        if (alive && ::status.isInitialized) {
            status.text = message.take(400)
            if (fullscreen) setFullscreen(false)
            showPage("connect"); renderComputers(); refreshComputers(true); updateControls()
        }
    }

    private fun resetModifiers() {
        modifiers.clear()
        for ((key, control) in modifierButtons) control.text = when (key) { "ctrl" -> "Ctrl"; "alt" -> "Alt"; "shift" -> "Shift"; else -> "Win" }
    }

    private fun clearBuffered() { buffered.clear(); bufferedBytes = 0L }

    private fun setFullscreen(enabled: Boolean) {
        if (!::sessionPage.isInitialized) return
        screen?.releaseInput()
        commandsVisibility.restore()
        if (enabled && selectedPage != "session") showPage("session")
        fullscreen = enabled
        requestedOrientation = if (enabled) ActivityInfo.SCREEN_ORIENTATION_SENSOR_LANDSCAPE else ActivityInfo.SCREEN_ORIENTATION_UNSPECIFIED
        chrome.visibility = if (enabled || compactSession()) View.GONE else View.VISIBLE
        navigation.visibility = if (enabled) View.GONE else View.VISIBLE
        sessionToolbar.visibility = if (enabled) View.GONE else View.VISIBLE
        sessionStatusRow?.visibility = if (enabled) View.GONE else View.VISIBLE
        filesOverlay?.visibility = View.GONE; toolsOverlay?.visibility = View.GONE
        fullScreenActions?.visibility = if (enabled) View.VISIBLE else View.GONE
        if (!selected) { updateSessionChrome(); return }
        if (enabled) window.addFlags(WindowManager.LayoutParams.FLAG_FULLSCREEN)
        else window.clearFlags(WindowManager.LayoutParams.FLAG_FULLSCREEN)
        if (Build.VERSION.SDK_INT >= 30) {
            window.insetsController?.systemBarsBehavior = android.view.WindowInsetsController.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE
            if (enabled) window.insetsController?.hide(WindowInsets.Type.systemBars())
            else window.insetsController?.show(WindowInsets.Type.systemBars())
        }
        updateSessionChrome()
        if (selected) root.requestApplyInsets(); screen?.releaseInput()
        if (selected) getSystemService(InputMethodManager::class.java).hideSoftInputFromWindow(root.windowToken, 0)
    }

    fun onResume() {
        resumed = true; presenceGeneration++
        resetTrafficRate(peer?.takeUnless { it.closed })
        main.removeCallbacks(trafficTick); main.postDelayed(trafficTick, 1_000)
        main.removeCallbacks(presenceTick)
        main.removeCallbacks(frameHealthTick); main.postDelayed(frameHealthTick, 1000)
        if (::status.isInitialized) { updateControls(); refreshComputers(true); main.postDelayed(presenceTick, 10_000) }
        requestStreamResume()
    }
    fun saveState(outState: Bundle) {
        outState.putString("computer_search", computerQuery)
    }

    private fun compactSession() = selectedPage == "session"

    private fun resetTrafficRate(connection: Peer?) {
        trafficPeer = connection
        trafficRate = SessionTrafficRate.ZERO
        trafficWindow.reset(SystemClock.elapsedRealtime(), connection?.receivedBytes ?: 0, connection?.sentBytes ?: 0)
        renderTrafficRate()
    }

    private fun refreshTrafficRate() {
        val connection = peer?.takeUnless { it.closed }
        if (connection !== trafficPeer) resetTrafficRate(connection)
        else if (connection != null) {
            trafficRate = trafficWindow.sample(SystemClock.elapsedRealtime(), connection.receivedBytes, connection.sentBytes)
            renderTrafficRate()
        }
        updateSessionChrome()
    }

    private fun renderTrafficRate() {
        trafficStatus?.text = if (resources.configuration.fontScale >= 1.5f)
            String.format(Locale.forLanguageTag("pt-BR"), "Sessão ↓ %.2f Mb/s\n↑ %.2f Mb/s", trafficRate.receivedMbps, trafficRate.sentMbps)
            else trafficRate.compactLabel()
        trafficStatus?.contentDescription = String.format(Locale.forLanguageTag("pt-BR"),
            "Tráfego desta sessão: recebendo %.2f megabits por segundo e enviando %.2f megabits por segundo. " +
                "Média de até 3 segundos, incluindo imagem, comandos e arquivos. Não mede a capacidade da internet.",
            trafficRate.receivedMbps, trafficRate.sentMbps)
    }

    private fun setCommandsHidden(hidden: Boolean) {
        // Release before changing hit targets or hiding a pending long press.
        screen?.releaseInput()
        quickClickButtons.forEach { it.cancelPendingInputEvents(); it.isPressed = false }
        if (hidden) commandsVisibility.hide(fullscreen, selected, inputReady()) else commandsVisibility.restore()
        toolsOverlay?.visibility = View.GONE; filesOverlay?.visibility = View.GONE
        if (selected) getSystemService(InputMethodManager::class.java).hideSoftInputFromWindow(root.windowToken, 0)
        updateSessionChrome()
    }

    private fun updateSessionChrome() {
        commandsVisibility.reconcile(fullscreen, selected, inputReady())
        val hidden = commandsVisibility.hidden
        val panelOpen = toolsOverlay?.visibility == View.VISIBLE || filesOverlay?.visibility == View.VISIBLE
        quickMouseBar?.visibility = if (hidden || peer?.closed != false || latestFrame == null || keyboardVisible || panelOpen) View.GONE else View.VISIBLE
        if (selected) activity.setSessionChrome(fullscreen || keyboardVisible)
        if (::chrome.isInitialized) chrome.visibility = if (fullscreen || compactSession() || keyboardVisible) View.GONE else View.VISIBLE
        if (::navigation.isInitialized) navigation.visibility = if (fullscreen || keyboardVisible) View.GONE else View.VISIBLE
        if (::sessionToolbar.isInitialized) sessionToolbar.visibility = if (fullscreen || keyboardVisible) View.GONE else View.VISIBLE
        navigationDivider?.visibility = if (fullscreen || keyboardVisible) View.GONE else View.VISIBLE
        toolbarDivider?.visibility = if (fullscreen || keyboardVisible) View.GONE else View.VISIBLE
        sessionStatusRow?.visibility = if (fullscreen || keyboardVisible) View.GONE else View.VISIBLE
        fullScreenActions?.visibility = if (fullscreen && !hidden && !keyboardVisible && !panelOpen) View.VISIBLE else View.GONE
        trafficStatus?.visibility = if (hidden || peer?.closed != false || keyboardVisible || panelOpen || selectedPage != "session") View.GONE else View.VISIBLE
        trafficStatus?.let { label ->
            val params = label.layoutParams as? FrameLayout.LayoutParams
            val gravity = (if (fullscreen) Gravity.BOTTOM else Gravity.TOP) or Gravity.START
            val mouseHeight = quickClickButtons.maxOfOrNull { (it as? MouseControlButton)?.preferredHeightPx() ?: it.minimumHeight } ?: dp(78)
            val bottomMargin = if (fullscreen) maxOf(quickMouseBar?.height ?: 0, mouseHeight + dp(4)) + dp(20) else dp(10)
            if (params != null && (params.gravity != gravity || params.bottomMargin != bottomMargin)) {
                params.gravity = gravity; params.bottomMargin = bottomMargin; label.layoutParams = params
            }
        }
        commandsReveal?.visibility = if (hidden) View.VISIBLE else View.GONE
    }

    fun onConfigurationChanged(newConfig: Configuration) {
        screen?.releaseInput()
        commandsVisibility.restore()
        quickClickButtons.forEach { it.cancelPendingInputEvents(); it.isPressed = false }
        updateSessionChrome(); root.requestApplyInsets()
    }

    fun onPause() {
        resumed = false; presenceGeneration++; presenceState.cancelDeferred(); main.removeCallbacks(presenceTick)
        main.removeCallbacks(frameHealthTick); main.removeCallbacks(trafficTick); setPasswordVisible(false)
        commandsVisibility.restore(); resetTrafficRate(null)
        if (recovering) stopAll("Reconexão interrompida ao sair do aplicativo. Conecte novamente quando retornar.")
        main.removeCallbacks(renderComputersTask); computerRenderScheduled = false
        screen?.releaseInput(); updateControls()
    }
    fun onWindowFocusChanged(hasFocus: Boolean) {
        if (!hasFocus) { screen?.releaseInput(); commandsVisibility.restore(); updateSessionChrome() }
    }
    fun handleBack(): Boolean {
        if (commandsVisibility.hidden) { setCommandsHidden(false); return true }
        if (fullscreen) { setFullscreen(false); return true }
        if (toolsOverlay?.visibility == View.VISIBLE || filesOverlay?.visibility == View.VISIBLE) {
            toolsOverlay?.visibility = View.GONE; filesOverlay?.visibility = View.GONE
            screen?.releaseInput(); updateSessionChrome(); return true
        }
        return false
    }

    fun setSelected(value: Boolean) {
        if (selected == value) return
        if (!value) {
            commandsVisibility.restore()
            screen?.releaseInput(); resetModifiers(); setPasswordVisible(false)
            if (fullscreen) setFullscreen(false)
            screen?.clearFrame(); latestFrame = null; frameStale = false
            if (selected) getSystemService(InputMethodManager::class.java).hideSoftInputFromWindow(root.windowToken, 0)
        }
        selected = value
        if (value) {
            presenceGeneration++
            refreshComputers(true)
            frameWaitingSince = SystemClock.elapsedRealtime()
            requestStreamResume()
        }
        updateControls(); updateSessionChrome()
    }

    fun destroy() {
        alive = false; resumed = false
        main.removeCallbacks(presenceTick); main.removeCallbacks(frameHealthTick); main.removeCallbacks(trafficTick); main.removeCallbacks(renderComputersTask); presenceWorkers.shutdownNow()
        stopAll(); commands.close()
    }

    private fun ui(task: () -> Unit) {
        main.post { if (alive && !isFinishing && !isDestroyed) task() }
    }
    private fun showFieldError(input: EditText, message: String) {
        if (input === name) {
            disclosureViews["connection-settings"]?.let { (toggle, body) -> if (body.visibility != View.VISIBLE) toggle.performClick() }
        }
        kit.setFieldError(input, message); input.requestFocus(); showError(message)
    }

    private fun showError(message: String) { if (alive && !isFinishing) AlertDialog.Builder(this).setTitle("AliançaDX · $sessionTitle").setMessage(message.take(1000)).setPositiveButton("OK", null).show() }
    private fun dp(value: Int) = (value * resources.displayMetrics.density + .5f).toInt()
    private fun column(spacing: Int = 10) = kit.column(spacing)
    private fun row(spacing: Int = 10) = kit.row(spacing)
    private fun weighted() = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
    private fun label(value: String, size: Float, color: Int, bold: Boolean = false) = kit.label(value, size, color, bold)
    private fun edit(hintValue: String, value: String) = kit.edit(hintValue, value)
    private fun button(value: String, kind: UiKit.ButtonKind = UiKit.ButtonKind.PRIMARY, action: () -> Unit) = kit.button(value, kind, action)
    private companion object {
        const val REQUEST_SOURCE = 103
        const val REQUEST_DESTINATION = 104
    }
}

