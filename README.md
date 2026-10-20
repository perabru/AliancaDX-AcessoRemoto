# AliançaDX Acesso Remoto

Página de apresentação e código-fonte do **AliançaDX Acesso Remoto 6.2.0** para Windows e Android.
O Hospedeiro funciona no PC Windows que será acessado; o Controlador funciona no dispositivo de quem acessa. Cada sessão precisa ser autorizada no PC.

## Downloads e manual

| Material | Link | Uso |
| --- | --- | --- |
| Controlador Windows | [Abrir no Google Drive](https://drive.google.com/file/d/1f7FFuWFtlwJS-dY2iNdpWAA0TY900pRg/view?usp=sharing) | Acessar outro PC pelo Windows |
| Hospedeiro Windows | [Abrir no Google Drive](https://drive.google.com/file/d/1KryJxq7vmQHovoY76n59m5cO7NILXOuR/view?usp=sharing) | Receber o acesso no PC Windows |
| Manual do usuário | [Abrir no Google Drive](https://drive.google.com/file/d/1cHQ3ASQqRthEv8ux9P06WgoJV41SyiAO/view?usp=sharing) | Instruções para Windows e Android |

Os links acima foram fornecidos pelo responsável pelo projeto. O download é feito no Google Drive e está sujeito às permissões e aos limites do serviço. Não foi fornecido um link público para o APK Android; o código Android está neste repositório.

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
| `assets/install-assistant.js`, `assets/install-assistant.css` | Busca de guias Android e identificação USB opcional |
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
node --check assets/install-assistant.js
node --test scripts/test_install_assistant.cjs
python scripts/package_site.py
```

O segundo comando cria `_site/` com **somente a página e seus recursos públicos**. Os fontes dos aplicativos não são copiados para o artefato de hospedagem; continuam disponíveis neste repositório.

Para ativar o GitHub Pages pela primeira vez, em **Settings → Pages → Build and deployment → Source**, selecione **GitHub Actions**. O workflow `Publicar GitHub Pages` publica a página a cada alteração em `main` e pode ser executado manualmente. A URL de destino prevista é `https://perabru.github.io/AliancaDX-AcessoRemoto/`; confirme o endereço e o sucesso da publicação na aba Actions/Pages.

O workflow usa as ações oficiais de Pages, sem PAT ou segredo adicional. A ativação inicial do Pages depende de uma conta com permissão de administração; o `GITHUB_TOKEN` do workflow não habilita o serviço por conta própria.

### Assistente de instalação no Android

A seção **Instalar no Android** permite pesquisar a marca, família ou modelo para selecionar um guia. O resultado é uma orientação da marca: a versão do Android e a interface do fabricante podem mudar os nomes dos menus. Quando não há correspondência, o visitante pode escolher a marca ou usar o guia geral.

No Android 8 ou superior, a permissão de instalar um APK é concedida ao aplicativo que o abre, por exemplo Chrome ou Meus Arquivos. O site mostra os passos para a pessoa decidir nas configurações do celular. O aviso de origem não autorizada e uma classificação do Play Protect têm procedimentos diferentes; conceder a permissão de origem não elimina um bloqueio do Play Protect.

O botão USB usa somente `navigator.usb.requestDevice()` e os descritores públicos do dispositivo selecionado: fabricante, nome do produto e identificadores de fabricante/produto. Não abre uma interface USB, não usa ADB, não lê número de série, não instala software, não altera permissões e não verifica se a instalação já foi autorizada. Nenhum desses dados é enviado pelo código da página ou salvo entre visitas. Quando disponível, a opção de esquecer o dispositivo revoga a autorização USB do site.

A tentativa USB depende de HTTPS, de um clique e da seleção do dispositivo no navegador. Use um navegador de computador com WebUSB disponível, como versões compatíveis de Chrome ou Edge. Drivers, aparelhos e políticas do navegador podem impedir que o celular apareça; o nome USB pode ser genérico. Nesses casos, use a busca manual. Não é necessário ativar a depuração USB para este recurso.

As orientações citam os suportes oficiais de [Google](https://support.google.com/pixelphone/answer/7391672?hl=pt-BR), [Samsung](https://www.samsung.com/ae/support/mobile-devices/how-to-enable-permission-to-install-apps-from-unknown-source-on-my-samsung-phone/) e [Xiaomi](https://www.mi.com/global/support/article/KA-33144/). A documentação de [WebUSB](https://developer.chrome.com/docs/capabilities/usb) e da ação nativa [ACTION_MANAGE_UNKNOWN_APP_SOURCES](https://developer.android.com/reference/android/provider/Settings#ACTION_MANAGE_UNKNOWN_APP_SOURCES) descreve as confirmações e os limites das APIs. Não há uma API de página web que conceda essa permissão de instalação no Android.

Para validar em equipamento real: procure seu modelo, confira o app de origem e compare o guia com a versão do sistema; no computador, teste seleção, cancelamento, nomes genéricos e remoção do cabo. As verificações automatizadas usam descritores e respostas simulados; não substituem uma conexão com um celular real.

## Segurança e licenças

Utilize o acesso remoto somente com autorização. As confirmações no Hospedeiro e as verificações de identidade da conexão fazem parte do funcionamento do aplicativo. Não publique convites, senhas, arquivos de assinatura ou dados de uma sessão em issues ou commits.

Os instaladores Windows desta edição não possuem assinatura Authenticode. Assinatura de APK, publicação dos fontes e hospedagem no GitHub não constituem aprovação pelo Microsoft Defender, Google Play ou Play Protect, nem garantem ausência de alertas.

A identidade AliançaDX e os materiais originais mantêm seus direitos existentes. Esta publicação não acrescenta uma licença global aos fontes autorais. As dependências mantêm suas próprias licenças, avisos e obrigações; consulte os arquivos de licenças e os manifestos em cada plataforma, incluindo os fontes correspondentes LGPL e as instruções de reconstrução.
