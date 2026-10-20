# Validação do MiniAnyDesk Controlador Android 6.1.0

Data: 16/10/2026. Contraparte: MiniAnyDesk Desktop/Host Python 6.1.

Este relatório separa execução na JVM, compilação, inspeção dos binários e
execução no Android. As contagens usam unidades diferentes e não devem ser
somadas como se fossem uma única suíte. Resultados antigos de Android 4.0 ou
Desktop 6.1 não substituem a validação deste aplicativo.

## Estado consolidado

| Verificação | Resultado registrado | Alcance e evidência |
|---|---|---|
| Interoperabilidade do Controlador Kotlin com Desktop Python 6.1 | 62 casos aprovados; 0 falhas, 0 erros, 0 ignorados | TLS local real na JVM, com substitutos de APIs/provedores Android; [relatório](../tests/port_6_1/evidence/controller-interop-report.json) e [log](../tests/port_6_1/evidence/controller-interop.log). |
| Geometria, ampliação e regras de toque | 29 grupos de verificações: 9 + 10 + 10 | Geometria e lógica de toque da implementação de produção, com primitivas Android simuladas na JVM; [log](../tests/viewport/checks.log) e [escopo](../tests/viewport/VALIDACAO.txt). |
| Compatibilidade das APIs da tela | Compilação aprovada contra android-35.jar | Verificação de tipos/APIs; não executa o renderizador Android. Mesma evidência da bateria de tela. |
| Regras de recuperação | 1 programa JVM aprovado, com 10 expressões de verificação | Regras puras de classificação, intervalos e prazo; [relatório](../tests/recovery/report.json) e [log](../tests/recovery/checks.log). |
| Fronteira JNI/C | 88 verificações e 800 chamadas concorrentes aprovadas | JNI real em JDK 17 com C ABI simulado; [resultado](../native-sources/tests/RESULTADO_JVM.txt), [código de teste](../native-sources/tests/) e [descrição](../native-sources/JNI_README.md). |
| Compilação e estrutura das bibliotecas nativas | 4 bibliotecas aprovadas, em 2 ABIs | EasyTier 2.6.4 compilado para arm64-v8a e x86_64; hashes, exports e segmentos ELF em [NATIVE_PROVENANCE.json](../native-sources/NATIVE_PROVENANCE.json). |
| Build limpo Release, Debug, AndroidTest e lint | 123 tarefas Gradle aprovadas; lint com 0 erros e 36 avisos | Build final isolado, assinatura, integridade, bibliotecas e alinhamento ZIP aprovados; [relatório](evidencias_6_1/BUILD_APK_6_1.md). São tarefas de build, não casos de teste. |
| Instrumentação com EasyTier real no Android | 8 de 8 cenários aprovados no APK final | Emulador API 35 x86_64, núcleo JNI real e retransmissor local forçado; transporte externo por ADB reverso. [Relatório](evidencias_6_1/native-runtime-report.json). |
| Revisão visual no Android | 11 cenários aprovados no APK final | Interface real, teclado, fonte ampliada e painéis; [relatório](evidencias_6_1/ui-runtime-report.json). Capturas usadas internamente. |

## O que os 62 casos de interoperabilidade verificaram

A bateria compila os fontes Kotlin atuais e usa uma cópia independente do
Desktop 6.1 como contraparte. São 8 casos de código privado, HMAC e configuração
TOML, mais 54 casos de transporte, protocolo, arquivos e comandos. Seis casos
antigos exclusivos do papel Hospedeiro Android são excluídos explicitamente;
eles não contam como aprovados nem como ignorados. Este aplicativo é o
Controlador de um PC.

Foram exercitados TLS local, autenticação e encerramento/recusa, identificação do
certificado, quadros JPEG, despacho dos comandos pelo Host Python, coordenadas
com origem negativa, filas, prazos, heartbeat e consulta de disponibilidade por
ALPN sem iniciar uma sessão de controle. O código de entrada do PC é chamado
com um provedor que registra as ações; isso não comprova que um Windows real
recebeu os cliques ou teclas.

As transferências em ambos os sentidos verificam blocos, SHA-256, pedidos,
recusas e limpeza de arquivos incompletos após Base64 ou hash inválidos. O
limite de 100 MiB aparece nos testes de validação de pedidos. Isso não significa
que um arquivo de exatamente 100 MiB foi transferido em um celular físico.
Os provedores Android desta bateria são substitutos na JVM; a instrumentação Android separada cobre ContentResolver e descritores
com um provedor de conteúdo local; não exercita o seletor de documentos do sistema.

Os hashes dos fontes efetivamente usados estão no relatório de
interoperabilidade. Alterações posteriores nesses arquivos exigem reavaliar o
resultado correspondente.

## Tela, entrada e recuperação

Os 29 grupos de geometria e toque verificam ajuste à área disponível,
coordenadas inclusivas dos cantos, bordas, proporções, deslocamento, ampliação
entre 1x e 6x e estabilidade após ciclos repetidos. A lógica de produção recebe
eventos e relógio simulados na JVM. São verificadas a liberação do mouse ao
cancelar/pausar/trocar captura, a separação entre gesto de dois dedos e clique,
e a continuidade da ampliação local quando o controle remoto está pausado.
Esses resultados não comprovam a renderização, a decodificação gráfica ou os
toques de um aparelho físico.

O programa de recuperação executado verifica 8 descrições de falha transitória,
11 descrições terminais e suas precedências, intervalos de 2, 4, 8, 15 e 30
segundos, janela fixa de 120 segundos e limites de estouro numérico. Uma recusa,
senha/certificado incorreto, cancelamento ou encerramento intencional deve
interromper as tentativas. São verificações da regra pura; uma queda de rede e
reconexão completas no Android continuam dependendo do teste de execução.

## Núcleo nativo e componentes incluídos

As bibliotecas `libeasytier_ffi.so` e `libeasytier_android_jni.so` foram
compiladas para arm64-v8a e x86_64, tendo Android API 26 como alvo nativo mínimo.
As quatro possuem todos os exports esperados, segmentos LOAD com alinhamento
mínimo de 16 KiB, pilha não executável e ausência de relocação de texto. As
dependências dinâmicas do núcleo são somente `libc.so`, `libdl.so` e `libm.so`;
a ponte JNI também depende da biblioteca FFI incluída. Esta inspeção do ELF é
separada do alinhamento ZIP e da assinatura do APK final. O Android Gradle
Plugin remove símbolos de depuração dos binários FFI ao empacotá-los. Por isso,
os hashes dos binários de entrada em `NATIVE_PROVENANCE.json` e os hashes
extraídos do APK podem diferir legitimamente. O relatório do APK final deve
registrar os dois conjuntos, incluindo essa transformação; não deve tratá-la
como corrupção do núcleo.

A versão do núcleo é EasyTier 2.6.4, compatível com a contraparte Desktop 6.1.
O modo configurado é `no_tun=true`, com encaminhamento TCP restrito ao loopback
do aplicativo. Não requer conta de VPN, criação de adaptador ou `VpnService`.
As features `tun` e `magic-dns` são compiladas por exigência dos tipos usados
pelo lançador Android do upstream; isso não ativa uma VPN do sistema.

Um retorno zero de `runNetworkInstance` indica que a inicialização foi
encaminhada. Não é prova de PC encontrado, retransmissor disponível ou sessão
TLS aprovada. A sessão precisa concluir o caminho de rede e a autenticação do
MiniAnyDesk, mantendo senha e aprovação no PC.

## Limites de compatibilidade e uso

- Android 8 ou superior, em arquitetura **64 bits ARM64**. Há também binários
  x86_64 para a arquitetura correspondente/emulador. Não há suporte compilado a
  aparelhos exclusivamente armeabi-v7a/32 bits nesta entrega.
- O Host do PC deve ser compatível com MiniAnyDesk Desktop 6.1 e permanecer
  aberto. Código privado e senha não substituem a aprovação do acesso no Host.
- O envio de texto atende ASCII básico, além de Enter/CRLF e Tab, até 1.024
  caracteres por envio. O protocolo atual não oferece colagem de Unicode,
  acentos ou emojis. Esse limite é apresentado separadamente do envio de teclas
  especiais e modificadores.
- Arquivos: até **100 MiB (104.857.600 bytes)** por transferência, com blocos de
  64 KiB e verificação SHA-256. O acesso aos arquivos depende das permissões e
  da seleção fornecida pelo usuário nos dois dispositivos.
- Uma conexão local, uma JVM ou um emulador com retransmissor local não prova
  conexão entre provedores físicos diferentes nem disponibilidade dos
  retransmissores públicos. Rede, dispositivos ligados e retransmissor/caminho
  direto disponível continuam necessários.

## Código-fonte, licenças e reprodução

O diretório [native-sources](../native-sources/) inclui o fonte EasyTier 2.6.4,
os snapshots exatos das dependências, a ponte JNI em C, licenças LGPL/GPL,
atribuições de terceiros, inventário e os scripts de reconstrução. Os fontes
Kotlin e scripts Gradle também acompanham o projeto. É possível modificar,
reconstruir, substituir as bibliotecas e assinar um APK modificado com chave
própria, conforme as licenças aplicáveis.

O manifesto [NATIVE_PROVENANCE.json](../native-sources/NATIVE_PROVENANCE.json)
registra versões do Rust, NDK e protoc, features selecionadas, hashes dos
arquivos-fonte e dos quatro binários. O [guia de reconstrução
nativa](../native-sources/BUILD_ANDROID.md) explica os pré-requisitos externos;
o APK já contém suas bibliotecas de execução. As licenças/avisos também são
incluídos nos assets do aplicativo.

Reprodução das regras de recuperação:

```sh
python3 tests/run_recovery_checks.py /caminho/android_port_tools
```

A localização das ferramentas segue o runner de tela: Gradle 8.11.1 para o
compilador Kotlin e, quando disponível, JDK 17 no diretório informado. O teste
usa arquivos temporários para a compilação e grava somente o log e os hashes
em `tests/recovery/`. Ele não inicia conexão de rede.

Os runners de interoperabilidade e tela têm instruções em
[tests/port_6_1/README.md](../tests/port_6_1/README.md) e
[tests/viewport/VALIDACAO.txt](../tests/viewport/VALIDACAO.txt). A instrumentação
Android está em `app/src/androidTest`; seu resultado efetivo está registrado abaixo.

## Consolidação final do APK

Arquivo: `MiniAnyDesk_Controlador_6.1.0.apk`, 34,910,570 bytes.
Pacote `br.com.perabru.minianydesk.controlador`; versionCode 61000 e versionName 6.1.0.
SHA-256: `b4e14fac5d440b28c28321d57f97cff86ecf2e211c78847eedcf3ad0a4ef9bb1`.

O build final limpo, realizado em diretório isolado para evitar interferência
da sincronização de arquivos temporários, concluiu 123 tarefas. Lint Release:
0 erros e 36 avisos. Foram conferidos a assinatura APK v2, o mesmo certificado
do Controlador 4.0 fornecido, a integridade do ZIP, as quatro bibliotecas em
duas ABIs, as licenças e o alinhamento ZIP para páginas de 16 KiB. O manifesto
solicita apenas INTERNET e ACCESS_NETWORK_STATE. As bibliotecas ELF de entrada
e as processadas pelo AGP estão identificadas separadamente no
[relatório de integridade](evidencias_6_1/apk-release-verification.json).

## Execução real no Android

A instrumentação executou **8 de 8 cenários** no mesmo APK final identificado
acima, em emulador Android API 35 x86_64. Foram usados o MeshTransport de
produção, a ponte JNI e o EasyTier 2.6.4 reais, um Host Python Desktop 6.1
independente e um retransmissor EasyTier local com P2P desativado.

Passaram a descoberta da rota e o túnel, identidade TLS antes da senha,
aprovação da sessão pelo Host de teste, decodificação JPEG Android e metadados
de múltiplos monitores, 15 eventos de comandos conferidos no Host, arquivos
multibloco em ambos os sentidos com bytes exatos, armazenamento cifrado no
AndroidKeyStore (IV novo, adulteração rejeitada e esquecimento) e encerramento
com remoção da instância nativa.

O transporte externo do laboratório foi **ADB reverso TCP local**
(`adb_reverse_local_tcp`). A rede Wi-Fi virtual do emulador não entregava TCP
ao computador de teste, inclusive em sondagem feita fora do aplicativo.
Esse canal permite verificar JNI, EasyTier, TLS e protocolo reais, mas não
comprova Wi-Fi físico, travessia de NAT ou retransmissores públicos.

Nos arquivos, ContentResolver.wrap, os descritores e a leitura/gravação são
Android reais, usando um ContentProvider local com bytes sintéticos. A
classificação de URI de documento é preparação do teste. Não foi exercitado
o seletor SAF, descoberta de provedor por Binder ou acesso a documentos do
usuário. O provedor de entrada do Host registra os comandos; não movimenta
mouse ou teclado de um Windows físico.

O [relatório nativo](evidencias_6_1/native-runtime-report.json) registra hashes
dos fontes, instrumentação, bibliotecas de entrada e bibliotecas incorporadas,
e confirma que o APK instalado corresponde ao APK local de entrega.

## Revisão da interface Android

A interface do APK final foi operada no emulador. O
[relatório de interface](evidencias_6_1/ui-runtime-report.json) lista somente
as verificações efetivamente executadas, com o hash do APK instalado.
Foram corrigidos a quebra confusa dos três passos, a sobreposição dos botões
flutuantes sobre Fechar nos painéis de tela cheia e o espaço insuficiente
para digitar com fonte grande e teclado aberto. Cabeçalho e navegação são
recolhidos durante a digitação; os botões flutuantes retornam ao fechar o painel.
As capturas foram usadas apenas para inspeção interna.

## Ensaios externos ainda não realizados

Não houve teste em celular físico ARM64, Windows físico, dois provedores
de internet ou retransmissores públicos. As bibliotecas ARM64 foram compiladas
e inspecionadas; a execução Android registrada usa x86_64. Não há garantia de
funcionamento perfeito em todo aparelho, velocidade mínima ou conexão
permanente. As limitações de ASCII, tamanho de arquivo e aprovação no Host
descritas acima continuam válidas.

O fallback de teclado em API 26–29 e o ciclo completo de reconexão automática
pela MainActivity não foram executados no Android. As regras puras de
recuperação foram testadas na JVM; isso não substitui ensaio do ciclo Android.
