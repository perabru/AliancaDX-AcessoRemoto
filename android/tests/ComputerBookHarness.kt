package br.com.perabru.minianydesk

import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.io.IOException
import java.nio.file.Files
import java.nio.file.StandardCopyOption
import java.util.UUID
import java.util.concurrent.Callable
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit

/** Runs production ComputerBook; no mocked validation or CRUD logic. Android's Context path is unused. */
object ComputerBookHarness {
    private class MemoryStore(var value: String? = null) : ComputerBook.Store {
        var fail = false
        override fun read() = value
        override fun write(value: String?): Boolean {
            this.value = value // SharedPreferences updates memory even when commit() returns false.
            return !fail
        }
    }

    private class FileStore(private val file: File) : ComputerBook.Store {
        override fun read() = if (file.exists()) file.readText() else null
        override fun write(value: String?): Boolean {
            if (value == null) { file.delete(); return true }
            val temporary = File(file.parentFile, file.name + ".tmp")
            temporary.writeText(value)
            Files.move(temporary.toPath(), file.toPath(), StandardCopyOption.REPLACE_EXISTING, StandardCopyOption.ATOMIC_MOVE)
            return true
        }
    }

    private inline fun <reified T : Throwable> fails(message: String? = null, action: () -> Unit) {
        try { action(); error("Expected ${T::class.java.simpleName}") }
        catch (error: Throwable) {
            check(error is T) { "Expected ${T::class.java.simpleName}, received $error" }
            if (message != null) check(error.message?.contains(message) == true) { error.message ?: "No error message" }
        }
    }

    private fun fixture(count: Int = 1): JSONObject {
        val records = JSONArray()
        repeat(count) { index -> records.put(JSONObject().put("id", UUID.randomUUID().toString())
            .put("name", "Computador $index").put("address", "pc-$index.example.org")
            .put("port", 64321).put("lastSeen", JSONObject.NULL)) }
        return JSONObject().put("schema", 1).put("computers", records)
    }

    @JvmStatic fun main(args: Array<String>) {
        val cases = linkedMapOf<String, () -> Unit>()
        cases["empty registry does not write"] = {
            val store = MemoryStore()
            check(ComputerBook(store).entries().isEmpty())
            check(store.value == null)
        }
        cases["CRUD persists across independent reloads without passwords"] = {
            val directory = Files.createTempDirectory("computer-book-test").toFile()
            try {
                val file = File(directory, "registry.json")
                val book = ComputerBook(FileStore(file))
                val first = book.add("  Sala de informática  ", "LAB.EXAMPLE.ORG.", 64321)
                check(first.name == "Sala de informática" && first.address == "lab.example.org")
                check(ComputerBook(FileStore(file)).entries() == listOf(first))
                val edited = book.update(first.id, "Computador do professor", "192.168.1.50", 5905)
                check(edited.id == first.id && edited.lastSeen == null)
                val seen = book.markSeen(first.id, 1_791_628_200_000)
                check(seen.name == "Computador do professor")
                check(ComputerBook(FileStore(file)).entries() == listOf(seen))
                val json = JSONObject(file.readText())
                check(json.getInt("schema") == 1)
                check(json.getJSONArray("computers").getJSONObject(0).keys().asSequence().toSet() == setOf("id", "name", "address", "port", "lastSeen"))
                check(!file.readText().contains("password", ignoreCase = true))
                book.remove(first.id)
                check(ComputerBook(FileStore(file)).entries().isEmpty())
            } finally { directory.listFiles()?.forEach { it.delete() }; directory.delete() }
        }
        cases["returned collections cannot mutate stored aliases"] = {
            val store = MemoryStore()
            val book = ComputerBook(store)
            val first = book.add("Meu alias", "office.example", 64321)
            val list = book.entries().toMutableList()
            list[0] = first.copy(name = "Cópia editada")
            list.clear()
            check(book.entries() == listOf(first))
            check(book.findEndpoint("OFFICE.EXAMPLE.", 64321) == first)
        }
        cases["DNS IDNA and IPv6 variants share endpoint identity"] = {
            check(ComputerBook.canonicalAddress("BÜCHER.Example.") == "xn--bcher-kva.example")
            check(ComputerBook.endpointKey("bücher.example", 64321) == ComputerBook.endpointKey("xn--bcher-kva.example", 64321))
            check(ComputerBook.canonicalAddress("[2001:0DB8:0:0:0:0:0:1]") == "2001:db8::1")
            check(ComputerBook.canonicalAddress("0:0:0:0:0:0:0:0") == "::")
            check(ComputerBook.canonicalAddress("::FFFF:192.0.2.1") == "::ffff:c000:201")
            check(ComputerBook.canonicalAddress("FE80::1%Ethernet") == "fe80::1%Ethernet")
            check(ComputerBook.canonicalAddress("2001:db8:0:1:0:1:0:1") == "2001:db8:0:1:0:1:0:1")
            check(ComputerBook.canonicalAddress("2001:0:0:1:0:0:1:1") == "2001::1:0:0:1:1")
        }
        cases["duplicates rejected while renaming same device and alternate port work"] = {
            val book = ComputerBook(MemoryStore())
            val named = book.add("Lab", "PC.EXAMPLE.", 64321)
            fails<IllegalArgumentException>("já estão cadastrados") { book.add("Outro", "pc.example", 64321) }
            val alternate = book.add("Outra porta", "pc.example", 5905)
            val old = book.entries()
            fails<IllegalArgumentException> { book.update(alternate.id, "Não salvar", "pc.example", 64321) }
            check(book.entries() == old)
            check(book.update(named.id, "Nome escolhido", "PC.EXAMPLE.", 64321).id == named.id)
            val v6 = book.add("IPv6", "[::1]", 64321)
            fails<IllegalArgumentException> { book.add("Duplicado IPv6", "0:0:0:0:0:0:0:1", 64321) }
            check(book.findEndpoint("::1", 64321) == v6)
        }
        cases["valid boundaries and supplementary Unicode names"] = {
            val book = ComputerBook(MemoryStore())
            check(book.add("A".repeat(80), "a".repeat(63) + ".example.org", 1).port == 1)
            check(book.add("Laboratório 😀", "[fe80::1%en0]", 65535).port == 65535)
        }
        cases["invalid names addresses and ports never write"] = {
            val store = MemoryStore()
            val book = ComputerBook(store)
            listOf("", "   ", "A".repeat(81), "Lab\nSala", "\u0000Lab", "Lab\u202e", "Lab\ud800").forEach { name ->
                fails<IllegalArgumentException> { book.add(name, "pc.example", 64321) }
            }
            listOf("", " pc", "pc lab", "pc\n", "https://pc", "pc/path", "user@pc", "pc:64321",
                "[pc]", "[::1", "::1]", "000.1.2.3", "256.1.2.3", "example..org", "a".repeat(254),
                "-pc", "pc-", "a".repeat(64) + ".org", "[127.0.0.1]", ":::1", "1::2::3", "1:2:3:4:5:6:7",
                "1:2:3:4:5:6:7:8:9", "fe80::1%bad scope").forEach { address ->
                fails<IllegalArgumentException> { book.add("Lab", address, 64321) }
            }
            listOf(-1, 0, 65536).forEach { port -> fails<IllegalArgumentException> { book.add("Lab", "pc.example", port) } }
            check(store.value == null)
        }
        cases["unknown and invalid IDs produce no writes"] = {
            val store = MemoryStore()
            val book = ComputerBook(store)
            val first = book.add("Lab", "pc.example", 64321)
            val raw = store.value
            fails<IllegalArgumentException> { book.remove(UUID.randomUUID().toString()) }
            fails<IllegalArgumentException> { book.update("invalid", "Nome", "pc.example", 64321) }
            fails<IllegalArgumentException> { book.markSeen(first.id, -1) }
            check(store.value == raw)
        }
        cases["256 records load and rename but cannot add another"] = {
            val store = MemoryStore(fixture(256).toString())
            val book = ComputerBook(store)
            val old = store.value
            fails<IllegalArgumentException>("256") { book.add("Novo", "new.example", 64321) }
            check(store.value == old)
            val first = book.entries()[0]
            book.update(first.id, "Nome atualizado", first.address, first.port)
            check(book.entries().size == 256)
        }
        cases["corrupt unsupported and malformed records are preserved"] = {
            val bad = mutableListOf("", "{broken", "{\"schema\":2,\"computers\":[]}", "{\"schema\":true,\"computers\":[]}",
                fixture(257).toString(), "[".repeat(33) + "]".repeat(33))
            listOf("port" to true, "lastSeen" to -1L, "lastSeen" to "yesterday", "id" to "invalid").forEach { (key, value) ->
                val json = fixture(); json.getJSONArray("computers").getJSONObject(0).put(key, value); bad.add(json.toString())
            }
            val password = fixture(); password.getJSONArray("computers").getJSONObject(0).put("password", "not allowed")
            bad.add(password.toString())
            val duplicate = fixture(2); val array = duplicate.getJSONArray("computers")
            array.getJSONObject(1).put("address", array.getJSONObject(0).getString("address")); bad.add(duplicate.toString())
            bad.forEach { raw ->
                val store = MemoryStore(raw)
                fails<IllegalArgumentException>("preservados") { ComputerBook(store) }
                check(store.value == raw)
            }
        }
        cases["external corruption detected before overwrite"] = {
            val store = MemoryStore()
            val book = ComputerBook(store)
            book.add("Lab", "pc.example", 64321)
            store.value = "{changed externally"
            fails<IllegalArgumentException> { book.entries() }
            fails<IllegalArgumentException> { book.add("Novo", "new.example", 64321) }
            check(store.value == "{changed externally")
        }
        cases["failed commit restores SharedPreferences-like in-memory state and permits retry"] = {
            val store = MemoryStore()
            val book = ComputerBook(store)
            val first = book.add("Lab", "pc.example", 64321)
            val raw = store.value
            store.fail = true
            listOf<() -> Unit>(
                { book.add("Novo", "new.example", 64321) },
                { book.update(first.id, "Não salvar", "pc.example", 64321) },
                { book.remove(first.id) }, { book.markSeen(first.id, 5000) }
            ).forEach { action ->
                fails<IOException>("dados anteriores foram mantidos", action)
                check(store.value == raw && book.entries() == listOf(first))
            }
            store.fail = false
            check(book.add("Novo", "new.example", 64321).name == "Novo")
        }
        cases["repeated markSeen preserves alias and identity"] = {
            val store = MemoryStore()
            val book = ComputerBook(store)
            val first = book.add("Meu nome escolhido", "pc.example", 64321)
            listOf(1000L, 2000L, 3000L).forEach { now ->
                val seen = book.markSeen(first.id, now)
                check(seen.id == first.id && seen.name == first.name && seen.lastSeen == now)
            }
            check(ComputerBook(store).entries().single().lastSeen == 3000L)
        }
        cases["multiple books and concurrent threads cannot lose edits"] = {
            val store = MemoryStore()
            val first = ComputerBook(store)
            val second = ComputerBook(store)
            val pool = Executors.newFixedThreadPool(8)
            try {
                val jobs = (0 until 40).map { index -> Callable {
                    val book = if (index % 2 == 0) first else second
                    val entry = book.add("Original $index", "pc-$index.example", 64321)
                    book.update(entry.id, "Nome escolhido $index", entry.address, entry.port)
                    book.markSeen(entry.id, index.toLong())
                    entry.id
                } }
                val ids = pool.invokeAll(jobs).map { it.get(5, TimeUnit.SECONDS) }.toSet()
                val records = ComputerBook(store).entries()
                check(records.size == 40 && records.map { it.id }.toSet() == ids)
                check(records.map { it.name }.toSet() == (0 until 40).map { "Nome escolhido $it" }.toSet())
                check(records.all { it.lastSeen != null })
            } finally { pool.shutdownNow() }
        }
        cases.forEach { (name, test) -> test(); println("PASS $name") }
        println(JSONObject().put("passed", cases.size).put("failed", 0))
    }
}
