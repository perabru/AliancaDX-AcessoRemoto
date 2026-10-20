# Build e integridade — Android Controlador 6.1.0

Arquivo de entrega: **MiniAnyDesk_Controlador_6.1.0.apk**.

- Pacote: `br.com.perabru.minianydesk.controlador`.
- Versão: 61000 / 6.1.0.
- Android: mínimo 8.0 / API 26; alvo e compilação API 35.
- ABIs: arm64-v8a e x86_64; não inclui ARM de 32 bits.
- Permissões: INTERNET e ACCESS_NETWORK_STATE.
- Gradle 8.11.1, AGP 8.9.2, Kotlin 2.1.20, build-tools 35.0.0, NDK 27.2.12479018, JDK 17.
- Recompilação limpa final: 123 tarefas (122 executadas / 1 atualizada) em 1 min 30 s; APKs Release, Debug e AndroidTest gerados.
- Lint Release: 0 erros e 36 avisos. Avisos incluem localização de textos, API de UI obsoleta e criação de objetos no desenho; o relatório completo está anexado.
- Assinatura APK v2 e alinhamento ZIP de 16 KB conferidos. Mesmo certificado da versão 4.0.0, SHA-256:`266dd27bd7af6914f0ddaebbbbbe30febb056c17939a94a8440fa6a33750b3d0`.
- Ausência de classes de captura/acessibilidade do antigo Hospedeiro no DEX conferida.
- Licenças EasyTier, avisos e licenças nativas incluídos em assets.

O JSON registra tanto os hashes das bibliotecas pré-compiladas em jniLibs quanto os das bibliotecas efetivamente incorporadas ao APK. O AGP remove símbolos ELF de depuração/desnecessários no empacotamento; essa transformação explica hashes diferentes sem alterar a interface binária.

Tamanho: 34910570 bytes.

SHA256 do APK final: `b4e14fac5d440b28c28321d57f97cff86ecf2e211c78847eedcf3ad0a4ef9bb1`.

Recompilação final após revisão visual: sucesso em 123 tarefas (122 executadas / 1 atualizada), em diretório temporário isolado para evitar arquivos transitórios da sincronização. Fontes Kotlin conferidos por SHA-256 antes de copiar apenas APKs e relatórios concluídos.
