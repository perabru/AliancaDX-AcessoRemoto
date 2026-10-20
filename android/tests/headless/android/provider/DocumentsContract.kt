package android.provider

import android.content.Context
import android.content.ContentResolver
import android.net.Uri

object DocumentsContract {
    fun isDocumentUri(context: Context, uri: Uri) = uri.scheme == "content"
    fun deleteDocument(resolver: ContentResolver, uri: Uri) = resolver.delete(uri)
}

object OpenableColumns {
    const val DISPLAY_NAME = "_display_name"
    const val SIZE = "_size"
}
