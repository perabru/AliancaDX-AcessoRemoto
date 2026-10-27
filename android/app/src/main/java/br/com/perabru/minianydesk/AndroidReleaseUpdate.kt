package br.com.perabru.minianydesk

import org.json.JSONArray
import org.json.JSONObject
import java.math.BigInteger
import java.text.ParsePosition
import java.text.SimpleDateFormat
import java.util.Locale
import java.util.TimeZone

/** Parses only a published stable release and its exact APK in the application's public repository. */
internal object AndroidReleaseUpdate {
    const val API_URL = "https://api.github.com/repos/perabru/AliancaDX-AcessoRemoto/releases/latest"
    const val MAX_RESPONSE_BYTES = 512 * 1024
    private const val MAX_APK_BYTES = 1024L * 1024L * 1024L
    private const val DOWNLOAD_ROOT = "https://github.com/perabru/AliancaDX-AcessoRemoto/releases/download/"
    private val versionPattern = Regex("(0|[1-9][0-9]{0,8})\\.(0|[1-9][0-9]{0,8})\\.(0|[1-9][0-9]{0,8})")
    private val publishedPattern = Regex("[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z")

    enum class Relation { AVAILABLE, CURRENT, LOCAL_NEWER }
    data class Result @JvmOverloads constructor(val installedVersion: String, val version: String, val tag: String,
                      val apkUrl: String, val relation: Relation, val apkSize: Long = 0, val sha256: String? = null)

    fun parse(body: String, installedVersion: String): Result {
        require(body.length <= MAX_RESPONSE_BYTES && body.toByteArray(Charsets.UTF_8).size <= MAX_RESPONSE_BYTES) {
            "A resposta de atualização excedeu o limite permitido."
        }
        val installed = versionParts(installedVersion)
        val release = JSONObject(body)
        require((release.opt("draft") as? Boolean) == false && (release.opt("prerelease") as? Boolean) == false) {
            "A versão encontrada ainda não é uma publicação estável."
        }
        val publishedAt = release.opt("published_at") as? String
        require(publishedAt != null && isPublishedDate(publishedAt)) {
            "A publicação não informou uma data válida."
        }
        val tag = release.opt("tag_name") as? String
            ?: throw IllegalArgumentException("A publicação não informou uma versão válida.")
        require(tag.startsWith("v")) { "A publicação não usa o identificador oficial de versão." }
        val version = tag.substring(1)
        val available = versionParts(version)
        val assets = release.opt("assets") as? JSONArray
            ?: throw IllegalArgumentException("A publicação não informou seus arquivos.")
        require(assets.length() <= 1024) { "A publicação informou arquivos demais para conferir." }
        val apkName = "AliancaDX_Acesso_Remoto_$version.apk"
        val expectedUrl = DOWNLOAD_ROOT + tag + "/" + apkName
        var found = 0
        var apkSize = 0L
        var sha256: String? = null
        for (index in 0 until assets.length()) {
            val asset = assets.optJSONObject(index)
                ?: throw IllegalArgumentException("A lista de arquivos da publicação é inválida.")
            if (asset.opt("name") != apkName) continue
            require((asset.opt("state") as? String) == "uploaded") {
                "O aplicativo Android ainda não terminou de ser publicado."
            }
            val size = integerSize(asset.opt("size"))
            require(size != null && size in 1..MAX_APK_BYTES) {
                "A publicação informou um tamanho inválido para o aplicativo Android."
            }
            require(asset.opt("browser_download_url") == expectedUrl) {
                "O endereço do aplicativo não corresponde à publicação oficial."
            }
            apkSize = size
            val digest = asset.opt("digest")
            if (digest != null && digest != JSONObject.NULL) {
                require(digest is String && Regex("sha256:[0-9a-f]{64}").matches(digest)) {
                    "A publicação informou uma assinatura de integridade inválida."
                }
                sha256 = (digest as String).removePrefix("sha256:")
            }
            found++
        }
        require(found == 1) { "Não foi possível conferir um único aplicativo Android nesta publicação." }
        val comparison = compare(available, installed)
        return Result(installedVersion, version, tag, expectedUrl, when {
            comparison > 0 -> Relation.AVAILABLE
            comparison < 0 -> Relation.LOCAL_NEWER
            else -> Relation.CURRENT
        }, apkSize, sha256)
    }

    // JSON decimal/exponent values, strings and booleans must never be coerced to a size.
    private fun integerSize(value: Any?): Long? = when (value) {
        is Int -> value.toLong()
        is Long -> value
        is BigInteger -> if (value.signum() > 0 && value.bitLength() <= 31) value.toLong() else null
        else -> null
    }

    private fun versionParts(version: String): List<Int> {
        val match = versionPattern.matchEntire(version)
        require(match != null) { "O número da versão publicada é inválido." }
        return match.groupValues.drop(1).map { it.toInt() }
    }

    private fun compare(first: List<Int>, second: List<Int>): Int {
        for (index in 0..2) {
            val difference = first[index].compareTo(second[index])
            if (difference != 0) return difference
        }
        return 0
    }

    private fun isPublishedDate(value: String): Boolean {
        if (!publishedPattern.matches(value)) return false
        val formatter = SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss'Z'", Locale.ROOT).apply {
            isLenient = false
            timeZone = TimeZone.getTimeZone("UTC")
        }
        val position = ParsePosition(0)
        val date = formatter.parse(value, position) ?: return false
        return position.index == value.length && formatter.format(date) == value
    }
}
