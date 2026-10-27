package br.com.perabru.minianydesk

import java.io.ByteArrayOutputStream
import java.io.IOException
import java.net.SocketTimeoutException
import java.net.UnknownHostException
import java.net.URL
import java.nio.ByteBuffer
import java.nio.charset.CodingErrorAction
import java.nio.charset.StandardCharsets
import javax.net.ssl.HttpsURLConnection
import javax.net.ssl.SSLException

internal interface AndroidReleaseSource {
    @Throws(IOException::class)
    fun fetch(installedVersion: String): AndroidReleaseUpdate.Result
    fun cancel()
}

internal class AndroidUpdateFailure(message: String, val retryAfterMillis: Long = 0) : IOException(message)

/** Anonymous, manual lookup of one fixed public endpoint. No session values enter this request. */
internal class AndroidReleaseFetcher(
    private val connectionFactory: () -> HttpsURLConnection = {
        URL(AndroidReleaseUpdate.API_URL).openConnection() as HttpsURLConnection
    }
) : AndroidReleaseSource {
    @Volatile private var active: HttpsURLConnection? = null
    @Volatile private var cancelled = false

    override fun fetch(installedVersion: String): AndroidReleaseUpdate.Result {
        var connection: HttpsURLConnection? = null
        try {
            if (cancelled || Thread.currentThread().isInterrupted) throw IOException("Update check cancelled")
            val deadline = System.nanoTime() + 20_000_000_000L
            connection = connectionFactory()
            require(connection.url.toExternalForm() == AndroidReleaseUpdate.API_URL) { "Unexpected update endpoint" }
            active = connection
            if (cancelled || Thread.currentThread().isInterrupted) throw IOException("Update check cancelled")
            connection.instanceFollowRedirects = false
            connection.connectTimeout = 6_000
            connection.readTimeout = 4_000
            connection.useCaches = false
            connection.requestMethod = "GET"
            connection.setRequestProperty("Accept", "application/vnd.github+json")
            connection.setRequestProperty("Accept-Encoding", "identity")
            connection.setRequestProperty("User-Agent", "AliancaDX-Android-Update/1")
            when (val status = connection.responseCode) {
                200 -> Unit
                403, 429 -> {
                    val wait = rateLimitDelay(connection)
                    throw AndroidUpdateFailure("O GitHub limitou as consultas. Aguarde ${maxOf(1, (wait + 59_999) / 60_000)} minuto(s) e tente novamente.", wait)
                }
                404 -> throw AndroidUpdateFailure("Ainda não encontramos uma versão pública. Tente novamente mais tarde.")
                in 300..399 -> throw AndroidUpdateFailure("O endereço da atualização mudou. Tente novamente mais tarde.")
                in 500..599 -> throw AndroidUpdateFailure("O GitHub está temporariamente indisponível. Tente novamente em alguns minutos.")
                else -> throw AndroidUpdateFailure("Não foi possível consultar o GitHub (código $status). Tente novamente.")
            }
            val length = connection.contentLengthLong
            if (length > AndroidReleaseUpdate.MAX_RESPONSE_BYTES) throw AndroidUpdateFailure("A resposta da atualização é maior que o esperado. Tente novamente mais tarde.")
            val output = ByteArrayOutputStream(minOf(if (length > 0) length.toInt() else 8192, AndroidReleaseUpdate.MAX_RESPONSE_BYTES))
            connection.inputStream.use { input ->
                val buffer = ByteArray(8192)
                while (true) {
                    if (Thread.currentThread().isInterrupted) throw IOException("Update check cancelled")
                    if (System.nanoTime() > deadline) throw SocketTimeoutException()
                    val count = input.read(buffer)
                    if (count < 0) break
                    if (count == 0) continue
                    if (output.size() + count > AndroidReleaseUpdate.MAX_RESPONSE_BYTES)
                        throw AndroidUpdateFailure("A resposta da atualização é maior que o esperado. Tente novamente mais tarde.")
                    output.write(buffer, 0, count)
                }
            }
            val body = StandardCharsets.UTF_8.newDecoder().onMalformedInput(CodingErrorAction.REPORT)
                .onUnmappableCharacter(CodingErrorAction.REPORT).decode(ByteBuffer.wrap(output.toByteArray())).toString()
            return try { AndroidReleaseUpdate.parse(body, installedVersion) }
                catch (_: Exception) { throw AndroidUpdateFailure("A versão publicada ainda não tem um aplicativo Android válido. Tente novamente mais tarde.") }
        } catch (error: AndroidUpdateFailure) { throw error }
        catch (_: SocketTimeoutException) { throw AndroidUpdateFailure("A consulta demorou demais. Confira a internet e tente novamente.") }
        catch (_: UnknownHostException) { throw AndroidUpdateFailure("Não foi possível encontrar o GitHub. Confira sua conexão com a internet e tente novamente.") }
        catch (_: SSLException) { throw AndroidUpdateFailure("Não foi possível estabelecer uma conexão segura com o GitHub. Confira a data e a internet do celular.") }
        catch (_: Exception) { throw AndroidUpdateFailure("Não foi possível buscar a atualização. Confira a internet e tente novamente.") }
        finally { active = null; connection?.disconnect() }
    }

    override fun cancel() { cancelled = true; active?.disconnect() }

    private fun rateLimitDelay(connection: HttpsURLConnection): Long {
        val retrySeconds = connection.getHeaderField("Retry-After")?.trim()?.toLongOrNull()
        val resetSeconds = connection.getHeaderField("X-RateLimit-Reset")?.trim()?.toLongOrNull()
        val seconds = when {
            retrySeconds != null -> retrySeconds.coerceIn(1, 3600)
            resetSeconds != null -> {
                val currentSeconds = System.currentTimeMillis() / 1000
                if (resetSeconds <= currentSeconds) 1 else (resetSeconds - currentSeconds).coerceAtMost(3600)
            }
            else -> 60
        }
        return seconds * 1000
    }
}
