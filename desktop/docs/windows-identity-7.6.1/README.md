# Identidade AliançaDX no Windows · 7.6.1

O programa continua usando o interpretador Python oficial, sem alterar seus
executáveis. Antes de criar qualquer janela Tk, cada aplicativo registra sua
identidade estável na barra de tarefas:

| Aplicativo | AppUserModelID |
| --- | --- |
| Controlador | `AliancaDX.AcessoRemoto.Controller` |
| Hospedeiro | `AliancaDX.AcessoRemoto.Host` |
| Conector iPhone/iPad | `AliancaDX.AcessoRemoto.Connector` |
| Instalação | `AliancaDX.AcessoRemoto.Installer` |
| Atualização | `AliancaDX.AcessoRemoto.Updater` |

As identidades não incluem a versão: as janelas do Controlador continuam
agrupadas depois de atualizar, e o Hospedeiro tem seu próprio grupo.

Os atalhos do menu Iniciar e da Área de Trabalho recebem a mesma identidade do
respectivo processo pelo repositório de propriedades nativo do Windows. O
instalador grava, confirma e relê a propriedade, preservando o executável
`pythonw.exe`, os argumentos, a pasta de trabalho e o caminho do ícone.

As janelas usam `assets/brand/aliancadx.ico`, com resoluções 16, 24, 32, 48, 64,
128 e 256 px em entradas DIB clássicas. Os pixels da marca são preservados e o
Tk pode selecionar a imagem pequena adequada sem recorrer à redução de uma
imagem PNG maior. No Windows, o ICO nativo tem prioridade e a foto de apoio só
é usada se seu carregamento falhar. A raiz oculta do Controlador recebe o ícone antes da criação das
suas janelas; os diálogos herdam o padrão do Tk. A imagem de apoio do Tk usa a
mesma arte da identidade já existente, em 256 px. A entrada de desinstalação
também informa o ícone da AliançaDX ao Windows.

O novo módulo integra o inventário, a cópia, a restauração de uma atualização
que falhar e a remoção dos arquivos do aplicativo. Nenhuma configuração de
segurança ou política do Windows é modificada.

Um atalho antigo já fixado pelo usuário pode conservar informações do Python
em cache. Nesse caso, desafixe o atalho antigo e fixe a janela da AliançaDX
depois de atualizar. O instalador não modifica os atalhos fixados pessoais.

## Validação

`python -m unittest desktop.tests.test_app_identity` verifica limites portáveis
e a estrutura ABI usada nas chamadas nativas.

`python desktop/tests/windows_identity_smoke.py` executa no Windows as chamadas
reais: leitura da identidade dos cinco processos; criação de atalhos em uma
pasta temporária; gravação e releitura independente das propriedades; execução
do auxiliar usado pelo instalador; preservação de alvo, argumentos, pasta e
ícone; obtenção dos ícones pequenos e grandes de duas janelas Tk por
`WM_GETICON`. O teste não cria atalhos na Área de Trabalho nem no menu Iniciar.

O teste nativo depende do Windows e é ignorado em outros sistemas. Ele verifica
as APIs e os identificadores de ícone; a aparência do Explorer e a fixação
manual ainda exigem uma conferência em um PC com barra de tarefas visível.

## Referências técnicas

- [AppUserModelIDs e identidade de atalhos](https://learn.microsoft.com/windows/win32/shell/appids)
- [Identidade explícita antes da criação da interface](https://learn.microsoft.com/windows/win32/api/shobjidl_core/nf-shobjidl_core-setcurrentprocessexplicitappusermodelid)
- [Repositório de propriedades de um arquivo](https://learn.microsoft.com/windows/win32/api/shobjidl_core/nf-shobjidl_core-shgetpropertystorefromparsingname)
- [Sinalizador GPS_READWRITE](https://learn.microsoft.com/windows/win32/api/propsys/ne-propsys-getpropertystoreflags)
