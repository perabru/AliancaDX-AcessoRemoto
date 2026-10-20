# AliançaDX Acesso Remoto 6.2.0 — validação Android

19/10/2026. Aplicativo `br.com.perabru.minianydesk.controlador`, código62000,
versão6.2.0, Android8/API26 mínimo e API36 de compilação/alvo. O nome exibido
é **AliançaDX Acesso Remoto**. Requer aparelho64bits (arm64-v8a; x86_64 de teste).

## Identidade e continuidade

O logo JPEG original foi preservado. O símbolo foi reconstituído em vetor para
ícones adaptativos, incluindo variante monocromática Android13+. A interface
usa branco, verde esmeralda, detalhes verde vivo e títulos verde escuro.
Botões com texto branco têm contraste5,39:1; títulos sobre branco,12,62:1.

Pacote, preferências, identificadores de computadores/pins, alias AndroidKeyStore
e protocolo3.0.0/MDA6 continuam os mesmos. Os clientes novos aceitam convites
AliançaDX e os antigos MiniAnyDesk. Para cliente antigo, use código MDA6 isolado
e senha separadamente; seu parser antigo pode recusar o novo cabeçalho completo.

A assinatura conserva exatamente os certificados e a linhagem da6.1.1:

- Legado: `266dd27bd7af6914f0ddaebbbbbe30febb056c17939a94a8440fa6a33750b3d0`.
- Produção: `1ddd6f2a1a82862a1a3a8a53cbbc257a8fc499bb5a500b8ebc0929792e14c2f1`.
- Linhagem: `d07ca55a030150152bf99d6194c8beb71d3915313662307518853f34c3988486`.

A chave antiga foi exposta nos fontes6.1.0 e continua necessária nas APIs26–32.
APIs33+ usam a chave de produção. Essa limitação permanece; a mudança de marca
não a revoga nem autentica retroativamente APKs antigos. O backup privado já
entregue na6.1.1 continua sendo o correto; nenhuma chave nova foi gerada aqui.

## Compilação e APK final

Clean Release+lint passou com JDK17, Gradle8.11.1, AGP8.10.1, Kotlin2.1.20 e
BuildTools35. Foram55 tarefas acionáveis,54 executadas e1 atualizada, em1m16s.
Os50 arquivos de entrada do snapshot ficaram iguais aos fontes finais.

APK `AliancaDX_Acesso_Remoto_6.2.0.apk`,34.699.638 bytes. SHA-256:
`3ff081e7c6244be6c07d5a45d658f05c845d052bbc7064deec8b736e89379f3c`.

Lint: **0 erros e37 avisos**, sem novas supressões. Categorias:19SetTextI18n,
6DrawAllocation,3UseRequiresApi,3CustomX509TrustManager,3UnusedResources,
1UnusedAttribute,1IconLauncherShape e1MonochromeLauncherIcon. Quatro avisos
adicionais nesta edição dizem respeito a recursos de ícone/cor não usados e à
variante base do ícone; a varianteAPI33 contém a versão monocromática.
Os avisos TLS correspondem ao modelo de pin e aprovação do Host, já documentado.

Não houve recompilação do componente nativo. As quatro bibliotecas no APK
coincidem com os fontes binários e a proveniência; ELF e offsets ZIP têm
alinhamento16KiB. O logo original foi conferido byte a byte no APK. O manifesto
contém somente INTERNET e ACCESS_NETWORK_STATE, sem debug/testOnly, backup,
tráfego em texto claro, serviços ou permissão de acessibilidade.

## Verificações

O pipeline oficial apksigner/apksig validou a assinatura real e os certificados
nos intervalos26–32,33–37,26–37 e nas APIs32,33,36 e37 isoladas, com `-Werr`.
Isso é validação estática, sem instalações nesses sistemas. A limitação do
exame artificialmente restrito somente à API26 está em `signing/LEIA-ME.md`.
Os12 testes da ferramenta de assinatura passaram; usam ferramentas simuladas
para falhas/credenciais, separados da verificação criptográfica real.

A revisão independente do APK atual está em `docs/evidencias_6_2_0/`, junto
das evidências de comunicação e compatibilidade. Os resultados6.1.1/6.1.0
permanecem históricos, sem serem contados como novos. Os relatórios atuais
indicam ambiente e doubles/fixtures quando utilizados.

## Limites

Não houve instalação, revisão visual, atualização real nem ensaio AndroidKeyStore
em aparelho Android nesta rodada. A tentativa da edição anterior ficou limitada
pelo boot do emulador sem KVM; não foi repetida como um teste supostamente novo.
A interface Android foi compilada e revisada nos fontes, sem imagem real de tela
validada nesta edição. Não houve Play Protect, publicação ou aprovação Google.

A assinatura não garante ausência de avisos. A comunicação entre provedores
físicos distintos, relays comunitários e funcionamento contínuo em toda rede
não foram validados aqui. São mantidos o limite100MiB por arquivo e o suporte
de texto do Host Windows. Consulte `PRIVACIDADE.md` e a orientação de distribuição.
