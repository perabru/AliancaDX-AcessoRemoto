package br.com.perabru.minianydesk

import org.json.JSONArray
import org.json.JSONObject

/** Contract and trust-boundary checks for the exact production release parser. No network/device claim. */
object AndroidReleaseUpdateHarness {
    private const val ROOT = "https://github.com/perabru/AliancaDX-AcessoRemoto/releases/download/"
    private fun release(version: String = "7.5.0", tag: String = "v$version"): JSONObject = JSONObject()
        .put("draft", false).put("prerelease", false).put("published_at", "2026-10-27T12:34:56Z")
        .put("tag_name", tag).put("assets", JSONArray().put(JSONObject()
            .put("name", "AliancaDX_Acesso_Remoto_$version.apk").put("state", "uploaded").put("size", 10485760)
            .put("browser_download_url", "$ROOT$tag/AliancaDX_Acesso_Remoto_$version.apk")))
    private fun rejected(body: String, installed: String = "7.4.0") {
        try { AndroidReleaseUpdate.parse(body, installed); error("Invalid release was accepted") }
        catch (expected: IllegalArgumentException) { }
        catch (expected: org.json.JSONException) { }
    }
    @JvmStatic fun main(args: Array<String>) {
        var count = 0
        fun test(name: String, body: () -> Unit) { body(); count++; println("PASS $name") }
        test("fixed endpoint and newer stable APK are exact, with no session-derived query") {
            check(AndroidReleaseUpdate.API_URL == "https://api.github.com/repos/perabru/AliancaDX-AcessoRemoto/releases/latest")
            val result = AndroidReleaseUpdate.parse(release().toString(), "7.4.0")
            check(result.relation == AndroidReleaseUpdate.Relation.AVAILABLE)
            check(result.installedVersion == "7.4.0" && result.version == "7.5.0" && result.tag == "v7.5.0")
            check(result.apkUrl == ROOT + "v7.5.0/AliancaDX_Acesso_Remoto_7.5.0.apk")
        }
        test("same and locally newer versions never become available") {
            check(AndroidReleaseUpdate.parse(release("7.4.0").toString(), "7.4.0").relation == AndroidReleaseUpdate.Relation.CURRENT)
            check(AndroidReleaseUpdate.parse(release("7.3.0").toString(), "7.4.0").relation == AndroidReleaseUpdate.Relation.LOCAL_NEWER)
        }
        test("SemVer compares numeric components rather than text or flattened decimals") {
            for ((remote, local) in listOf("7.10.0" to "7.9.99", "8.0.0" to "7.99.99", "7.4.10" to "7.4.9"))
                check(AndroidReleaseUpdate.parse(release(remote).toString(), local).relation == AndroidReleaseUpdate.Relation.AVAILABLE)
            rejected(release("7.4.0", "7.4.0").toString())
        }
        test("draft and prerelease flags require literal false booleans") {
            for (key in listOf("draft", "prerelease")) for (bad in listOf<Any>(true, "false", 0, JSONObject.NULL))
                rejected(release().put(key, bad).toString())
            for (key in listOf("draft", "prerelease")) rejected(release().apply { remove(key) }.toString())
        }
        test("published release requires a real exact GitHub UTC publication timestamp") {
            for (bad in listOf<Any>("", "2026-02-30T00:00:00Z", "2026-01-01", "2026-01-01T25:00:00Z", "2026-01-01T00:00:00Zx", "2026-01-01T00:00:00+00:00", 1, false, JSONObject.NULL))
                rejected(release().put("published_at", bad).toString())
            rejected(release().apply { remove("published_at") }.toString())
        }
        test("non-stable or malformed tags cannot produce downloads") {
            for (bad in listOf("v7.5.0-beta", "v7.5.0+build", "v07.5.0", "V7.5.0", "vv7.5.0", "v7.5", "v7.5.0 ", "v-1.5.0", "v7.1000000000.0", "v999999999999999999999999999999999999.0.0", "v7.5.0/path", "v7.5.0?file=x"))
                rejected(release().put("tag_name", bad).toString())
            for (bad in listOf<Any>(1, false, JSONObject.NULL)) rejected(release().put("tag_name", bad).toString())
        }
        test("invalid installed version fails instead of offering an uncertain downgrade") {
            for (bad in listOf("7.4", "v7.4.0", "07.4.0", "7.4.0-dev", "", "7.4.0+build")) rejected(release().toString(), bad)
        }
        test("APK URL must match repository, tag, exact filename, HTTPS and every byte") {
            val valid = "$ROOT" + "v7.5.0/AliancaDX_Acesso_Remoto_7.5.0.apk"
            for (bad in listOf(valid.replace("https:", "http:"), valid.replace("github.com/", "github.com.evil.test/"), valid.replace("perabru/", "somebody/"), valid.replace("/v7.5.0/", "/v7.4.0/"), valid + "?download=1", valid + "#fragment", valid.replace("github.com/", "user@github.com/"), valid.replace("AliancaDX_Acesso_Remoto_", "aliancadx_acesso_remoto_"), valid.replace("v7.5.0", "v7%2e5%2e0"), valid.replace("https://github.com", "HTTPS://github.com"))) {
                val body = release(); body.getJSONArray("assets").getJSONObject(0).put("browser_download_url", bad); rejected(body.toString())
            }
        }
        test("asset filename must match stable version and expected case") {
            for (bad in listOf("AliancaDX_Acesso_Remoto_7.4.0.apk", "AliancaDX_Acesso_Remoto_7.5.0.apk.exe", "aliancadx_acesso_remoto_7.5.0.apk", "../AliancaDX_Acesso_Remoto_7.5.0.apk")) {
                val body = release(); body.getJSONArray("assets").getJSONObject(0).put("name", bad); rejected(body.toString())
            }
        }
        test("APK must be completely uploaded with an exact state literal") {
            for (bad in listOf<Any>("uploading", "uploaded ", "UPLOADED", "", false, true, 1, JSONObject.NULL)) {
                val body = release(); body.getJSONArray("assets").getJSONObject(0).put("state", bad); rejected(body.toString())
            }
            val missing = release(); missing.getJSONArray("assets").getJSONObject(0).remove("state"); rejected(missing.toString())
        }
        test("APK size must be a positive integer no larger than one GiB") {
            for (allowed in listOf(1L, 1073741824L)) {
                val body = release(); body.getJSONArray("assets").getJSONObject(0).put("size", allowed)
                check(AndroidReleaseUpdate.parse(body.toString(), "7.4.0").relation == AndroidReleaseUpdate.Relation.AVAILABLE)
            }
            for (bad in listOf<Any>(0, -1, 1073741825L, Long.MAX_VALUE, java.math.BigInteger("999999999999999999999999999999999999999999999999999999999999999"), "1024", false, true, JSONObject.NULL)) {
                val body = release(); body.getJSONArray("assets").getJSONObject(0).put("size", bad); rejected(body.toString())
            }
            val missing = release(); missing.getJSONArray("assets").getJSONObject(0).remove("size"); rejected(missing.toString())
        }
        test("decimal and exponent JSON literals cannot masquerade as integer APK sizes") {
            val body = release().toString()
            for (literal in listOf("1.0", "1e3", "1E3", "1000.000", "0.5", "1.073741824e9")) {
                val changed = body.replace("\"size\":10485760", "\"size\":" + literal)
                check(changed != body); rejected(changed)
            }
        }
        test("missing or duplicate matching APK is rejected, unrelated release assets are allowed") {
            rejected(release().put("assets", JSONArray()).toString())
            val duplicate = release(); val assets = duplicate.getJSONArray("assets"); assets.put(JSONObject(assets.getJSONObject(0).toString())); rejected(duplicate.toString())
            val normal = release(); normal.getJSONArray("assets").put(JSONObject().put("name", "Manual_AliancaDX.pdf").put("browser_download_url", ROOT + "v7.5.0/Manual_AliancaDX.pdf"))
            check(AndroidReleaseUpdate.parse(normal.toString(), "7.4.0").relation == AndroidReleaseUpdate.Relation.AVAILABLE)
        }
        test("malformed JSON structures and asset URL types cannot be coerced") {
            for (body in listOf("[]", "null", "not JSON", "{", release().put("assets", "[]").toString(), release().put("assets", JSONArray().put(4)).toString())) rejected(body)
            for (bad in listOf<Any>(true, 1, JSONObject.NULL)) {
                val body = release(); body.getJSONArray("assets").getJSONObject(0).put("browser_download_url", bad); rejected(body.toString())
            }
        }
        test("JSON response is bounded by UTF8 bytes as well as characters") {
            check(AndroidReleaseUpdate.MAX_RESPONSE_BYTES == 512 * 1024)
            rejected(" ".repeat(AndroidReleaseUpdate.MAX_RESPONSE_BYTES + 1))
            val body = release().put("body", "á".repeat(AndroidReleaseUpdate.MAX_RESPONSE_BYTES / 2)).toString()
            check(body.length < AndroidReleaseUpdate.MAX_RESPONSE_BYTES)
            check(body.toByteArray(Charsets.UTF_8).size > AndroidReleaseUpdate.MAX_RESPONSE_BYTES)
            rejected(body)
        }
        test("unrelated release title and body are never used as URLs or displayed result fields") {
            val body = release().put("name", "https://evil.test/app.apk").put("body", "arbitrary text")
            val result = AndroidReleaseUpdate.parse(body.toString(), "7.4.0")
            check(result.apkUrl == ROOT + "v7.5.0/AliancaDX_Acesso_Remoto_7.5.0.apk" && result.version == "7.5.0")
        }
        println("ANDROID_RELEASE_UPDATE_CHECKS_PASSED=$count")
    }
}
