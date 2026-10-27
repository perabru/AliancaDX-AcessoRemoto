package br.com.perabru.minianydesk

import java.io.File
import java.io.FileOutputStream
import java.io.IOException
import java.net.URL
import java.security.MessageDigest
import javax.net.ssl.HttpsURLConnection

internal interface AndroidApkSource {
    fun download(release: AndroidReleaseUpdate.Result, directory: File, progress: (Long, Long) -> Unit): File
    fun cancel()
}

/** Streams only the explicitly chosen official release into app-private storage. */
internal class AndroidApkDownload(
    private val connectionFactory: (URL) -> HttpsURLConnection = { it.openConnection() as HttpsURLConnection }
) : AndroidApkSource {
    @Volatile private var cancelled = false
    @Volatile private var active: HttpsURLConnection? = null

    override fun download(release: AndroidReleaseUpdate.Result, directory: File, progress: (Long, Long) -> Unit): File {
        require(release.relation == AndroidReleaseUpdate.Relation.AVAILABLE &&
            Regex("[0-9]+\\.[0-9]+\\.[0-9]+").matches(release.version) &&
            release.apkUrl == "https://github.com/perabru/AliancaDX-AcessoRemoto/releases/download/v${release.version}/AliancaDX_Acesso_Remoto_${release.version}.apk" &&
            release.apkSize in 1..1024L * 1024 * 1024 &&
            release.sha256?.matches(Regex("[0-9a-f]{64}")) == true) {
            "A publicação ainda não tem todos os dados para uma atualização interna segura. Tente novamente mais tarde."
        }
        check(!cancelled && !Thread.currentThread().isInterrupted) { "Atualização cancelada." }
        check(directory.mkdirs() || directory.isDirectory) { "Não foi possível preparar a atualização no celular." }
        directory.listFiles()?.filter { it.isFile && it.name.startsWith("aliancadx-") && it.name.endsWith(".apk") }?.forEach { it.delete() }
        val temporary = File.createTempFile("aliancadx-", ".apk", directory)
        val deadline = System.nanoTime() + 600_000_000_000L
        var connection: HttpsURLConnection? = null
        var completed = false
        try {
            var address = URL(release.apkUrl)
            for (hop in 0..3) {
                checkRunning(deadline)
                connection = connectionFactory(address)
                require(connection.url.toExternalForm() == address.toExternalForm()) { "Endereço inesperado da atualização." }
                active = connection
                connection.instanceFollowRedirects = false
                connection.connectTimeout = 10_000
                connection.readTimeout = 15_000
                connection.useCaches = false
                connection.requestMethod = "GET"
                connection.setRequestProperty("Accept-Encoding", "identity")
                connection.setRequestProperty("User-Agent", "AliancaDX-Android-Update/2")
                val status = connection.responseCode
                checkRunning(deadline)
                if (status in listOf(301, 302, 303, 307, 308)) {
                    require(hop < 3) { "O servidor redirecionou a atualização vezes demais." }
                    val next = URL(address, connection.getHeaderField("Location") ?: "")
                    require(allowedAssetRedirect(next)) { "O download saiu do serviço oficial de publicação." }
                    connection.disconnect(); connection = null; active = null
                    address = next
                    continue
                }
                if (status != 200) throw IOException("O download não foi concluído (código $status). Tente novamente.")
                val length = connection.contentLengthLong
                require(length == -1L || length == release.apkSize) { "O tamanho do arquivo não corresponde à publicação." }
                val encoding = connection.getHeaderField("Content-Encoding")
                require(encoding == null || encoding.equals("identity", true)) { "Formato inesperado do download." }
                val digest = MessageDigest.getInstance("SHA-256")
                var received = 0L
                connection.inputStream.use { input ->
                    FileOutputStream(temporary).use { output ->
                        val buffer = ByteArray(64 * 1024)
                        while (true) {
                            checkRunning(deadline)
                            val count = input.read(buffer)
                            checkRunning(deadline)
                            if (count < 0) break
                            if (count == 0) continue
                            require(received + count <= release.apkSize) { "O arquivo recebido excedeu o tamanho publicado." }
                            output.write(buffer, 0, count); digest.update(buffer, 0, count); received += count
                            progress(received, release.apkSize)
                        }
                        output.fd.sync()
                    }
                }
                require(received == release.apkSize) { "O download foi interrompido. Tente novamente." }
                val expected = hexBytes(release.sha256!!)
                require(MessageDigest.isEqual(digest.digest(), expected)) {
                    "A verificação da atualização falhou. O arquivo foi descartado; tente novamente."
                }
                checkRunning(deadline)
                completed = true
                return temporary
            }
            throw IOException("Não foi possível localizar o arquivo da atualização.")
        } finally {
            active = null; connection?.disconnect()
            if (!completed) temporary.delete()
        }
    }

    private fun checkRunning(deadline: Long) {
        if (cancelled || Thread.currentThread().isInterrupted) throw IOException("Atualização cancelada.")
        if (System.nanoTime() > deadline) throw IOException("O download demorou demais. Confira a internet e tente novamente.")
    }

    override fun cancel() { cancelled = true; active?.disconnect() }

    companion object {
        fun allowedAssetRedirect(url: URL): Boolean = url.protocol == "https" && url.userInfo == null &&
            (url.port == -1 || url.port == 443) && url.ref == null &&
            url.host in setOf("release-assets.githubusercontent.com", "objects.githubusercontent.com") &&
            url.path.startsWith("/") && url.path.length > 1
        fun hexBytes(value: String): ByteArray = ByteArray(value.length / 2) { index ->
            value.substring(index * 2, index * 2 + 2).toInt(16).toByte()
        }
    }
}
