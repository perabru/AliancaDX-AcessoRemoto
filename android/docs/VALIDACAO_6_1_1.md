# Validação Android 6.1.1

Data: 19/10/2026. Pacote: `br.com.perabru.minianydesk.controlador`.
Versão: 6.1.1, código 61001. Android mínimo 8/API26, alvo/compilação API36.

## Compilação e inspeção

Build release, bundle, lint e código de instrumentação compilados com JDK17,
Gradle8.11.1, AGP8.10.1 e Kotlin2.1.20, sem incorporar chave privada ao Gradle.
As bibliotecas nativas previamente construídas foram preservadas; não houve
recompilação do EasyTier. O alinhamento ELF e dos offsets no APK é de 16 KiB.

O lint terminou com zero erros e 33 avisos: 19 SetTextI18n, 6 DrawAllocation,
3 UseRequiresApi, 3 CustomX509TrustManager, 1 UnusedAttribute e 1 UnusedResources.
Não foram acrescentadas supressões. As APIs de compatibilidade antigas mantêm
suporte ao Android8. Os três avisos de confiança TLS foram revisados: a identidade
do Host usa pin/conferência explícita para seus certificados próprios.

AndroidNet valida a identidade antes de enviar a senha; MeshTransport exige o
fingerprint do MDA6 e não envia dados da aplicação nessa conferência; PresenceProbe
compara pins salvos e não envia credenciais. Esse modelo não é o de uma página web
autenticada por uma CA pública. Os testes incluem recusa de pins errados ou alterados.

## Testes executados

- **62/62 testes de interoperabilidade**: Kotlin de produção atualizado contra
  o Python Desktop6.1.1 desta entrega. TLS local real, senha após validação,
  aprovação, imagens/comandos, monitores, arquivos, presença e vetores MDA6.
  As interfaces Android desse ensaio são doubles JVM; não simulam um aparelho.
- **8/8 testes de transferências**: incluem temporários abandonados, retenção de
  arquivos não pertencentes ao app e transferência ativa preservada quando
  outro gerenciador usa o mesmo cache. Também cobrem as seis regressões anteriores.
- **JNI**: 88 verificações de fronteira e 800 chamadas concorrentes com o adaptador
  C real, Linux/JDK17 e FFI de teste. Não é execução do EasyTier no Android.
- **12 testes da ferramenta de assinatura**: controles de credenciais/chaves,
  identidade/linhagem, saída Windows CRLF, falhas e publicação atômica.

Os resultados e hashes ficam em `docs/evidencias_6_1_1/`. Os relatórios da edição
6.1.0 em `docs/evidencias_6_1/` são históricos e não são contados como testes novos.

## Assinatura

A assinatura anterior foi preservada como origem de uma linhagem válida, com
chave nova a partir da API33. APIs26–32 conservam a assinatura antiga para permitir
atualizações. Essa assinatura antiga deve ser tratada como exposta porque sua
chave estava incluída no projeto anterior; a rotação não a revoga nos Androids
antigos. Os arquivos privados desta edição estão separados do projeto público.
Como a chave antiga era exposta, a primeira migração também depende da origem
confiável do download e da conferência do hash/certificado novo publicado.

Os certificados públicos e a política estão em `signing/`. A ferramenta confere
pacote, versão, launcher, ausência de depuração/testOnly, alinhamento e assinatura
com os Android Build Tools oficiais. Nenhuma assinatura representa aprovação do
Google ou garantia de ausência de avisos do Play Protect.

As verificações estritas (`-Werr`) passaram nos intervalos completos API26–32,
33–37 e 26–37, e nas APIs32, 33, 36 e 37 isoladas. O auxiliar Java com apksig
oficial confirmou os certificados dos blocos v2/v3/v3.1, seus intervalos e a
linhagem. Um exame limitado apenas à API26 avisa sobre a referência ao bloco v3,
que surgiu depois; esse exame isolado não é apresentado como aprovado com
`-Werr`. Não foram filtrados avisos. O detalhe está em `signing/LEIA-ME.md`.

SHA-256 do APK final:
`7133730ea66b230f050a0e20ce198822d86b29f3a057146c813b5034c586e8ce`.
Certificado novo SHA-256:
`1ddd6f2a1a82862a1a3a8a53cbbc257a8fc499bb5a500b8ebc0929792e14c2f1`.

A revisão independente do APK final está em `docs/RELATORIO_REVISAO_6_1_1.json`.
As quatro bibliotecas empacotadas são idênticas às entradas nativas do projeto.
Em relação ao APK6.1.0, as duas JNI são idênticas byte a byte; as duas FFI
conservam agora metadados antes removidos pelo empacotamento. Todas as seções
alocadas de código e dados das quatro bibliotecas coincidem com a edição anterior.

## Teste de aparelho e limites

O emulador oficial Android16/API36 foi preparado e seus downloads conferidos.
Sem KVM, a execução por software chegou ao system_server e ao PackageManager,
mas não terminou a inicialização no limite de 12 minutos. A instalação da
versão anterior foi recusada pelo Android com `device is still booting`.
Nenhuma atualização do APK, preservação real das preferências/AndroidKeyStore,
revisão visual ou instrumentação de toque foi validada nesse ambiente.
Isso é uma limitação do ensaio, não uma falha observada no aplicativo.
O emulador foi encerrado; o resultado está em
`docs/evidencias_6_1_1/runtime-api36/runtime-scope.json`.

Não houve varredura do Play Protect nem publicação em loja. Não houve teste em
celular físico, entre provedores físicos ou garantia de disponibilidade dos
retransmissores comunitários. O limite de arquivos permanece 100MiB, e o texto
remoto segue o suporte de caracteres básicos do Host Windows.
