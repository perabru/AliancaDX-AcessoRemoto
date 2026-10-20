# AliançaDX Acesso Remoto

Página de apresentação e código-fonte do **AliançaDX Acesso Remoto 6.2.0** para Windows e Android.
O Hospedeiro funciona no PC Windows que será acessado; o Controlador funciona no dispositivo de quem acessa. Cada sessão precisa ser autorizada no PC.

## Downloads e manual

| Material | Link | Uso |
| --- | --- | --- |
| Controlador Windows | [Abrir no Google Drive](https://drive.google.com/file/d/1f7FFuWFtlwJS-dY2iNdpWAA0TY900pRg/view?usp=sharing) | Acessar outro PC pelo Windows |
| Hospedeiro Windows | [Abrir no Google Drive](https://drive.google.com/file/d/1KryJxq7vmQHovoY76n59m5cO7NILXOuR/view?usp=sharing) | Receber o acesso no PC Windows |
| Controlador Android 6.2.0 | [Abrir no Google Drive](https://drive.google.com/file/d/1r7QwgsDlbiLbFPsD6zTuF3WQ9YFKWp9m/view?usp=sharing) | Acessar o PC pelo celular |
| Manual do usuário | [Abrir no Google Drive](https://drive.google.com/file/d/1cHQ3ASQqRthEv8ux9P06WgoJV41SyiAO/view?usp=sharing) | Instruções para Windows e Android |

Os links acima foram fornecidos pelo responsável pelo projeto. O download é feito no Google Drive e está sujeito às permissões e aos limites do serviço. O APK Android indicado é a versão 6.2.0, com SHA-256 `3ff081e7c6244be6c07d5a45d658f05c845d052bbc7064deec8b736e89379f3c` e 34.699.638 bytes. O assistente USB recusa qualquer arquivo diferente. A conferência de assinatura e integridade não constitui aprovação do Play Protect.

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
| `assets/usb-install-assistant.js`, `assets/usb-install-assistant.css` | Instalação USB avançada, confirmação, progresso e erros |
| `src/usb-adb-transport.js`, `tools/web-adb_build/` | Adaptador ADB limitado e construção reproduzível do módulo local |
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
node --test scripts/test_usb_install_assistant.cjs
npm ci --ignore-scripts --prefix tools/web-adb_build
node --test tools/web-adb_build/transport.test.mjs tools/web-adb_build/transport-sdk.test.mjs
python scripts/package_site.py
```

O segundo comando cria `_site/` com **somente a página e seus recursos públicos**. Os fontes dos aplicativos não são copiados para o artefato de hospedagem; continuam disponíveis neste repositório.

Para ativar o GitHub Pages pela primeira vez, em **Settings → Pages → Build and deployment → Source**, selecione **GitHub Actions**. O workflow `Publicar GitHub Pages` publica a página a cada alteração em `main` e pode ser executado manualmente. A URL de destino prevista é `https://perabru.github.io/AliancaDX-AcessoRemoto/`; confirme o endereço e o sucesso da publicação na aba Actions/Pages.

O workflow usa as ações oficiais de Pages, sem PAT ou segredo adicional. A ativação inicial do Pages depende de uma conta com permissão de administração; o `GITHUB_TOKEN` do workflow não habilita o serviço por conta própria.

### Assistente de instalação no Android

A seção **Instalar no Android** permite pesquisar a marca, família ou modelo para selecionar um guia. O resultado é uma orientação da marca: a versão do Android e a interface do fabricante podem mudar os nomes dos menus. Quando não há correspondência, o visitante pode escolher a marca ou usar o guia geral.

No Android 8 ou superior, a permissão de instalar um APK é concedida ao aplicativo que o abre, por exemplo Chrome ou Meus Arquivos. O site mostra os passos para a pessoa decidir nas configurações do celular. O aviso de origem não autorizada e uma classificação do Play Protect têm procedimentos diferentes; conceder a permissão de origem não elimina um bloqueio do Play Protect.

O botão **Consultar celular por USB** do guia de marcas usa somente `navigator.usb.requestDevice()` e os descritores públicos do dispositivo selecionado: fabricante, nome do produto e identificadores de fabricante/produto. Essa consulta não abre uma interface USB, não usa ADB, não lê número de série, não instala software, não altera permissões e não verifica se a instalação já foi autorizada. Nenhum desses dados é enviado pelo código da página ou salvo entre visitas. Quando disponível, a opção de esquecer o dispositivo revoga a autorização USB do site.

A tentativa USB depende de HTTPS, de um clique e da seleção do dispositivo no navegador. Use um navegador de computador com WebUSB disponível, como versões compatíveis de Chrome ou Edge. Drivers, aparelhos e políticas do navegador podem impedir que o celular apareça; o nome USB pode ser genérico. Nesses casos, use a busca manual. Não é necessário ativar a depuração USB para este recurso.

As orientações citam os suportes oficiais de [Google](https://support.google.com/pixelphone/answer/7391672?hl=pt-BR), [Samsung](https://www.samsung.com/ae/support/mobile-devices/how-to-enable-permission-to-install-apps-from-unknown-source-on-my-samsung-phone/) e [Xiaomi](https://www.mi.com/global/support/article/KA-33144/). A documentação de [WebUSB](https://developer.chrome.com/docs/capabilities/usb) e da ação nativa [ACTION_MANAGE_UNKNOWN_APP_SOURCES](https://developer.android.com/reference/android/provider/Settings#ACTION_MANAGE_UNKNOWN_APP_SOURCES) descreve as confirmações e os limites das APIs. Não há uma API de página web que conceda essa permissão de instalação no Android.

Para validar em equipamento real: procure seu modelo, confira o app de origem e compare o guia com a versão do sistema; no computador, teste seleção, cancelamento, nomes genéricos e remoção do cabo. As verificações automatizadas usam descritores e respostas simulados; não substituem uma conexão com um celular real.

### Instalação USB avançada, como último recurso

A seção recolhida **Último recurso: instalar pelo cabo USB** usa ADB/WebUSB para instalar somente o APK 6.2.0 acima. O usuário baixa o arquivo pelo Drive e o seleciona no próprio computador; o conteúdo é conferido localmente e não é enviado a um servidor. O link `/view` do Drive é uma página de prévia, portanto o site não finge conseguir baixá-lo diretamente por `fetch` sem as permissões CORS necessárias.

A ativação inicial das opções de desenvolvedor e da depuração USB é **manual**, nas configurações do aparelho. O Android exige ainda a confirmação RSA na tela desbloqueada. Sem essas autorizações não existe um canal ADB para o site usar. A interface explica essas etapas, exige confirmação do responsável e informa o que está aguardando. Não há recurso para ativar a depuração silenciosamente.

A abertura do canal de transferência tem limite de 20 segundos e é interrompida em desconexão ou cancelamento, inclusive quando o SDK ainda espera a resposta inicial. Os detalhes de stdout e stderr do instalador são preservados juntos.

Depois da autorização, o adaptador confere marca, modelo, API e arquitetura do sistema. Ao comando explícito de instalar, valida novamente os bytes, transfere o APK para um caminho temporário fixo, solicita a instalação normal com `pm install -r` e só confirma o sucesso após verificar o pacote e o código de versão 62000. Não força downgrade, não desinstala versões existentes e não desativa verificação de aplicativos, Play Protect ou restrições do aparelho.

Se a opção de encerramento estiver selecionada e a instalação for confirmada, o assistente solicita que os indicadores de opções de desenvolvedor e depuração USB sejam desligados. A alteração pode encerrar imediatamente o próprio canal ADB; por isso, perder a conexão **não comprova o desligamento**. As leituras disponíveis, rejeições e estados não confirmados aparecem no histórico. A pessoa deve conferir no celular os interruptores e, se necessário, desligá-los manualmente. Esses dois indicadores também não garantem que todas as preferências específicas de um fabricante tenham sido restauradas.

O hash aparece abreviado como `3ff081e7…89379f3c`, com opções para copiar ou visualizar o valor completo. A validação continua usando os 64 caracteres e não exige digitação.

O histórico local mostra as etapas, os horários, o progresso dos bytes, a confirmação do instalador e as orientações de erro. O botão **Copiar diagnóstico** reúne a etapa, o aparelho consultado, o erro e os eventos locais; não inclui o número de série nem chaves de depuração. Quando a área de transferência estiver indisponível, o texto fica selecionável para cópia manual. Percentual de envio não equivale a instalação concluída. Ao finalizar ou cancelar, o módulo tenta fechar a conexão, esquecer a autorização USB do site e descartar a chave ADB temporária em memória. Esquecer o USB no navegador não revoga sozinho as autorizações de depuração gravadas no Android; a interface orienta a conferência no aparelho.

O módulo usa dependências locais com versões fixadas; não carrega um serviço remoto de controle. Licenças e procedência estão em `assets/vendor/WEBADB-LICENSES.txt` e nos avisos do módulo. Para reconstruir:

```sh
cd tools/web-adb_build
npm ci --ignore-scripts
npm run build
```

O código-fonte do adaptador e os testes estão no repositório. As simulações verificam recusas, cancelamento, integridade, instalação, confirmação e encerramento, mas não representam um teste em um celular físico. O funcionamento depende do navegador, do driver e das políticas do aparelho. Se uma etapa falhar, o histórico orienta a instalação ou o encerramento manual.

## Segurança e licenças

Utilize o acesso remoto somente com autorização. As confirmações no Hospedeiro e as verificações de identidade da conexão fazem parte do funcionamento do aplicativo. Não publique convites, senhas, arquivos de assinatura ou dados de uma sessão em issues ou commits.

Os instaladores Windows desta edição não possuem assinatura Authenticode. Assinatura de APK, publicação dos fontes e hospedagem no GitHub não constituem aprovação pelo Microsoft Defender, Google Play ou Play Protect, nem garantem ausência de alertas.

A identidade AliançaDX e os materiais originais mantêm seus direitos existentes. Esta publicação não acrescenta uma licença global aos fontes autorais. As dependências mantêm suas próprias licenças, avisos e obrigações; consulte os arquivos de licenças e os manifestos em cada plataforma, incluindo os fontes correspondentes LGPL e as instruções de reconstrução.
