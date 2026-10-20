package br.com.perabru.minianydesk

fun main() {
    val transient = listOf("Connection reset by peer", "read failed: ECONNRESET", "Broken pipe",
        "Sem resposta do outro dispositivo", "connect timed out", "No route to host", "ENETUNREACH",
        "O Host ainda não foi encontrado. Confira se o PC está aberto.")
    check(transient.all(ConnectionRecovery::retryable))
    val terminal = listOf("Conexão encerrada.", "EOF", "Socket closed", "Senha recusada: timed out",
        "Certificado incorreto: ECONNRESET", "session_end: network is unreachable",
        "Acesso não autorizado", "Encerrada pelo Host: Broken pipe", "Conexão cancelada",
        "O usuário do hospedeiro recusou a conexão: Connection reset", "Access denied: timed out")
    check(terminal.none(ConnectionRecovery::retryable))
    check((0..5).map(ConnectionRecovery::delayMs) == listOf(2000L,4000L,8000L,15000L,30000L,30000L))
    check(ConnectionRecovery.delayMs(-1) == 2000L && ConnectionRecovery.delayMs(Int.MAX_VALUE) == 30000L)
    val begin = 345_000L
    val end = ConnectionRecovery.deadline(begin)
    check(end == 465_000L)
    check(ConnectionRecovery.remaining(begin,end) == 120_000L)
    check(ConnectionRecovery.remaining(end-1,end) == 1L)
    check(ConnectionRecovery.remaining(end,end) == 0L && ConnectionRecovery.remaining(end+1,end) == 0L)
    check(ConnectionRecovery.deadline(Long.MAX_VALUE-1) == Long.MAX_VALUE)
    check(ConnectionRecovery.remaining(-1,Long.MAX_VALUE) == Long.MAX_VALUE)
    println("PASS: recovery transient/terminal precedence, bounded delays, fixed deadline and overflow boundaries")
}
