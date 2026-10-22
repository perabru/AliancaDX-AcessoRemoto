# AliançaDX Acesso Remoto

Página de apresentação e código-fonte do **AliançaDX Acesso Remoto 6.2.0** para Windows e Android.
O Hospedeiro funciona no PC Windows que será acessado; o Controlador funciona no dispositivo de quem acessa. Cada sessão precisa ser autorizada no PC.

## Downloads e manual

| Material | Link | Uso |
| --- | --- | --- |
| Controlador Windows | [Abrir no Google Drive](https://drive.google.com/file/d/1f7FFuWFtlwJS-dY2iNdpWAA0TY900pRg/view?usp=sharing) | Acessar outro PC pelo Windows |
| Hospedeiro Windows | [Abrir no Google Drive](https://drive.google.com/file/d/1KryJxq7vmQHovoY76n59m5cO7NILXOuR/view?usp=sharing) | Receber o acesso no PC Windows |
| Controlador Android 6.2.1 | [Abrir no Google Drive](https://drive.google.com/file/d/1r7QwgsDlbiLbFPsD6zTuF3WQ9YFKWp9m/view?usp=sharing) | Acessar o PC pelo celular |
| Manual do usuário | [Abrir no Google Drive](https://drive.google.com/file/d/1cHQ3ASQqRthEv8ux9P06WgoJV41SyiAO/view?usp=sharing) | Instruções para Windows e Android |

Os links acima foram fornecidos pelo responsável pelo projeto. O download é feito no Google Drive e está sujeito às permissões e aos limites do serviço. O APK Android indicado é a versão 6.2.1, com SHA-256 `5d3d15137354f37bb1038e709c38672e0b5fd8dd033c2bb92c6227a7801dc590` e 34.716.022 bytes. A conferência de assinatura e integridade não constitui aprovação do Play Protect.

Requisitos: Windows 10/11 de 64 bits; Android 8 ou superior com sistema de 64 bits. O aplicativo Android é um **Controlador**: ele acessa o PC e não compartilha a tela do celular.

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
| `android/` | Aplicativo Android em Kotlin, integração JNI, Gradle, testes e documentação |
| `scripts/` | Validação e empacotamento da página |
| `.github/workflows/` | Publicação da página pelo GitHub Actions |

Os fontes autorais, recursos, scripts de compilação, testes, certificados **públicos** e avisos de terceiros estão incluídos. Chaves privadas, senhas, builds, executáveis, caches e SDKs não fazem parte deste checkout. As dependências nativas grandes são obtidas pelos procedimentos verificados descritos abaixo.

### Compilar os aplicativos

- **Windows:** consulte `desktop/README_FONTE_PUBLICO.md` e `desktop/installer/FONTES_DEPENDENCIAS.md`. O procedimento de preparação do cache recupera os componentes e os fontes de terceiros do instalador 6.2.0, verifica o SHA-256 e os manifestos antes do build. A compilação aceita Windows ou Linux com as ferramentas indicadas nesses documentos; a execução e a validação real dos instaladores exigem Windows.
- **Android:** consulte `android/README.md` e `android/native-sources/README.md`. O bootstrap de dependências recupera o EasyTier na revisão fixada e valida os arquivos pelos hashes registrados. As bibliotecas JNI são reconstruídas com o NDK antes de compilar o aplicativo. O assinador de continuidade em `android/signing/` exige as chaves oficiais fora do checkout. Para um aplicativo independente, siga as instruções de build de desenvolvimento ou de assinatura própria; uma chave diferente não reproduz a assinatura da distribuição.

Os documentos de cada plataforma descrevem os requisitos, os comandos e os limites de reprodutibilidade. O checkout público não contém a chave usada para assinar os APKs distribuídos. Uma compilação com outra chave não substitui automaticamente a instalação existente.

## Editar e publicar a página

Edite `index.html`. Os links de download ficam diretamente nas âncoras HTML; os arquivos de marca ficam em `assets/brand/`. A busca do assistente funciona no navegador, sem enviar o modelo pesquisado a um servidor. Não há cadastro, cookies próprios ou rastreamento na página.

```sh
python scripts/check_site.py
node --check assets/device-guide.js
node --test scripts/test_device_guide.cjs
python scripts/package_site.py
```

O último comando cria `_site/` com **somente a página e seus recursos públicos**. Os fontes dos aplicativos não são copiados para o artefato de hospedagem; continuam disponíveis neste repositório.

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
