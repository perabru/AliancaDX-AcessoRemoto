package android.net

data class Uri(private val value: String) {
    val scheme: String get() = value.substringBefore(':')
    override fun toString() = value
    companion object { fun parse(value: String) = Uri(value) }
}
