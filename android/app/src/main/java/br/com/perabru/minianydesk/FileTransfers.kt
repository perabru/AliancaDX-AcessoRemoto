package br.com.perabru.minianydesk

import android.content.Context
import android.net.Uri
import android.provider.DocumentsContract
import android.provider.OpenableColumns
import android.util.Base64
import org.json.JSONObject
import java.io.File
import java.io.FileInputStream
import java.io.FileOutputStream
import java.io.InputStream
import java.io.OutputStream
import java.security.MessageDigest
import java.util.Locale
import java.util.UUID
import java.util.concurrent.ArrayBlockingQueue
import java.util.concurrent.RejectedExecutionException
import java.util.concurrent.ThreadPoolExecutor
import java.util.concurrent.TimeUnit
import java.util.concurrent.locks.ReentrantLock
import kotlin.concurrent.withLock

data class TransferStatus(
    val active: Boolean,
    val message: String,
    val progress: Float,
    val name: String = "",
    val transferred: Long = 0,
    val size: Long = 0,
    val path: String? = null,
    val success: Boolean? = null,
    val cleanupRequired: Boolean = false
)

/**
 * Streaming exchange compatible with Desktop 3.2.2 and transfer protocol 3.0.0.
 *
 * Only the local user's SAF selection grants access. askReceive MUST return a NEW document URI
 * from ACTION_CREATE_DOCUMENT, never an existing/open document: a failed transfer deletes this
 * newly created document. Incoming bytes stay in private cache until size and SHA-256 pass.
 * No storage permission, remote path, automatic opening, or URI from the network is accepted.
 */
class FileTransfers(
    context: Context,
    private val peer: Peer,
    private val dispatch: ((() -> Unit) -> Unit),
    private val askReceive: (offer: JSONObject, callback: (Uri?) -> Unit) -> Unit,
    private val onStatus: (TransferStatus) -> Unit,
    private val onRequestFile: ((callback: (Uri?) -> Unit) -> Unit)? = null,
    private val promptGuard: (Boolean) -> Unit = {}
) {
    private val context = context.applicationContext
    private val lock = ReentrantLock()
    private val changed = lock.newCondition()
    private var active: State? = null
    @Volatile private var closed = false
    private var requestPending: String? = null
    private var requestSent = 0L
    private var requestToken: String? = null
    private var requestChosenAt = 0L
    private val promptTokens = mutableSetOf<String>()
    // These queues never execute provider, transport, or UI callbacks under the state lock.
    private val outbound = executor("file-outbox", 32)
    private val incoming = executor("file-inbox", 8)
    private val guardLock = Any()
    private val cleaning = mutableSetOf<String>()
    private var statusScheduled = false
    private var lastStatusAt = 0L
    private var lastStatusOwner: String? = null
    private var lastStatus = TransferStatus(false, "Nenhuma transferência em andamento.", 0f)

    private class State(
        val sending: Boolean,
        val id: String,
        var name: String,
        var size: Long = 0,
        var sha256: String = "",
        val requestId: String? = null
    ) {
        var offset = 0L
        var updated = now()
        var cancelled = false
        var offerSent = false
        var accepted = false
        var expectedAck = -1L
        var ack = -1L
        var done = false
        var endSent = false
        var ready = false
        var committing = false
        var temp: File? = null
        var input: InputStream? = null
        var output: OutputStream? = null
        var destination: Uri? = null
        var destinationCommitted = false
        var destinationOpening = false
        var digest: MessageDigest? = null
    }

    init {
        if (!cleanPreviousProcessCache(this.context.cacheDir)) {
            status("Não foi possível remover um arquivo temporário anterior; limpe o cache do app nas configurações do Android.",
                cleanupRequired = true)
        }
        background("file-watchdog") {
            while (!closed) {
                try { Thread.sleep(1000) } catch (_: InterruptedException) { return@background }
                lock.withLock {
                    val state = active
                    if (state != null && now() - state.updated > TIMEOUT_MILLIS) {
                        abort(state, "Tempo de transferência esgotado.", true)
                    }
                    val pending = requestPending
                    if (pending != null && now() - requestSent > TIMEOUT_MILLIS) {
                        safeSend(message("file_request_cancel", pending))
                        requestPending = null
                        status("O outro usuário não escolheu um arquivo. Você pode tentar novamente.")
                    }
                    val choosing = requestToken
                    if (choosing != null && now() - requestChosenAt > TIMEOUT_MILLIS) {
                        safeSend(message("file_request_result", choosing).put("accepted", false))
                        requestToken = null
                        status("O tempo para escolher um arquivo terminou.")
                    }
                }
            }
        }
    }

    fun sendFile(uri: Uri): Boolean = startSend(uri, null)

    private fun startSend(uri: Uri, requestId: String?): Boolean {
        val state = lock.withLock {
            if (closed || busy()) {
                status("Aguarde a transferência ou seleção atual.")
                return false
            }
            if (uri.scheme != "content") {
                status("Escolha um arquivo pelo seletor de documentos do Android.")
                return false
            }
            // Provider metadata is queried off the main thread below.
            State(true, UUID.randomUUID().toString(), "", requestId = requestId).also { active = it }
        }
        status("Preparando arquivo e calculando SHA-256…", state)
        background("file-send") { sender(state, uri, requestId) }
        return true
    }

    fun receiveRequest(): Boolean = lock.withLock {
        if (closed || busy()) {
            status("Aguarde a transferência ou seleção atual.")
            return false
        }
        val id = UUID.randomUUID().toString()
        requestPending = id
        requestSent = now()
        try {
            send(message("file_request", id))
            status("O outro usuário precisa escolher e autorizar o arquivo.")
            true
        } catch (error: Exception) {
            requestPending = null
            status(errorMessage(error))
            false
        }
    }

    private fun sender(initial: State, uri: Uri, requestId: String?) {
        val state = initial
        try {
            val name = displayName(uri)
            require(validName(name)) { "Nome de arquivo incompatível com o Windows." }
            lock.withLock {
                requireLive(initial)
                state.name = name
            }
            val temporary = File.createTempFile("minianydesk-send-", ".part", context.cacheDir)
            val temporaryRegistered = lock.withLock { if (!isLive(state)) false else { state.temp = temporary; true } }
            if (!temporaryRegistered) { discardTemporary(temporary); return }
            val input = context.contentResolver.openInputStream(uri)
                ?: throw IllegalArgumentException("O documento selecionado não pode ser lido.")
            val inputRegistered = lock.withLock { if (!isLive(state)) false else { state.input = input; true } }
            if (!inputRegistered) { input.close(); return }
            val output = FileOutputStream(temporary)
            val outputRegistered = lock.withLock { if (!isLive(state)) false else { state.output = output; true } }
            if (!outputRegistered) {
                try { input.close() } catch (_: Exception) { }
                try { output.close() } catch (_: Exception) { }
                discardTemporary(temporary)
                return
            }
            val digest = MessageDigest.getInstance("SHA-256")
            val buffer = ByteArray(CHUNK)
            var count = 0L
            while (true) {
                lock.withLock { requireLive(state) }
                val amount = input.read(buffer)
                if (amount < 0) break
                if (amount == 0) continue
                count += amount
                require(count <= MAX_FILE) { "Escolha um arquivo de até 100 MiB." }
                output.write(buffer, 0, amount)
                digest.update(buffer, 0, amount)
                lock.withLock { requireLive(state); state.updated = now() }
            }
            output.flush()
            output.fd.sync()
            input.close()
            output.close()
            val spool = FileInputStream(temporary)
            val spoolRegistered = lock.withLock {
                if (!isLive(state)) false else {
                state.input = spool
                state.output = null
                state.size = count
                state.sha256 = hex(digest.digest())
                state.offerSent = true
                send(message("file_offer", state.id).put("name", state.name)
                    .put("size", count).put("sha256", state.sha256))
                true
                }
            }
            if (!spoolRegistered) { spool.close(); return }
            status("Aguardando aprovação e destino no outro dispositivo…", state)
            await(state, TIMEOUT_MILLIS) { state.accepted }
            while (true) {
                val offset = lock.withLock { requireLive(state); state.offset }
                if (offset >= count) break
                val amount = minOf(CHUNK.toLong(), count - offset).toInt()
                val bytes = ByteArray(amount)
                var read = 0
                while (read < amount) {
                    val result = spool.read(bytes, read, amount - read)
                    require(result > 0) { "O arquivo temporário ficou incompleto." }
                    read += result
                }
                val expected = offset + amount
                lock.withLock {
                    requireLive(state)
                    state.expectedAck = expected
                    send(message("file_chunk", state.id).put("offset", offset)
                        .put("data", Base64.encodeToString(bytes, Base64.NO_WRAP)))
                }
                await(state, BLOCK_TIMEOUT_MILLIS) { state.ack == expected }
                lock.withLock { requireLive(state); state.offset = expected; state.updated = now() }
                status("Enviando arquivo…", state)
            }
            lock.withLock {
                requireLive(state)
                state.endSent = true
                send(message("file_end", state.id))
            }
            await(state, BLOCK_TIMEOUT_MILLIS) { state.done }
            finish(state, "Arquivo enviado e conferido no destino.", true)
        } catch (error: Exception) {
            abort(state, errorMessage(error), true)
        }
    }

    fun handle(incoming: JSONObject): Boolean {
        val type = incoming.opt("type") as? String ?: return false
        if (type !in TYPES) return false
        if (closed) return true
        val id = incoming.opt("id") as? String
        if (!validId(id)) return true
        val identifier = id!!
        when (type) {
            "file_request" -> remoteRequest(identifier)
            "file_request_result" -> lock.withLock {
                if (requestPending == identifier && incoming.opt("accepted") == false) {
                    requestPending = null
                    status("O outro usuário recusou o pedido ou não selecionou um arquivo.")
                }
            }
            "file_request_cancel" -> lock.withLock {
                var matched = false
                if (requestToken == identifier) { requestToken = null; matched = true }
                active?.let {
                    if (it.sending && it.requestId == identifier) {
                        matched = true
                        abort(it, "Pedido de arquivo cancelado pelo outro usuário.", true)
                    }
                }
                if (matched) status("Pedido de arquivo cancelado pelo outro usuário.")
            }
            "file_offer" -> offer(incoming, identifier)
            else -> lock.withLock {
                val state = active
                if (state == null || state.id != identifier) return@withLock
                state.updated = now()
                try {
                    when {
                        type == "file_cancel" -> abort(state, "Transferência recusada ou cancelada pelo outro usuário.")
                        type == "file_accept" && state.sending && state.offerSent && !state.accepted -> {
                            state.accepted = true
                            changed.signalAll()
                        }
                        type == "file_ack" && state.sending -> {
                            val offset = integer(incoming, "offset")
                            require(state.accepted && state.expectedAck > state.offset && offset != null && offset == state.expectedAck) {
                                "Confirmação de bloco inválida."
                            }
                            state.ack = offset
                            changed.signalAll()
                        }
                        type == "file_done" && state.sending -> {
                            require(state.endSent && state.offset == state.size) { "Confirmação prematura." }
                            state.done = true
                            changed.signalAll()
                        }
                        (type == "file_chunk" || type == "file_end") && !state.sending -> {
                            // handle() is also called by the Activity's UI dispatcher.
                            try {
                                this.incoming.execute {
                                    try {
                                        if (type == "file_chunk") chunk(state, incoming) else received(state)
                                    } catch (error: Exception) { abort(state, errorMessage(error), true) }
                                }
                            } catch (_: RejectedExecutionException) {
                                abort(state, "Blocos recebidos mais rápido do que podem ser gravados.", true)
                            }
                        }
                        else -> throw IllegalArgumentException("Etapa de transferência inválida.")
                    }
                } catch (error: Exception) { abort(state, errorMessage(error), true) }
            }
        }
        return true
    }

    private fun offer(offer: JSONObject, id: String) {
        val name = offer.opt("name") as? String
        val size = integer(offer, "size")
        val digest = offer.opt("sha256") as? String
        if (name == null || !validName(name) || size == null || size !in 0..MAX_FILE ||
            digest == null || !DIGEST.matches(digest)) {
            safeSend(message("file_cancel", id))
            lock.withLock { if (!busy()) status("Oferta de arquivo inválida ou maior que 100 MiB.") }
            return
        }
        val state = lock.withLock {
            if (closed || active != null || requestToken != null || promptTokens.isNotEmpty()) {
                safeSend(message("file_cancel", id))
                return
            }
            requestPending = null
            State(false, id, name, size, digest).also { active = it }
        }
        status("Escolha se deseja receber este arquivo.", state)
        background("file-approval") {
            prompt(id, { callback -> askReceive(JSONObject(offer.toString()), callback) },
                { uri -> receiveDestination(state, uri) }, { isLive(state) })
        }
    }

    private fun receiveDestination(state: State, uri: Uri?) {
        if (uri == null) { abort(state, "Recebimento recusado.", true); return }
        background("file-destination") {
            var temporary: File? = null
            var output: FileOutputStream? = null
            try {
                require(uri.scheme == "content" && DocumentsContract.isDocumentUri(context, uri)) {
                    "Escolha um novo documento pelo seletor de arquivos do Android."
                }
                val live = lock.withLock {
                    if (!isLive(state)) false else { state.destination = uri; true }
                }
                if (!live) {
                    if (!deleteNewDocument(uri)) status("O provedor não permitiu excluir o documento criado. Exclua-o pelo seletor de arquivos.", cleanupRequired = true)
                    return@background
                }
                temporary = File.createTempFile("minianydesk-receive-", ".part", context.cacheDir)
                output = FileOutputStream(temporary)
                val digest = MessageDigest.getInstance("SHA-256")
                val registered = lock.withLock {
                    if (!isLive(state)) false else {
                        state.temp = temporary
                        state.output = output
                        state.digest = digest
                        state.ready = true
                        state.updated = now()
                        send(message("file_accept", state.id))
                        true
                    }
                }
                if (!registered) {
                    output.close(); discardTemporary(temporary)
                    return@background
                }
                temporary = null; output = null // State now owns both resources.
                status("Recebendo arquivo…", state)
            } catch (error: Exception) {
                try { output?.close() } catch (_: Exception) { }
                temporary?.let { discardTemporary(it) }
                abort(state, errorMessage(error), true)
            }
        }
    }

    private fun chunk(state: State, incoming: JSONObject) {
        val offset = integer(incoming, "offset")
        val encoded = incoming.opt("data") as? String
        val output = lock.withLock {
            requireLive(state)
            require(state.ready && !state.committing && offset == state.offset) { "Bloco fora de ordem ou inválido." }
            state.output ?: throw IllegalArgumentException("Destino temporário indisponível.")
        }
        require(encoded != null && encoded.length <= ((CHUNK + 2) / 3) * 4 && BASE64.matches(encoded)) {
            "Bloco fora de ordem ou inválido."
        }
        val bytes = Base64.decode(encoded, Base64.NO_WRAP)
        require(bytes.isNotEmpty() && bytes.size <= CHUNK && state.offset + bytes.size <= state.size &&
            Base64.encodeToString(bytes, Base64.NO_WRAP) == encoded) { "Bloco corrompido ou tamanho inválido." }
        output.write(bytes)
        // Digest and receive offsets have one serial inbox worker. Cancellation may only detach them.
        state.digest!!.update(bytes)
        lock.withLock {
            requireLive(state)
            state.offset += bytes.size
            state.updated = now()
            send(message("file_ack", state.id).put("offset", state.offset))
        }
        status("Recebendo arquivo…", state)
    }

    private fun received(state: State) {
        val output = lock.withLock {
            requireLive(state)
            require(state.ready && !state.committing && state.offset == state.size) {
                "Arquivo incompleto. O recebimento foi descartado."
            }
            state.output as FileOutputStream
        }
        require(hex(state.digest!!.digest()) == state.sha256) {
            "SHA-256 diferente. O recebimento foi descartado."
        }
        output.flush()
        output.fd.sync()
        output.close()
        lock.withLock {
            requireLive(state)
            state.output = null
            state.committing = true
        }
        status("Arquivo conferido. Salvando no destino escolhido…", state)
        background("file-save") {
            try {
                val destination = state.destination ?: throw IllegalArgumentException("Destino não disponível.")
                lock.withLock { requireLive(state) }
                val temporary = lock.withLock { requireLive(state); state.temp!! }
                val input = FileInputStream(temporary)
                val inputRegistered = lock.withLock { if (!isLive(state)) false else { state.input = input; true } }
                if (!inputRegistered) { input.close(); return@background }
                val target = openDestination(state, destination)
                val buffer = ByteArray(CHUNK)
                var count = 0L
                while (count < state.size) {
                    lock.withLock { requireLive(state) }
                    val amount = input.read(buffer, 0, minOf(buffer.size.toLong(), state.size - count).toInt())
                    require(amount > 0) { "O arquivo conferido ficou incompleto." }
                    lock.withLock { requireLive(state) }
                    target.write(buffer, 0, amount)
                    lock.withLock {
                        requireLive(state)
                        state.updated = now()
                    }
                    count += amount
                }
                target.flush()
                // A SAF destination may be a provider pipe, even when its stream extends
                // FileOutputStream. Only our verified private file has a regular, fsync-able FD.
                target.close()
                input.close()
                lock.withLock {
                    requireLive(state)
                    state.input = null
                    state.output = null
                    state.destinationCommitted = true
                    send(message("file_done", state.id)) {
                        finish(state, "Arquivo recebido e conferido: ${state.name}", true, destination.toString())
                    }
                }
            } catch (error: Exception) { abort(state, errorMessage(error), true) }
        }
    }

    private fun openDestination(state: State, destination: Uri): OutputStream {
        lock.withLock { requireLive(state); state.destinationOpening = true }
        var target: OutputStream? = null
        var registered = false
        try {
            target = context.contentResolver.openOutputStream(destination, "w")
                ?: throw IllegalArgumentException("Não foi possível salvar no documento escolhido.")
            registered = lock.withLock { if (!isLive(state)) false else { state.output = target; true } }
            check(registered) { "Transferência cancelada." }
            return target
        } finally {
            if (!registered) try { target?.close() } catch (_: Exception) { }
            lock.withLock { state.destinationOpening = false; changed.signalAll() }
        }
    }

    private fun remoteRequest(token: String) {
        lock.withLock {
            if (closed || busy() || onRequestFile == null) {
                safeSend(message("file_request_result", token).put("accepted", false))
                return
            }
            requestToken = token
            requestChosenAt = now()
        }
        status("O outro usuário pediu um arquivo. Escolha se deseja enviar.")
        background("file-approval") { prompt(token, onRequestFile!!, { uri ->
            val valid = lock.withLock {
                if (requestToken != token || closed) false else { requestToken = null; true }
            }
            if (valid) {
                if (uri == null || !startSend(uri, token)) {
                    safeSend(message("file_request_result", token).put("accepted", false))
                    status("O usuário não selecionou um arquivo para envio.")
                }
            }
        }, { requestToken == token && !closed }) }
    }

    private fun prompt(
        token: String,
        ask: ((Uri?) -> Unit) -> Unit,
        result: (Uri?) -> Unit,
        stillValid: () -> Boolean
    ) {
        // Serialize guard transitions separately: callbacks may send state and wait for transport.
        // A cancelled picker retains its guard until the real picker callback returns.
        var guardFailed = false
        synchronized(guardLock) {
            val valid = lock.withLock {
                if (closed || !stillValid()) false else promptTokens.add(token)
            }
            if (!valid) return
            try { promptGuard(true) } catch (_: Exception) { guardFailed = true }
        }
        val complete: (Uri?) -> Unit = { uri ->
            val accepted = synchronized(guardLock) {
                val removed = lock.withLock { promptTokens.remove(token) }
                if (removed) try { promptGuard(false) } catch (_: Exception) { }
                removed
            }
            if (accepted) {
                // A stale receive callback still owns a freshly created SAF document and removes it.
                result(uri)
                lock.withLock {
                    val busy = !closed && busy()
                    if (lastStatus.active != busy) emit(lastStatus.copy(active = busy))
                }
            }
        }
        if (guardFailed) { complete(null); return }
        try {
            dispatch {
                val valid = lock.withLock { !closed && token in promptTokens && stillValid() }
                if (!valid) complete(null)
                else try { ask(complete) } catch (_: Exception) { complete(null) }
            }
        } catch (_: Exception) { complete(null) }
    }

    fun cancel() {
        lock.withLock {
            requestPending?.let { safeSend(message("file_request_cancel", it)) }
            requestToken?.let { safeSend(message("file_request_result", it).put("accepted", false)) }
            requestPending = null
            requestToken = null
            val state = active
            if (state != null) abort(state, "Transferência cancelada.", true)
            else status("Pedido de arquivo cancelado.")
        }
    }

    fun close() {
        lock.withLock {
            if (closed) return
            closed = true
            requestPending = null
            requestToken = null
            active?.let { abort(it, "Conexão encerrada; transferência descartada.") }
            changed.signalAll()
        }
        outbound.shutdownNow()
        incoming.shutdownNow()
    }

    private fun await(state: State, timeout: Long, predicate: () -> Boolean) {
        val deadline = now() + timeout
        lock.withLock {
            while (true) {
                requireLive(state)
                if (predicate()) return
                val remaining = deadline - now()
                check(remaining > 0) { "O outro dispositivo não respondeu à transferência." }
                changed.awaitNanos(remaining * 1_000_000)
            }
        }
    }

    private fun abort(state: State, text: String, notify: Boolean = false) {
        lock.withLock {
            if (active !== state) return
            if (notify && !closed) {
                safeSend(message("file_cancel", state.id))
                state.requestId?.let {
                    if (!state.offerSent) safeSend(message("file_request_result", it).put("accepted", false))
                }
            }
            finish(state, text, false)
        }
    }

    private fun finish(state: State, text: String, success: Boolean, path: String? = null) {
        val resources = lock.withLock {
            if (active !== state) return
            active = null
            state.cancelled = true
            changed.signalAll()
            val resources = Triple(state.input, state.output, state.temp)
            state.input = null
            state.output = null
            state.temp = null
            cleaning.add(state.id)
            emit(TransferStatus(!closed && busy(), "Finalizando transferência…", if (success) 1f else 0f,
                state.name, state.offset, state.size))
            resources
        }
        background("file-cleanup") {
            try { resources.first?.close() } catch (_: Exception) { }
            try { resources.second?.close() } catch (_: Exception) { }
            val cacheRemoved = resources.third?.let { removeTemporary(it) } != false
            // A provider may return after cancellation. Delete once, after its pending open closes,
            // so a late "w" open cannot recreate a document which cleanup already removed.
            lock.withLock { while (state.destinationOpening) changed.awaitUninterruptibly() }
            val removed = state.destinationCommitted || state.destination?.let { deleteNewDocument(it) } != false
            val destinationText = if (removed) text else
                "$text O provedor não permitiu excluir o documento criado; exclua-o pelo seletor de arquivos."
            val finalText = if (cacheRemoved) destinationText else
                "$destinationText Não foi possível remover o arquivo temporário; limpe o cache do app nas configurações do Android."
            lock.withLock {
                cleaning.remove(state.id)
                emit(TransferStatus(!closed && busy(), finalText, if (success) 1f else 0f,
                    state.name, state.offset, state.size, path, success, cleanupRequired = !removed || !cacheRemoved))
            }
        }
    }

    private fun status(text: String, state: State? = null, cleanupRequired: Boolean = false) {
        lock.withLock {
            if (state != null && !isLive(state)) return
            val current = state ?: active
            val event = TransferStatus(!closed && busy(), text,
                if (current != null && current.size > 0) current.offset.toFloat() / current.size else 0f,
                current?.name ?: "", current?.offset ?: 0, current?.size ?: 0, cleanupRequired = cleanupRequired)
            val owner = current?.id
            val timestamp = now()
            if (!cleanupRequired && event.active && event.message == lastStatus.message && event.active == lastStatus.active &&
                owner == lastStatusOwner && timestamp - lastStatusAt < STATUS_INTERVAL_MILLIS) return
            lastStatusAt = timestamp
            lastStatusOwner = owner
            emit(event)
        }
    }

    private fun emit(event: TransferStatus) {
        // Cleanup warnings must survive a newer transfer/progress event and must not repeat when
        // a still-open picker later changes only the busy flag.
        lastStatus = if (event.cleanupRequired) event.copy(cleanupRequired = false) else event
        if (event.cleanupRequired) {
            try {
                STATUS_EXECUTOR.execute {
                    try { dispatch { try { onStatus(event) } catch (_: Exception) { } } } catch (_: Exception) { }
                }
            } catch (_: RejectedExecutionException) { }
            return
        }
        if (statusScheduled) return
        statusScheduled = true
        try {
            STATUS_EXECUTOR.execute {
                try {
                    dispatch {
                        val latest = lock.withLock { statusScheduled = false; lastStatus }
                        try { onStatus(latest) } catch (_: Exception) { }
                    }
                } catch (_: Exception) { lock.withLock { statusScheduled = false } }
            }
        } catch (_: RejectedExecutionException) { statusScheduled = false }
    }

    private fun displayName(uri: Uri): String {
        context.contentResolver.query(uri, arrayOf(OpenableColumns.DISPLAY_NAME, OpenableColumns.SIZE), null, null, null)?.use { cursor ->
            if (cursor.moveToFirst()) {
                val sizeIndex = cursor.getColumnIndex(OpenableColumns.SIZE)
                if (sizeIndex >= 0 && !cursor.isNull(sizeIndex))
                    require(cursor.getLong(sizeIndex) <= MAX_FILE) { "Escolha um arquivo de até 100 MiB." }
                val index = cursor.getColumnIndex(OpenableColumns.DISPLAY_NAME)
                if (index >= 0 && !cursor.isNull(index)) return cursor.getString(index)
            }
        }
        return "arquivo.bin"
    }

    private fun deleteNewDocument(uri: Uri): Boolean {
        // Ownership comes ONLY from askReceive's ACTION_CREATE_DOCUMENT contract, never the peer.
        return try {
            if (uri.scheme == "content" && DocumentsContract.isDocumentUri(context, uri))
                DocumentsContract.deleteDocument(context.contentResolver, uri)
            else false
        } catch (_: Exception) { false }
    }

    private fun removeTemporary(file: File): Boolean = try {
        !file.exists() || file.delete() || !file.exists()
    } catch (_: Exception) { false }

    private fun discardTemporary(file: File) {
        if (!removeTemporary(file)) status(
            "Não foi possível remover o arquivo temporário; limpe o cache do app nas configurações do Android.",
            cleanupRequired = true)
    }

    private fun busy() = active != null || requestPending != null || requestToken != null || promptTokens.isNotEmpty() || cleaning.isNotEmpty()
    private fun isLive(state: State) = !closed && !state.cancelled && active === state
    private fun requireLive(state: State) { check(isLive(state)) { "Transferência cancelada." } }
    private fun send(message: JSONObject, onSent: (() -> Unit)? = null) {
        check(!closed) { "Conexão encerrada." }
        val id = message.optString("id")
        val owner = lock.withLock { active?.takeIf { it.id == id } }
        val request = if (message.optString("type") == "file_request") id else null
        outbound.execute {
            val valid = lock.withLock { !closed && (owner == null || isLive(owner)) && (request == null || requestPending == request) }
            if (!valid) return@execute
            try {
                peer.send(message)
                onSent?.invoke()
            } catch (error: Exception) {
                if (owner != null) abort(owner, errorMessage(error), true)
                else lock.withLock {
                    if (requestPending == request && request != null) {
                        requestPending = null
                        status(errorMessage(error))
                    }
                }
            }
        }
    }

    private fun safeSend(message: JSONObject) {
        try {
            if (!closed) outbound.execute {
                try { if (!closed) peer.send(message) } catch (_: Exception) { }
            }
        } catch (_: RejectedExecutionException) { }
    }

    companion object {
        const val MAX_FILE = 100L * 1024 * 1024
        const val CHUNK = 64 * 1024
        private const val TIMEOUT_MILLIS = 90_000L
        private const val BLOCK_TIMEOUT_MILLIS = 30_000L
        private const val STATUS_INTERVAL_MILLIS = 100L
        private val STATUS_EXECUTOR = executor("file-status", 128)
        private val CACHE_CLEANUP_LOCK = Any()
        private var previousProcessCacheChecked = false
        private val TYPES = setOf("file_offer", "file_accept", "file_chunk", "file_ack", "file_end", "file_done",
            "file_cancel", "file_request", "file_request_result", "file_request_cancel")
        private val DIGEST = Regex("[a-f0-9]{64}")
        private val BASE64 = Regex("[A-Za-z0-9+/]*={0,2}")
        private val RESERVED = setOf("CON", "PRN", "AUX", "NUL") +
            (1..9).flatMap { listOf("COM$it", "LPT$it") }

        /** Serialize the first constructors before either can create an active spool. */
        private fun cleanPreviousProcessCache(directory: File): Boolean = synchronized(CACHE_CLEANUP_LOCK) {
            if (previousProcessCacheChecked) return@synchronized true
            previousProcessCacheChecked = true
            try {
                val entries = directory.listFiles() ?: return@synchronized false
                var removed = true
                for (entry in entries) {
                    val owned = entry.name.endsWith(".part") &&
                        (entry.name.startsWith("minianydesk-send-") || entry.name.startsWith("minianydesk-receive-"))
                    if (owned && entry.isFile && entry.exists() && !entry.delete() && entry.exists()) removed = false
                }
                removed
            } catch (_: Exception) { false }
        }

        internal fun validName(name: String): Boolean = name.length in 1..200 && name != "." && name != ".." &&
            name.toByteArray(Charsets.UTF_8).toString(Charsets.UTF_8) == name &&
            !name.endsWith(" ") && !name.endsWith(".") && name.none { it < ' ' || it in "\\/:*?\"<>|" } &&
            name.substringBefore('.').uppercase(Locale.ROOT) !in RESERVED

        private fun validId(id: String?): Boolean = try { id != null && UUID.fromString(id).toString() == id } catch (_: Exception) { false }
        private fun integer(json: JSONObject, key: String): Long? = when (val value = json.opt(key)) {
            is Int -> value.toLong()
            is Long -> value
            else -> null
        }
        private fun message(type: String, id: String) = JSONObject().put("type", type).put("id", id)
        private fun hex(bytes: ByteArray) = bytes.joinToString("") { "%02x".format(it.toInt() and 255) }
        private fun now() = System.nanoTime() / 1_000_000
        private fun errorMessage(error: Exception) = error.message ?: "Não foi possível concluir a transferência."
        private fun background(name: String, task: () -> Unit) = Thread(task, "MiniAnyDesk-$name").apply { isDaemon = true; start() }
        private fun executor(name: String, capacity: Int) = ThreadPoolExecutor(1, 1, 30, TimeUnit.SECONDS,
            ArrayBlockingQueue<Runnable>(capacity), { task ->
                Thread(task, "MiniAnyDesk-$name").apply { isDaemon = true }
            }, ThreadPoolExecutor.AbortPolicy()).apply { allowCoreThreadTimeOut(true) }
    }
}
