package android.content

import android.net.Uri
import android.database.Cursor
import java.io.File
import java.io.FileInputStream
import java.io.FileOutputStream
import java.io.InputStream
import java.io.OutputStream
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.atomic.AtomicInteger

/** Test doubles only; these are never included in the APK's source set. */
open class Context(val cacheDir: File, val contentResolver: ContentResolver) {
    val applicationContext: Context get() = this
}

class ContentResolver(private val knownSize: Boolean = true) {
    private val documents = ConcurrentHashMap<String, File>()
    @Volatile var beforeOpenOutput: ((Uri) -> Unit)? = null
    @Volatile var transformOutput: ((OutputStream) -> OutputStream)? = null
    @Volatile var deleteAllowed = true
    val deleteCalls = AtomicInteger()
    fun register(file: File): Uri {
        val uri = Uri.parse("content://test/" + java.util.UUID.randomUUID())
        documents[uri.toString()] = file
        return uri
    }
    fun openInputStream(uri: Uri): InputStream = FileInputStream(documents[uri.toString()] ?: error("No document"))
    fun openOutputStream(uri: Uri, mode: String): OutputStream {
        check(mode == "w")
        beforeOpenOutput?.invoke(uri)
        val stream = FileOutputStream(documents[uri.toString()] ?: error("No document"))
        return transformOutput?.invoke(stream) ?: stream
    }
    fun query(uri: Uri, projection: Array<String>, selection: String?, args: Array<String>?, sort: String?): Cursor {
        val file = documents[uri.toString()] ?: error("No document")
        return Cursor(file, knownSize)
    }
    fun delete(uri: Uri): Boolean {
        deleteCalls.incrementAndGet()
        if (!deleteAllowed) return false
        return documents.remove(uri.toString())?.delete() ?: false
    }
}
