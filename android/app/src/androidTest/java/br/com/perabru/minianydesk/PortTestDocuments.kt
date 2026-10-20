package br.com.perabru.minianydesk

import android.content.ContentProvider
import android.content.ContentValues
import android.database.Cursor
import android.database.MatrixCursor
import android.net.Uri
import android.os.ParcelFileDescriptor
import android.provider.OpenableColumns
import java.io.File

/** A real Android content provider containing synthetic test bytes only; picker selection is scripted. */
class PortTestDocuments : ContentProvider() {
    override fun onCreate() = true
    private fun document(id: String): File {
        require(id.matches(Regex("[a-z0-9-]+\\.bin")))
        return File(context!!.filesDir, id)
    }
    override fun query(uri: Uri, projection: Array<out String>?, selection: String?,
                       selectionArgs: Array<out String>?, sortOrder: String?): Cursor =
        MatrixCursor(arrayOf(OpenableColumns.DISPLAY_NAME, OpenableColumns.SIZE)).apply {
            val name = uri.lastPathSegment!!
            val file = document(name)
            require(file.exists())
            addRow(arrayOf(name, file.length()))
        }
    override fun openFile(uri: Uri, mode: String): ParcelFileDescriptor =
        ParcelFileDescriptor.open(document(uri.lastPathSegment!!), ParcelFileDescriptor.parseMode(mode))
    override fun getType(uri: Uri): String = "application/octet-stream"
    override fun insert(uri: Uri, values: ContentValues?): Uri {
        require(uri.lastPathSegment == "root")
        val displayName = values!!.getAsString(OpenableColumns.DISPLAY_NAME)
        check(document(displayName).createNewFile())
        return uri.buildUpon().path("/document/$displayName").build()
    }
    override fun delete(uri: Uri, selection: String?, selectionArgs: Array<out String>?): Int =
        if (document(uri.lastPathSegment!!).delete()) 1 else 0
    override fun update(uri: Uri, values: ContentValues?, selection: String?, selectionArgs: Array<out String>?): Int = 0
}
