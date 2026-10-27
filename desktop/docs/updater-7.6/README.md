# Atualização pelo aplicativo — Windows 7.6

No Controlador e no Hospedeiro, **Buscar atualização → Atualizar agora** recebe o
instalador correto dentro do aplicativo. O usuário acompanha o progresso em MB e
porcentagem. O arquivo só é aceito se o tamanho e o SHA256 corresponderem ao digest
do asset da versão estável publicada no repositório oficial do GitHub.

A instalação aguarda o encerramento das conexões, reconexões e transferências.
Se o Conector iPhone/iPad ainda estiver em execução, aparece o botão **Encerrar
Conector e atualizar**. Ele pede confirmação clara de que qualquer atendimento
nesse Conector será interrompido. Não basta fechar a aba do navegador: o botão
encerra o processo do Conector imediatamente e a instalação aguarda sua saída real,
sem depender do prazo de cinco minutos de inatividade. O estado é conferido
novamente antes do aplicativo fechar. O Windows continua aplicando suas proteções normais; a instalação é para
o usuário atual, sem solicitar privilégios de administrador por padrão.

O aplicativo fecha, o instalador existente mostra o progresso, prepara o ambiente
privado e reabre o mesmo papel quando a versão instalada é confirmada. Computadores
salvos, certificados e configurações permanecem no diretório de dados existente.
O usuário pode cancelar o recebimento ou a espera. Uma instalação em andamento
continua usando o cancelamento e a restauração já existentes no instalador.

## Compatibilidade

- É necessário instalar a versão 7.6 uma vez pelo site para receber este novo
  recurso. Uma versão antiga que apenas abre o navegador não pode atualizar seu
  próprio mecanismo antes de essa nova versão ser instalada.
- O recurso requer a instalação Windows na pasta por usuário reconhecida pelo
  aplicativo. Uma execução direta dos fontes recebe uma orientação legível.
- O arquivo ainda precisa ser transferido pela internet; a diferença é que o
  usuário não precisa baixar, procurar nem executar outro arquivo manualmente.
- O programa consulta somente versões públicas e estáveis do próprio projeto.
  Sem digest válido, o botão de instalar permanece desabilitado.

## Implementação e limites

`updates.py` consulta a versão pública, preservando tamanho e SHA256 do asset.
`update_install.py` limita o instalador a 512 MiB, usa TLS com validação de
certificado e hostname, limita redirecionamentos aos hosts CDN de assets do
GitHub, calcula o hash por blocos, rejeita arquivos incompletos e verifica a
assinatura de arquivo PE (`MZ`). Essa verificação não equivale a uma assinatura
Authenticode do editor. A autenticidade da origem depende do HTTPS e da conta
que publica o repositório GitHub, como no download pelo site oficial.

O download usa uma pasta temporária por operação no diretório de dados do usuário.
Cancelar ou falhar no recebimento elimina o arquivo parcial. Antes da instalação,
o hash é conferido novamente. Um auxiliar é executado pelo Python principal,
fora do ambiente `.venv` que será substituído, e aguarda o processo antigo encerrar.
Um sinal de prontidão impede o aplicativo de fechar quando o auxiliar não inicia;
um sinal de cancelamento cobre uma conexão aberta durante esse intervalo.

O auxiliar chama o NSIS com `/S /UPDATE`, sem comando de shell. `/UPDATE` prepara o
instalador Python com `--from-nsis --auto-update`: sua janela de progresso continua
visível, a operação começa sem outro clique e termina automaticamente no sucesso.
Erros permanecem visíveis e permitem tentar novamente. Não há reinicialização do
Windows. O auxiliar só abre o aplicativo após conferir a versão e o papel no
marcador da instalação concluída.

## Validação reproduzível

```sh
python3 -m unittest desktop/tests/test_update_install.py desktop/tests/test_updates.py desktop/tests/test_update_dialog.py desktop/tests/test_installer_python.py
python3 -m unittest discover -s desktop/tests -p 'test_controller*.py'
```

Os testes cobrem tamanho, hash, papel, origem HTTPS, redirecionamentos, cancelamento,
tamper após o encerramento do processo, argumentos de instalação, falhas de
instalação, confirmação da versão, relançamento, múltiplas janelas, conexões em
andamento e ausência de operações Tk no worker.

Com um display Tk disponível:

```sh
python3 desktop/tests/gui_updates.py --report /tmp/windows-update-ui.json --screenshots /tmp/windows-update-ui
python3 desktop/tests/gui_auto_update_installer.py --report /tmp/windows-installer-ui.json --screenshots /tmp/windows-installer-ui
```

As verificações de interface usam widgets Tk reais e TLS local com telas geradas;
a resposta do GitHub, o download e a execução do instalador são injetados. Também
conferem a negociação e apresentação PNG e a preservação exata de um pixel colorido.
Os testes do instalador usam workers de sucesso/falha para validar início automático,
progresso visível, conclusão automática e erro com opção de tentar novamente.
Esses testes em Linux não substituem instalar e atualizar em um Windows físico.
