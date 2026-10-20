package br.com.perabru.minianydesk

import org.json.JSONObject

object MeshPairingHarness {
    @JvmStatic fun main(args: Array<String>) {
        generateSequence(::readLine).forEach { line ->
            val request = JSONObject(line)
            val response = try {
                val pairing = MeshPairing.parseInvitation(request.getString("invitation"))
                JSONObject().put("ok", true).put("publicId", pairing.publicId).put("address", pairing.address)
                    .put("port", pairing.port).put("fingerprint", pairing.fingerprint).put("code", pairing.code)
                    .put("networkName", pairing.networkName).put("networkSecret", pairing.networkSecret)
                    .put("safeString", pairing.toString()).apply {
                        if (request.has("forwardPort")) put("config", MeshConfig.build(pairing,
                            request.getInt("forwardPort"), request.getString("controllerIp"),
                            instanceId = "d4dc9388-31c0-4da3-a846-d954267d788c",
                            forceRelay = request.optBoolean("forceRelay"),
                            peers = listOf("tcp://127.0.0.1:11010", "wss://relay.example/")))
                    }
            } catch (_: Exception) { JSONObject().put("ok", false) }
            println(response.toString())
        }
    }
}
