package br.com.perabru.minianydesk

/** A document picker belongs to its launching session, never to the selected tab. */
internal class SessionDocumentRequests<T> {
    data class Request<T>(val owner: T, val originalCode: Int)
    private var nextCode = 1000
    private val pending = LinkedHashMap<Int, Request<T>>()
    fun reserve(owner: T, originalCode: Int): Int {
        check(pending.isEmpty()) { "Conclua o seletor de arquivos aberto antes de iniciar outro." }
        val code = nextCode++
        if (nextCode >= 65000) nextCode = 1000
        pending[code] = Request(owner, originalCode)
        return code
    }
    fun complete(code: Int): Request<T>? = pending.remove(code)
    fun cancel(owner: T) { pending.entries.removeAll { it.value.owner === owner } }
    fun clear() = pending.clear()
}
