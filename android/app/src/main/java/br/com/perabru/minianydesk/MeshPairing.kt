package br.com.perabru.minianydesk

import java.security.MessageDigest
import java.util.Locale
import java.util.UUID
import javax.crypto.Mac
import javax.crypto.spec.SecretKeySpec

/** Desktop 6.x invitation. Its bearer secret is never part of a saved endpoint. */
class MeshPairing private constructor(
    private val token: ByteArray,
    val port: Int,
    val fingerprint: String,
    val code: String
) {
    val publicId: String = derive("public-id").hex().take(24)
    val address: String = "mesh-$publicId.invalid"
    internal val networkName: String = "mda6-" + derive("network-name").hex().take(32)
    internal val networkSecret: String get() = derive("network-secret").hex()

    private fun derive(label: String): ByteArray = Mac.getInstance("HmacSHA256").run {
        init(SecretKeySpec(token, "HmacSHA256"))
        doFinal("MiniAnyDesk6/$label".toByteArray(Charsets.UTF_8))
    }

    override fun toString(): String = "MeshPairing(publicId=$publicId, port=$port)"

    companion object {
        private const val ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"
        private val CODE = Regex("MDA6-[A-Z2-7]{114}")
        private val ADDRESS = Regex("mesh-[0-9a-f]{24}\\.invalid")

        fun isAddress(address: String): Boolean = ADDRESS.matches(address)

        fun parseInvitation(text: String): MeshPairing {
            require(text.length <= 4096) { "Convite muito longo. Copie novamente no Host." }
            val value = text.trim()
            if (value.uppercase(Locale.ROOT).startsWith("MDA6-") && '\n' !in value && '\r' !in value)
                return parseCode(value)
            val lines = value.lines()
            require(lines.firstOrNull()?.let { first ->
                first.startsWith("AliançaDX Host ") || first.startsWith("MiniAnyDesk Host ")
            } == true) {
                "Cole o código ou o convite completo copiado no Host do PC."
            }
            val codes = lines.filter { it.startsWith("Código privado:") }
            require(codes.size == 1) { "Cole um único convite completo do Host." }
            return parseCode(codes.single().substringAfter(':').trim())
        }

        fun parseCode(text: String): MeshPairing {
            require(text.length <= 160) { "Código inválido. Copie o código completo no Host." }
            val canonical = text.trim().uppercase(Locale.ROOT)
            require(CODE.matches(canonical)) { "Código incompleto ou inválido. Copie novamente no Host." }
            val encoded = canonical.substring(5)
            val payload = ByteArray(71)
            var buffer = 0
            var bits = 0
            var position = 0
            for (character in encoded) {
                buffer = (buffer shl 5) or ALPHABET.indexOf(character)
                bits += 5
                if (bits >= 8) {
                    bits -= 8
                    if (position < payload.size) payload[position++] = ((buffer ushr bits) and 255).toByte()
                }
            }
            require(position == 71 && bits == 2 && (buffer and 3) == 0 && payload[0] == 1.toByte()) {
                "Código inválido. Copie novamente no Host."
            }
            val body = payload.copyOfRange(0, 67)
            val checksum = MessageDigest.getInstance("SHA-256").digest("MDA6-check/".toByteArray() + body).copyOf(4)
            require(MessageDigest.isEqual(checksum, payload.copyOfRange(67, 71))) {
                "O código foi alterado ou cortado. Copie novamente no Host."
            }
            val port = ((payload[33].toInt() and 255) shl 8) or (payload[34].toInt() and 255)
            require(port in 1..65535) { "O código contém uma porta inválida." }
            val identity = payload.copyOfRange(35, 67)
            require(identity.any { it != 0.toByte() }) { "Copie um código atualizado com certificado no Host." }
            return MeshPairing(payload.copyOfRange(1, 33), port, identity.hex().uppercase(Locale.ROOT), canonical)
        }

        private fun ByteArray.hex(): String = joinToString("") { "%02x".format(it.toInt() and 255) }
    }
}

/** App-scoped, no-TUN configuration; no inbound or forwarding permission. */
object MeshConfig {
    private val RELAYS = listOf("tcp://public.easytier.top:11010", "tcp://38.76.179.190:11010", "wss://relay.38-76-179-190.sslip.io/")

    fun build(pairing: MeshPairing, forwardPort: Int, controllerIp: String,
              instanceId: String = UUID.randomUUID().toString(), forceRelay: Boolean = false,
              peers: List<String> = RELAYS): String {
        require(forwardPort in 1..65535)
        require(UUID.fromString(instanceId).toString() == instanceId)
        val octets = controllerIp.split('.').map { it.toIntOrNull() ?: -1 }
        require(octets.size == 4 && octets[0] == 10 && octets[1] == 126 &&
            octets.all { it in 0..255 } && controllerIp != "10.126.126.1")
        require(peers.isNotEmpty() && peers.size <= 8)
        for (peer in peers) {
            require(peer.length <= 256 && !peer.any { it == '"' || it == '\\' || it.code < 32 })
            require(peer.startsWith("tcp://") || peer.startsWith("wss://"))
        }
        return buildString {
            append("instance_name = \"MiniAnyDesk-android-$instanceId\"\ninstance_id = \"$instanceId\"\nhostname = \"MiniAnyDesk-android\"\n")
            append("ipv4 = \"$controllerIp/16\"\nipv6_public_addr_provider = false\nipv6_public_addr_auto = false\n")
            append("dhcp = false\nlisteners = []\nexit_nodes = []\nroutes = []\ntcp_whitelist = []\nudp_whitelist = []\n")
            append("[network_identity]\nnetwork_name = \"${pairing.networkName}\"\nnetwork_secret = \"${pairing.networkSecret}\"\n")
            for (peer in peers) append("[[peer]]\nuri = \"$peer\"\n")
            append("[[port_forward]]\nproto = \"tcp\"\nbind_addr = \"127.0.0.1:$forwardPort\"\ndst_addr = \"10.126.126.1:${pairing.port}\"\n")
            append("[flags]\nno_tun = true\nuse_smoltcp = true\nenable_encryption = true\nencryption_algorithm = \"aes-gcm\"\n")
            append("enable_ipv6 = false\nenable_exit_node = false\nproxy_forward_by_system = false\nbind_device = false\n")
            append("relay_network_whitelist = \"\"\nrelay_all_peer_rpc = false\naccept_dns = false\ndisable_upnp = true\n")
            append("enable_udp_broadcast_relay = false\nenable_kcp_proxy = false\ndisable_kcp_input = true\nenable_quic_proxy = false\n")
            append("disable_quic_input = true\nenable_relay_foreign_network_kcp = false\nenable_relay_foreign_network_quic = false\n")
            append("disable_relay_kcp = true\ndisable_relay_quic = true\ndisable_p2p = $forceRelay\n")
            if (forceRelay) append("disable_tcp_hole_punching = true\ndisable_udp_hole_punching = true\n")
            for ((type, name) in listOf(1 to "inbound", 3 to "forward"))
                append("[[acl.acl_v1.chains]]\nname = \"mda-$name\"\nchain_type = $type\nenabled = true\ndefault_action = 2\n")
            append("[[acl.acl_v1.chains]]\nname = \"mda-outbound\"\nchain_type = 2\nenabled = true\ndefault_action = 1\n")
            append("[console_logger]\nlevel = \"off\"\n[file_logger]\nlevel = \"off\"\n")
        }
    }
}
