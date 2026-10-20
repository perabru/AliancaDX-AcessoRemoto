package android.database

import java.io.File

class Cursor(private val file: File, private val knownSize: Boolean) : AutoCloseable {
    fun moveToFirst() = true
    fun getColumnIndex(name: String) = when (name) { "_display_name" -> 0; "_size" -> 1; else -> -1 }
    fun isNull(index: Int) = index == 1 && !knownSize
    fun getLong(index: Int) = file.length()
    fun getString(index: Int) = file.name
    override fun close() {}
}
