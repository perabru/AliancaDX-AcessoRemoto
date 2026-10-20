package br.com.perabru.minianydesk

/** Exercises the production key migration and ComputerBook normalization on a JVM. */
object ConnectionPinsHarness {
    private val pin = "abcdef0123456789".repeat(4)
    private val upper = pin.uppercase()
    private val other = "1234567890abcdef".repeat(4)
    private fun expect(value: Boolean, message: String) { check(value) { message } }

    @JvmStatic fun main(args: Array<String>) {
        var cases = 0
        fun test(name: String, run: () -> Unit) { run(); cases++; println("PASS $name") }

        test("Unicode and ASCII DNS aliases retain one equal fingerprint") {
            val result = ConnectionPins.migrate(linkedMapOf(
                "pin:BÜCHER.Example.:64321" to pin,
                "pin:xn--bcher-kva.example:64321" to upper,
                "pin:xn--bcher-kva.EXAMPLE.:64321" to pin.chunked(2).joinToString(":")))
            expect(result == mapOf("pin:xn--bcher-kva.example:64321" to upper), "IDNA aliases did not coalesce")
            expect(ConnectionPins.preferenceKey("bücher.example.", 64321) == result.keys.single(), "Live preference key differs from migrated key")
        }
        test("three conflicting aliases drop trust for every ordering") {
            val aliases = listOf("pin:BÜCHER.example:64321" to pin,
                "pin:xn--bcher-kva.example:64321" to other,
                "pin:XN--BCHER-KVA.EXAMPLE.:64321" to pin)
            for (a in aliases.indices) for (b in aliases.indices) for (c in aliases.indices) {
                if (setOf(a, b, c).size != 3) continue
                expect(ConnectionPins.migrate(linkedMapOf(aliases[a], aliases[b], aliases[c])).isEmpty(),
                    "Later alias restored conflicting trust in order $a$b$c")
            }
        }
        test("expanded compressed bracketed and legacy unbracketed IPv6 share one key") {
            val result = ConnectionPins.migrate(linkedMapOf(
                "pin:2001:0db8:0000:0000:0000:0000:0000:0001:64321" to pin,
                "pin:[2001:db8::1]:64321" to upper,
                "pin:2001:DB8::1:64321" to pin))
            expect(result == mapOf("pin:[2001:db8::1]:64321" to upper), "IPv6 aliases differ")
        }
        test("IPv6 interface scope is retained and distinct scopes stay separate") {
            val result = ConnectionPins.migrate(linkedMapOf(
                "pin:fe80:0000:0000:0000:0000:0000:0000:0001%wlan0:64321" to pin,
                "pin:[fe80::1%wlan0]:64321" to upper,
                "pin:[fe80::1%eth0]:64321" to other))
            expect(result == mapOf("pin:[fe80::1%wlan0]:64321" to upper,
                "pin:[fe80::1%eth0]:64321" to other.uppercase()), "Scope normalization changed interface identity")
        }
        test("malformed endpoint ports values and fingerprints are ignored") {
            val result = ConnectionPins.migrate(mapOf(
                "pin:pc.example:0" to pin, "pin:pc.example:65536" to pin,
                "pin:pc.example:9999999999999999" to pin, "pin:pc.example:port" to pin,
                "pin:pc.example" to pin, "pin:[::1:64321" to pin,
                "pin:bad address:64321" to pin, "pin:http://pc.example:64321" to pin,
                "pin:pc.example:12" to null, "pin:pc.example:13" to 123,
                "pin:pc.example:14" to "g".repeat(64), "pin:pc.example:15" to pin.dropLast(1),
                "pin:pc.example:16" to "$pin ", "pin:pc.example:17" to ""))
            expect(result.isEmpty(), "Malformed saved preferences authorized trust")
        }
        test("passwords and unrelated preferences never enter the migrated map") {
            val result = ConnectionPins.migrate(mapOf("password" to pin, "address" to "pc.example",
                "role" to "host", "PIN:pc.example:64321" to pin, "pin:pc.example:64321" to pin))
            expect(result == mapOf("pin:pc.example:64321" to upper), "Other preferences were included")
        }
        test("conflict and invalid aliases do not discard an unrelated valid endpoint") {
            val result = ConnectionPins.migrate(linkedMapOf("pin:PC.example:64321" to pin,
                "pin:pc.example.:64321" to other, "pin:pc.example:64321" to pin,
                "pin:other.example:64321" to pin, "pin:OTHER.example.:64321" to "malformed"))
            expect(result == mapOf("pin:other.example:64321" to upper), "Conflict leaked into unrelated endpoint")
        }
        println("$cases connection-pin migration checks passed")
    }
}
