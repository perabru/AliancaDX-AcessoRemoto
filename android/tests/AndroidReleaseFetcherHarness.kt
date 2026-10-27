package br.com.perabru.minianydesk

import java.io.ByteArrayInputStream
import java.io.IOException
import java.io.InputStream
import java.net.SocketTimeoutException
import java.net.URL
import java.net.UnknownHostException
import java.security.cert.Certificate
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicReference
import javax.net.ssl.HttpsURLConnection
import javax.net.ssl.SSLException
import org.json.JSONArray
import org.json.JSONObject

/** Exact production fetcher with controlled HTTPS transport; no live network or Android device. */
object AndroidReleaseFetcherHarness {
    private fun release(): ByteArray = JSONObject().put("draft", false).put("prerelease", false)
        .put("published_at", "2026-10-27T12:34:56Z").put("tag_name", "v7.5.0")
        .put("assets", JSONArray().put(JSONObject().put("name", "AliancaDX_Acesso_Remoto_7.5.0.apk").put("state", "uploaded").put("size", 10485760)
            .put("browser_download_url", "https://github.com/perabru/AliancaDX-AcessoRemoto/releases/download/v7.5.0/AliancaDX_Acesso_Remoto_7.5.0.apk")))
        .toString().toByteArray(Charsets.UTF_8)

    private class TrackedInput(bytes: ByteArray) : ByteArrayInputStream(bytes) {
        var readBytes = 0
        var closes = 0
        override fun read(buffer: ByteArray, offset: Int, length: Int): Int = super.read(buffer, offset, length).also {
            if (it > 0) readBytes += it
        }
        override fun close() { closes++; super.close() }
    }

    private class FakeConnection(endpoint: String = AndroidReleaseUpdate.API_URL) : HttpsURLConnection(URL(endpoint)) {
        var status = 200
        var statusError: IOException? = null
        var length = -1L
        var stream: InputStream = TrackedInput(release())
        var disconnects = 0
        var inputRequests = 0
        val headers = LinkedHashMap<String, String>()
        val requestHeaders = LinkedHashMap<String, String>()
        var disconnectAction: (() -> Unit)? = null
        override fun connect() { connected = true }
        override fun disconnect() { disconnects++; disconnectAction?.invoke() }
        override fun usingProxy() = false
        override fun getCipherSuite() = "TLS_AES_128_GCM_SHA256"
        override fun getLocalCertificates(): Array<Certificate>? = null
        override fun getServerCertificates(): Array<Certificate> = emptyArray()
        override fun getResponseCode(): Int { statusError?.let { throw it }; return status }
        override fun getContentLengthLong() = length
        override fun getInputStream(): InputStream { inputRequests++; return stream }
        override fun getHeaderField(name: String?) = headers[name]
        override fun setRequestProperty(key: String, value: String) { requestHeaders[key] = value }
    }

    private fun failed(fetcher: AndroidReleaseFetcher): AndroidUpdateFailure {
        try { fetcher.fetch("7.4.0"); error("Expected update failure") }
        catch (failure: AndroidUpdateFailure) { return failure }
    }

    @JvmStatic fun main(args: Array<String>) {
        var count = 0
        fun test(name: String, body: () -> Unit) { body(); count++; println("PASS $name") }
        test("valid response uses one exact anonymous GET with fixed headers and bounded timeouts") {
            val connection = FakeConnection(); var factories = 0
            val result = AndroidReleaseFetcher { factories++; connection }.fetch("7.4.0")
            check(result.relation == AndroidReleaseUpdate.Relation.AVAILABLE && result.version == "7.5.0")
            check(factories == 1 && connection.url.toString() == AndroidReleaseUpdate.API_URL)
            check(connection.url.query == null && connection.url.userInfo == null)
            check(connection.requestMethod == "GET" && !connection.instanceFollowRedirects && !connection.useCaches)
            check(connection.connectTimeout in 1..6000 && connection.readTimeout in 1..4000)
            check(connection.requestHeaders == mapOf("Accept" to "application/vnd.github+json", "Accept-Encoding" to "identity", "User-Agent" to "AliancaDX-Android-Update/1"))
            check(connection.inputRequests == 1 && connection.disconnects == 1)
            check((connection.stream as TrackedInput).closes == 1)
        }
        test("wrong endpoint, query, userinfo and HTTP are rejected before the transport is read") {
            for (endpoint in listOf(AndroidReleaseUpdate.API_URL + "?computer=secret", AndroidReleaseUpdate.API_URL.replace("https:", "http:"), AndroidReleaseUpdate.API_URL.replace("api.github.com", "api.github.com.evil.test"), AndroidReleaseUpdate.API_URL.replace("api.github.com", "user@api.github.com"))) {
                val connection = FakeConnection(endpoint); val error = failed(AndroidReleaseFetcher { connection })
                check(error.retryAfterMillis == 0L && connection.inputRequests == 0 && connection.disconnects == 1)
            }
        }
        test("content-length exceeding 512KiB is rejected before opening a body") {
            val connection = FakeConnection(); connection.length = AndroidReleaseUpdate.MAX_RESPONSE_BYTES.toLong() + 1
            val error = failed(AndroidReleaseFetcher { connection })
            check(error.message!!.contains("maior") && connection.inputRequests == 0 && connection.disconnects == 1)
        }
        test("chunked or underreported oversized body stops at the byte limit and closes its stream") {
            for (length in listOf(-1L, 4L)) {
                val connection = FakeConnection(); connection.length = length
                val input = TrackedInput(ByteArray(AndroidReleaseUpdate.MAX_RESPONSE_BYTES + 32768) { ' '.code.toByte() }); connection.stream = input
                check(failed(AndroidReleaseFetcher { connection }).message!!.contains("maior"))
                check(input.readBytes in AndroidReleaseUpdate.MAX_RESPONSE_BYTES + 1..AndroidReleaseUpdate.MAX_RESPONSE_BYTES + 8192)
                check(input.closes == 1 && connection.disconnects == 1)
            }
        }
        test("a valid JSON body exactly at 512KiB remains accepted") {
            val body = release()
            val bytes = body + ByteArray(AndroidReleaseUpdate.MAX_RESPONSE_BYTES - body.size) { ' '.code.toByte() }
            val connection = FakeConnection(); connection.length = bytes.size.toLong(); connection.stream = TrackedInput(bytes)
            check(AndroidReleaseFetcher { connection }.fetch("7.4.0").relation == AndroidReleaseUpdate.Relation.AVAILABLE)
            check(connection.disconnects == 1 && (connection.stream as TrackedInput).closes == 1)
        }
        test("malformed JSON, unsafe APK URL and invalid UTF8 cannot yield a downloadable result") {
            val unsafe = JSONObject(String(release(), Charsets.UTF_8)).apply {
                getJSONArray("assets").getJSONObject(0).put("browser_download_url", "https://evil.test/app.apk")
            }.toString().toByteArray(Charsets.UTF_8)
            for (bytes in listOf("{".toByteArray(), unsafe, byteArrayOf(0xc3.toByte(), 0x28))) {
                val connection = FakeConnection(); connection.stream = TrackedInput(bytes)
                check(failed(AndroidReleaseFetcher { connection }).retryAfterMillis == 0L)
                check(connection.disconnects == 1 && (connection.stream as TrackedInput).closes == 1)
            }
        }
        test("403 and 429 honor bounded Retry-After without trying a download or reading an error body") {
            for (status in listOf(403, 429)) for ((header, expected) in listOf("2" to 2000L, "0" to 1000L, "-10" to 1000L, "7200" to 3600000L, "bad" to 60000L)) {
                val connection = FakeConnection(); connection.status = status; connection.headers["Retry-After"] = header
                val error = failed(AndroidReleaseFetcher { connection })
                check(error.retryAfterMillis == expected && error.message!!.contains("limitou"))
                check(connection.inputRequests == 0 && connection.disconnects == 1)
            }
        }
        test("rate-limit reset header is bounded and Retry-After takes precedence") {
            val now = System.currentTimeMillis() / 1000
            val connection = FakeConnection(); connection.status = 403; connection.headers["X-RateLimit-Reset"] = (now + 120).toString()
            check(failed(AndroidReleaseFetcher { connection }).retryAfterMillis in 119000L..120000L)
            connection.headers["Retry-After"] = "3"
            check(failed(AndroidReleaseFetcher { connection }).retryAfterMillis == 3000L)
            connection.headers.remove("Retry-After"); connection.headers["X-RateLimit-Reset"] = (now - 100).toString()
            check(failed(AndroidReleaseFetcher { connection }).retryAfterMillis == 1000L)
            connection.headers["X-RateLimit-Reset"] = Long.MAX_VALUE.toString()
            check(failed(AndroidReleaseFetcher { connection }).retryAfterMillis == 3600000L)
        }
        test("404, server errors, other codes and redirects fail safely without following Location") {
            for ((status, word) in listOf(404 to "pública", 500 to "indisponível", 503 to "indisponível", 301 to "mudou", 307 to "mudou", 401 to "401")) {
                val connection = FakeConnection(); connection.status = status; connection.headers["Location"] = "https://evil.test/app.apk"
                check(failed(AndroidReleaseFetcher { connection }).message!!.contains(word))
                check(!connection.instanceFollowRedirects && connection.inputRequests == 0 && connection.disconnects == 1)
            }
        }
        test("timeout, offline resolution and TLS errors are translated and always disconnected") {
            for ((failure, word) in listOf(SocketTimeoutException("private data") to "demorou", UnknownHostException("private data") to "encontrar", SSLException("private data") to "segura", IOException("private data") to "internet")) {
                val connection = FakeConnection(); connection.statusError = failure
                val error = failed(AndroidReleaseFetcher { connection })
                check(error.message!!.contains(word) && !error.message!!.contains("private data"))
                check(connection.disconnects == 1)
            }
            check(failed(AndroidReleaseFetcher { throw UnknownHostException("private data") }).message!!.contains("encontrar"))
        }
        test("body read errors close streams and the HTTPS connection") {
            var closes = 0
            val connection = FakeConnection(); connection.stream = object : InputStream() {
                override fun read(): Int { throw IOException("private read error") }
                override fun close() { closes++ }
            }
            val error = failed(AndroidReleaseFetcher { connection })
            check(!error.message!!.contains("private read error") && closes == 1 && connection.disconnects == 1)
        }
        test("cancel disconnects active transport and its blocked check finishes with no result") {
            val reading = CountDownLatch(1); val cancelled = CountDownLatch(1)
            var closes = 0
            val connection = FakeConnection(); connection.disconnectAction = { cancelled.countDown() }
            connection.stream = object : InputStream() {
                override fun read(): Int {
                    reading.countDown(); check(cancelled.await(5, TimeUnit.SECONDS)); throw IOException("cancelled by test")
                }
                override fun close() { closes++ }
            }
            val fetcher = AndroidReleaseFetcher { connection }
            val outcome = AtomicReference<Any?>()
            val worker = Thread { try { outcome.set(fetcher.fetch("7.4.0")) } catch (error: Exception) { outcome.set(error) } }
            worker.start(); check(reading.await(5, TimeUnit.SECONDS)); fetcher.cancel(); worker.join(5000)
            check(!worker.isAlive && outcome.get() is AndroidUpdateFailure && closes == 1 && connection.disconnects >= 1)
        }
        test("a worker already interrupted before fetch opens no connection") {
            val connection = FakeConnection(); val outcome = AtomicReference<Any?>(); var factories = 0
            val worker = Thread {
                Thread.currentThread().interrupt()
                try { outcome.set(AndroidReleaseFetcher { factories++; connection }.fetch("7.4.0")) } catch (error: Exception) { outcome.set(error) }
            }
            worker.start(); worker.join(5000)
            check(!worker.isAlive && outcome.get() is AndroidUpdateFailure && factories == 0)
            check(connection.inputRequests == 0 && connection.disconnects == 0)
        }
        test("cancelling before a queued fetch begins avoids even creating its transport") {
            var factories = 0
            val connection = FakeConnection(); val fetcher = AndroidReleaseFetcher { factories++; connection }
            fetcher.cancel(); check(failed(fetcher).retryAfterMillis == 0L)
            check(factories == 0 && connection.inputRequests == 0 && connection.disconnects == 0)
        }
        test("cancellation while constructing a connection cannot proceed to a request or body") {
            val constructing = CountDownLatch(1); val proceed = CountDownLatch(1)
            val connection = FakeConnection(); val outcome = AtomicReference<Any?>()
            val fetcher = AndroidReleaseFetcher {
                constructing.countDown(); check(proceed.await(5, TimeUnit.SECONDS)); connection
            }
            val worker = Thread { try { outcome.set(fetcher.fetch("7.4.0")) } catch (error: Exception) { outcome.set(error) } }
            worker.start(); check(constructing.await(5, TimeUnit.SECONDS)); fetcher.cancel(); proceed.countDown(); worker.join(5000)
            check(!worker.isAlive && outcome.get() is AndroidUpdateFailure && connection.disconnects == 1)
            check(connection.inputRequests == 0 && connection.requestHeaders.isEmpty())
        }
        println("ANDROID_RELEASE_FETCHER_CHECKS_PASSED=$count")
    }
}
