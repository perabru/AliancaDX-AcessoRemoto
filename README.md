# AliançaDX Acesso Remoto

Página de apresentação e código-fonte do **AliançaDX Acesso Remoto**: Windows, Android e PWA para iPhone/iPad **7.6.1**.
O Hospedeiro funciona no PC Windows que será acessado; o Controlador funciona no dispositivo de quem acessa. Cada sessão precisa ser autorizada no PC.

## Downloads e manual

| Material | Link | Uso |
| --- | --- | --- |
| Controlador Windows | [Baixar pelo GitHub Releases](https://github.com/perabru/AliancaDX-AcessoRemoto/releases/download/v7.6.1/AliancaDX_Controlador_Setup.exe) | Acessar outro PC pelo Windows |
| Hospedeiro Windows | [Baixar pelo GitHub Releases](https://github.com/perabru/AliancaDX-AcessoRemoto/releases/download/v7.6.1/AliancaDX_Host_Setup.exe) | Receber o acesso no PC Windows |
| Controlador Android 7.6.1 | [Baixar pelo GitHub Releases](https://github.com/perabru/AliancaDX-AcessoRemoto/releases/download/v7.6.1/AliancaDX_Acesso_Remoto_7.6.1.apk) | Acessar o PC pelo celular |
| iPhone e iPad · PWA 7.6.1 | [Abrir a PWA](https://perabru.github.io/AliancaDX-AcessoRemoto/ios-pwa/) | Acessar o PC pelo Safari ou pela Tela de Início |
| Arquivos da PWA 7.6.1 | [Baixar pacote ZIP](https://github.com/perabru/AliancaDX-AcessoRemoto/releases/download/v7.6.1/AliancaDX_iOS_PWA_7.6.1.zip) | Arquivos públicos da versão publicada |
| Guia iPhone/iPad | [Abrir o passo a passo](docs/IOS_6_3_0.md) | Instalação e pareamento com o Windows |
| Manual do usuário 7.6.1 | [Abrir PDF direto](https://perabru.github.io/AliancaDX-AcessoRemoto/docs/manual/Manual_AliancaDX.pdf) · [Ver no GitHub](docs/manual/Manual_AliancaDX.pdf) | 44 páginas: Windows, Android, iPhone e iPad |

Os instaladores Windows 7.6.1 e os controles móveis 7.6.1 estão no GitHub Releases; a PWA funciona também pelo link acima. O manual atualizado de 44 páginas tem download direto no site e explica o mousepad de tela inteira e os botões de mouse. Os textos e a diagramação estão em `docs/manual/`. O APK Android é a versão 7.6.1, com SHA-256 `f0273184969fc2d4fda0946b87f2ab81da228a13c7771470f5a502f95b185630` e 34.781.558 bytes. A conferência de assinatura e integridade não constitui aprovação do Play Protect.

Requisitos: Windows 10/11 de 64 bits; Android 8 ou superior com sistema de 64 bits. O aplicativo Android e a PWA para iPhone/iPad são **Controladores**: acessam o PC e não compartilham a tela do celular.

**Qualidade automática e resposta mais rápida (7.6.1):** a transmissão acompanha os tempos reais de envio, decodificação e apresentação. Com uma conexão estável e disponível, recupera nitidez, resolução e fluidez gradualmente; quando a conexão piora ou o dispositivo fica sobrecarregado, reduz o peso das imagens e a quantidade de quadros em trânsito. A recuperação exige estabilidade para evitar oscilações. Textos e cores recebem maior detalhe nos perfis superiores, e as filas ficam limitadas aos quadros recentes. **Atualize o Hospedeiro e o Controlador** para aproveitar o conjunto completo. A medição não é um teste da capacidade total da internet, e latência zero não é possível. [Implementação, medições e limites da versão 7.6](desktop/docs/quality-7.6/README.md).

## Tela bem ajustada e ícone próprio no Windows (7.6.1)

**Ajustar** agora ocupa a maior área disponível mantendo a proporção, inclusive quando a transmissão recebe quadros pequenos. Isso corrige a miniatura no centro da tela no Android, Windows e PWA. O enquadramento acompanha a rotação, a tela cheia e o tamanho da janela. No celular, use **Ajustar → Tela cheia** e gire na horizontal para aproveitar melhor a largura. Barras vazias podem continuar quando as proporções das telas são diferentes.

**100%** conserva a escala de um pixel recebido por pixel da tela e pode mostrar uma imagem menor que Ajustar. Zoom amplia os pixels disponíveis, sem recriar detalhes. O Windows passa a usar o ícone AliançaDX nas janelas, nos atalhos e na barra de tarefas. Depois de atualizar, abra pelo atalho AliançaDX; se um atalho antigo fixado ainda mostrar Python, desafixe-o e fixe a janela atualizada.

## Buscar atualização (7.6.1)

Use **Buscar atualização** no Controlador ou Hospedeiro Windows, no Android, na PWA do iPhone/iPad ou no Conector para iPhone. A consulta acontece quando você pede e compara a versão instalada com a última versão estável publicada neste repositório. A janela mostra o resultado ou explica a falha e permite tentar novamente.

Quando houver novidade, use **Atualizar agora**. O Windows e o Android baixam o pacote oficial internamente, conferem sua integridade e preparam a instalação sobre a versão existente. A janela mostra andamento, cancelamento e erros. A instalação aguarda o encerramento das conexões e transferências. O sistema pode exigir uma autorização ou confirmação; no Android compatível, a própria atualização pode prosseguir automaticamente.

**Primeiro acesso ao recurso:** quem usa 7.5 ou anterior precisa instalar a versão atual uma vez; o instalador interno está disponível desde a 7.6. As próximas atualizações poderão ser iniciadas e instaladas pelo próprio aplicativo, sem procurar manualmente outro arquivo.

Na PWA publicada, encerre as sessões antes de aplicar uma atualização preparada. Se a nova versão ainda estiver sendo publicada no site, aguarde e consulte novamente. Para a PWA e o Conector incorporados ao Windows, use **Buscar atualização → Atualizar agora** no Hospedeiro e depois reabra o Conector. A consulta não envia senhas, convites nem informações das conexões ao GitHub.

## Conexões salvas na versão 7

No Windows, marque **Salvar este acesso em Meus computadores** ao continuar com o convite. No Android, marque **Lembrar código privado neste celular** e toque em **Salvar acesso agora**. O cartão fica disponível antes do primeiro acesso autorizado.

Em **Seus computadores** ou **Salvos**, escolha **Conectar/Reconectar**: o código protegido é recuperado e a senha atual é solicitada. **Abrir sessão** retorna a um acesso já aberto. A senha não é gravada e o PC continua aprovando cada conexão. Se o Host renovar o convite, use **Atualizar código** no mesmo cartão para preservar seu apelido e histórico. Reiniciar o Host pode mudar a senha automática sem invalidar o código guardado quando os dados, certificado e porta são mantidos.

Na PWA, favoritos guardam apenas nomes e preferências locais. Uma sessão encerrada requer novo convite, resposta e autorização; convites e senhas não são persistidos.

## Texto mais nítido e visualização em 100% (7.6.1)

Atualize o **Hospedeiro e o Controlador**. O modo **Automático** começa com mais detalhes; **Mais nitidez** pode chegar a 4K quando o monitor, a rede e o dispositivo permitem. Textos e interfaces podem usar imagem sem perdas quando ela cabe nos limites da sessão; conteúdos mais pesados continuam com compressão adaptativa. Isso preserva melhor os pixels disponíveis e reduz o volume de dados em telas de texto, conforme o conteúdo. Se o computador estiver ocupado, a transmissão pode reduzir a frequência dos quadros antes de reduzir a resolução; a conexão continua se adaptando quando a rede piora.

No Windows, use **Ver 100%** para examinar a imagem recebida sem reduzi-la e navegue pelas barras. **Ajustar tela** volta à visão completa. No Android e na PWA, abra a engrenagem de imagem e escolha **100%** ou **Ajustar**; amplie mais para ler no celular e use dois dedos para mover a área visível. Um dedo continua controlando o mousepad. Cada sessão tem sua própria visualização.

Ajustar amplia ou reduz a imagem para caber inteira na área disponível, mantendo a proporção. A visualização em 100% conserva os pixels disponíveis, e ampliar facilita a leitura; nenhuma ampliação recupera detalhes que não foram enviados. Se o aparelho precisar reduzir a imagem por limite de memória ou de tela, o aplicativo informa a limitação. Compartilhar um monitor de cada vez também facilita a leitura.

[Comparativo de nitidez, imagens de teste e medições da versão 7.6](desktop/docs/quality-7.6/README.md).

## Engrenagem de imagem e resposta (7.6.1)

Durante a sessão, abra a **engrenagem** e escolha **Automático**, **Resposta rápida** ou **Mais nitidez**. A troca acontece sem reconectar e pertence à sessão selecionada. **Em uso** mostra o modo confirmado por uma imagem recebida do Hospedeiro; enquanto aguarda, a interface informa que está aplicando a escolha.

- **Automático:** equilibra nitidez e resposta, adaptando a imagem conforme a conexão.
- **Resposta rápida:** prioriza menor atraso, podendo reduzir a resolução e os detalhes.
- **Mais nitidez:** prioriza detalhes e cores, aceitando mais dados e possível atraso.

Os três modos mantêm limites de transmissão e podem reduzir o peso da imagem quando necessário. Atualize o **Hospedeiro e o Controlador para 7.6.1**; na PWA, atualize também o Conector Windows. Versões anteriores continuam conectando, mas a engrenagem informa quando não podem aplicar os modos. A preferência local não salva senha nem dispensa a autorização no PC.

## Velocidade da sessão e tela cheia

Os aplicativos mostram o tráfego recente de recebimento (↓) e envio (↑) em Mb/s, por sessão. A medição é dos dados da aplicação e não testa a capacidade total da internet. No celular em tela cheia, **Ocultar comandos** recolhe os controles e uma pequena alça **Mostrar comandos** permite restaurá-los. No Controlador Windows, **F8** oculta ou mostra os comandos e **Esc** sai da tela cheia.

## Vários PCs e botões de mouse

- **Windows:** use **Nova conexão** para abrir até **8 janelas independentes**. O seletor traz a conexão escolhida para frente.
- **Android:** toque em **+** para manter até **4 sessões** e use as abas para alternar. Em tela cheia, use **Sessões**.
- **Android e PWA:** os botões **Clique esquerdo**, **Clique direito**, **Clique duplo** e **Arrastar** formam um controle translúcido de mouse. O mousepad ocupa toda a área da tela remota e o marcador mostra a posição do cursor. Os comandos se ajustam à tela cheia e à posição horizontal.
- Cada PC exige autorização própria. Encerrar uma sessão preserva as outras; os arquivos e comandos pertencem ao PC que os iniciou.

[Passo a passo das sessões no Windows e Android](desktop/docs/SESSOES_6_2_2.md). O Hospedeiro continua aceitando uma sessão autorizada por vez no mesmo PC.

**Mousepad no celular e no tablet (7.6.1):** deslize um dedo em qualquer parte da área remota para mover o cursor de forma relativa, como no touchpad de um notebook. Levante e reposicione o dedo para continuar o movimento sem saltos. Tocar ou manter o dedo parado não clica nem inicia um arraste. Use os botões para os cliques. Para arrastar, ative **Arrastar**, mova pelo mousepad e toque novamente no botão para soltar. Com dois dedos, ajuste o zoom e mova a imagem. O mouse é liberado ao interromper o gesto ou trocar de sessão. Ocultar os comandos mantém o mousepad disponível; a alça restaura os botões.

## iPhone e iPad pela PWA

1. No Safari, abra a [PWA AliançaDX](https://perabru.github.io/AliancaDX-AcessoRemoto/ios-pwa/). Para instalar, use **Compartilhar → Adicionar à Tela de Início**.
2. No PC, instale o **Hospedeiro Windows 7.6.1**, inicie o atendimento e clique em **iPhone e iPad**. Mantenha o Conector aberto no Edge ou Chrome.
3. Gere o convite no Conector. Abra o link ou QR code no iPhone, toque em conectar e devolva a resposta ao Conector por cópia, compartilhamento ou arquivo.
4. Aplique a resposta no PC e autorize o pedido no Hospedeiro. Somente essa autorização libera tela, mouse, teclado e arquivos.

A PWA oferece clique duplo, clique direito, zoom, teclado, seleção de monitor, até quatro sessões e transferência de arquivos de até **25 MiB**. O Conector usa a sessão autenticada do aplicativo Windows; a senha não vai no convite da PWA. O Controlador Windows também pode abrir um Conector para outro Hospedeiro.

O pareamento inicial é manual e a conexão usa WebRTC criptografado. O serviço STUN ajuda a encontrar o caminho de rede; redes com NAT ou firewall restritivo podem precisar de TURN configurado pelo responsável ou de outra conexão. Não há garantia de conexão em todas as redes. Mantenha o iPhone em primeiro plano e o navegador do PC aberto; o iOS pode suspender o acesso em segundo plano. O modo offline guarda a interface e as instruções, não permite controlar um PC sem conexão.

Esta é uma PWA, sem arquivo IPA ou publicação na App Store. Consulte o [guia completo e os limites da validação](docs/IOS_6_3_0.md).

## Comece por aqui

1. Instale e abra o **Hospedeiro** no PC que receberá o acesso.
2. Clique em **Copiar convite** e envie o convite somente à pessoa autorizada.
3. Abra o **Controlador** no outro dispositivo, cole o convite, confira a senha e conecte.
4. No PC, confira o pedido e autorize a sessão. Mantenha os aplicativos abertos durante o atendimento.

No Controlador Windows, colar um convite importa o código; a senha é informada manualmente. No Android, um convite completo também pode preencher a senha. Consulte o manual antes de sua primeira sessão.

## Organização dos fontes

| Caminho | Conteúdo |
| --- | --- |
| `index.html` | Página estática, estilos e navegação, sem dependências externas |
| `assets/brand/` | Logo original, ícones, cores e imagem de compartilhamento |
| `assets/device-guide.js`, `assets/device-guide.css` | Consulta USB de descritores e tutorial por marca em quatro janelas |
| `desktop/` | Aplicativos Windows em Python, instalador NSIS, testes e documentação |
| `ios-pwa/` | PWA para iPhone/iPad, ponte WebRTC no navegador do PC, testes e recursos públicos |
| `android/` | Aplicativo Android em Kotlin, integração JNI, Gradle, testes e documentação |
| `scripts/` | Validação e empacotamento da página |
| `.github/workflows/` | Publicação da página e dos pacotes verificados pelo GitHub Actions |

Os fontes autorais, recursos, scripts de compilação, testes, certificados **públicos** e avisos de terceiros estão incluídos. Chaves privadas, senhas, builds, executáveis, caches e SDKs não fazem parte deste checkout. As dependências nativas grandes são obtidas pelos procedimentos verificados descritos abaixo.

### Compilar os aplicativos

- **Windows:** consulte `desktop/README_FONTE_PUBLICO.md` e `desktop/installer/FONTES_DEPENDENCIAS.md`. O procedimento de preparação do cache recupera os componentes e os fontes de terceiros de instaladores com hashes publicados (6.2.1 e 6.2.0), verifica o SHA-256 e os manifestos antes do build. A compilação aceita Windows ou Linux com as ferramentas indicadas nesses documentos; a execução e a validação real dos instaladores exigem Windows.
- **Android:** consulte `android/README.md` e `android/native-sources/README.md`. O bootstrap de dependências recupera o EasyTier na revisão fixada e valida os arquivos pelos hashes registrados. As bibliotecas JNI são reconstruídas com o NDK antes de compilar o aplicativo. O assinador de continuidade em `android/signing/` exige as chaves oficiais fora do checkout. Para um aplicativo independente, siga as instruções de build de desenvolvimento ou de assinatura própria; uma chave diferente não reproduz a assinatura da distribuição.

Os documentos de cada plataforma descrevem os requisitos, os comandos e os limites de reprodutibilidade. O checkout público não contém a chave usada para assinar os APKs distribuídos. Uma compilação com outra chave não substitui automaticamente a instalação existente.

## Editar e publicar a página

Edite `index.html`. Os links de download ficam diretamente nas âncoras HTML; os arquivos de marca ficam em `assets/brand/`. A busca do assistente funciona no navegador, sem enviar o modelo pesquisado a um servidor. Não há cadastro, cookies próprios ou rastreamento na página.

```sh
python scripts/check_site.py
node --check assets/device-guide.js
node --test scripts/test_device_guide.cjs
node --test ios-pwa/tests/*.test.mjs
python -m unittest discover -s scripts -p test_package_site.py
python scripts/package_site.py
```

O último comando cria `_site/` com **somente a página e seus recursos públicos**, incluindo a PWA conforme o inventário explícito `ios-pwa/public-assets.json`. Os fontes dos aplicativos não são copiados para o artefato de hospedagem; continuam disponíveis neste repositório.

Para ativar o GitHub Pages pela primeira vez, em **Settings → Pages → Build and deployment → Source**, selecione **GitHub Actions**. O workflow `Publicar GitHub Pages` publica a página a cada alteração em `main` e pode ser executado manualmente. A URL de destino prevista é `https://perabru.github.io/AliancaDX-AcessoRemoto/`; confirme o endereço e o sucesso da publicação na aba Actions/Pages.

O workflow usa as ações oficiais de Pages, sem PAT ou segredo adicional. A ativação inicial do Pages depende de uma conta com permissão de administração; o `GITHUB_TOKEN` do workflow não habilita o serviço por conta própria.

### Guia do celular por USB ou por marca

A seção **Seu Android, passo a passo** consulta as informações USB que o aparelho disponibiliza ou permite pesquisar a marca/modelo manualmente. O tutorial abre em um diálogo com quatro etapas: conferir o aparelho, ativar as opções do desenvolvedor, encontrar a depuração USB e encerrar a atividade nas configurações do celular. As marcas com guias específicos são Samsung, Xiaomi/Redmi/POCO, Motorola e Google Pixel. Outras marcas recebem o guia geral, identificado como tal.

O USB é somente uma consulta de descritores, iniciada em um clique explícito. O código lê apenas `manufacturerName`, `productName`, `vendorId` e `productId` do dispositivo escolhido. Não chama `open`, não assume uma interface, não usa ADB, não lê o número de série ou o IMEI e não instala ou transfere arquivos. A opção de instalação por depuração e seu SDK foram removidos. A consulta não depende de selecionar um APK, ativar a depuração ou aceitar uma chave RSA.

O nome e o modelo são apresentados como informações fornecidas pelo USB. O par VID:PID identifica fabricante/produto USB e pode ser compartilhado por vários celulares; não é um identificador único do aparelho. Descritores genéricos, marcas conflitantes ou nomes ausentes pedem confirmação manual. Um VID Google não é tratado, sozinho, como prova de que o celular é um Pixel. A consulta não comprova a versão do Android nem o estado das configurações do desenvolvedor.

Use HTTPS e um navegador de computador com WebUSB, como versões compatíveis do Chrome ou Edge. Políticas do navegador, cabos e aparelhos podem impedir a consulta. Falhas e cancelamento mantêm a escolha manual disponível. A autorização é restrita ao dispositivo selecionado. Quando o navegador oferece `forget`, há uma ação explícita para esquecê-la. Nenhuma informação de aparelho é enviada a um servidor ou salva entre visitas.

O diálogo usa controles Voltar/Próximo/Concluir, fechamento por botão ou Escape e devolução do foco ao controle que o abriu. A animação respeita `prefers-reduced-motion`. O guia de leitura em HTML continua disponível sem JavaScript e serve de alternativa quando o navegador não oferece diálogos nativos. A seleção no navegador é nativa; o site não inventa uma janela de dispositivos nem confirma mudanças que só o usuário pode fazer no Android.

Os tutoriais apontam fontes oficiais de [Android](https://developer.android.com/studio/debug/dev-options), [Samsung](https://www.samsung.com/uk/support/mobile-devices/how-do-i-turn-on-the-developer-options-menu-on-my-samsung-galaxy-device/), [Xiaomi MIUI](https://www.mi.com/global/support/article/KA-06515/), [Xiaomi HyperOS](https://www.mi.com/global/support/faq/details/KA-07878/) e [Motorola](https://help.motorola.com/hc/apps/service/rsa/58/en-us/CG1590266220.html). Os nomes dos menus variam com o sistema e o idioma. Ativar ou desligar as opções é uma ação manual no próprio celular; o tutorial não informa que foi concluída por USB.

A verificação de hash do APK permanece como referência de download: a página mostra uma versão abreviada e permite copiar ou visualizar os 64 caracteres completos. Não há envio nem instalação do APK pelo assistente.

Os testes usam descritores e DOM simulados para conferir busca, tutoriais, seleção/cancelamento, navegação, cópia e foco. Não representam validação em um celular físico. Para testar em equipamento real, compare a marca/modelo informado com o aparelho, confira os menus da versão instalada e teste a recusa, a remoção do cabo e a autorização do navegador.

## Segurança e licenças

Utilize o acesso remoto somente com autorização. As confirmações no Hospedeiro e as verificações de identidade da conexão fazem parte do funcionamento do aplicativo. Não publique convites, senhas, arquivos de assinatura ou dados de uma sessão em issues ou commits.

Os instaladores Windows desta edição não possuem assinatura Authenticode. Assinatura de APK, publicação dos fontes e hospedagem no GitHub não constituem aprovação pelo Microsoft Defender, Google Play ou Play Protect, nem garantem ausência de alertas.

A identidade AliançaDX e os materiais originais mantêm seus direitos existentes. Esta publicação não acrescenta uma licença global aos fontes autorais. As dependências mantêm suas próprias licenças, avisos e obrigações; consulte os arquivos de licenças e os manifestos em cada plataforma, incluindo os fontes correspondentes LGPL e as instruções de reconstrução.
