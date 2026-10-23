# AliançaDX Acesso Remoto 6.2.2 — sessões e comandos de mouse

Esta edição permite manter conexões com PCs diferentes no mesmo Controlador.
Cada acesso continua dependendo da senha, da identidade TLS do PC e da aprovação
no Hospedeiro. Abrir outra sessão não autoriza acesso automaticamente.

## Windows: abrir outra conexão

1. Conecte ao primeiro PC e aguarde a aprovação no Hospedeiro.
2. Use **Nova conexão** para abrir outra janela do Controlador.
3. Informe o convite e a senha do segundo PC e aguarde sua aprovação.
4. Use o seletor de sessões ou as próprias janelas para alternar entre os PCs.
5. Use **Encerrar** na sessão desejada para terminar somente aquele acesso.

As sessões usam conexões, filas de comandos, telas e transferências independentes.
As configurações e os certificados conhecidos são compartilhados pelo gerenciador;
as senhas de conexão não são salvas. Fechar uma janela encerra sua sessão.

## Android: abrir e alternar sessões

1. Conecte ao primeiro PC e aguarde a aprovação no Hospedeiro.
2. Use a opção de adicionar uma conexão no seletor de sessões.
3. Cole o convite do próximo PC, informe a senha e conecte.
4. Toque na sessão desejada para trocar a tela que será controlada.
5. Confira o nome do PC antes de clicar, digitar ou enviar arquivos.
6. Encerre cada sessão quando terminar o acesso.

As conexões com os outros PCs permanecem abertas enquanto você alterna a tela.
Somente a sessão exibida recebe os comandos do usuário. As teclas e os botões
mantidos pressionados são liberados ao sair da tela, para evitar comandos presos.
As escolhas e confirmações de arquivos pertencem à sessão que as iniciou.

## Android: botões do mouse

Toque na tela remota para selecionar o alvo. Esse toque já faz um clique
esquerdo; o marcador verde mostra o ponto do ponteiro. Depois, use os botões de
**Clique duplo** e **Clique direito** nesse ponto. O clique duplo envia dois cliques completos do botão
esquerdo no mesmo ponto. O clique direito abre o menu de contexto do Windows
quando o programa remoto oferece esse menu. Os comandos também ficam disponíveis
em tela cheia. Os gestos de toque, arraste e ampliação continuam disponíveis.

## Limites e uso em redes lentas

Cada sessão consome memória, processamento e conexão. Abra apenas os PCs de que
precisa e encerre os demais quando terminar. O limite é de **8 janelas no Windows** e **4 sessões no Android**, para evitar
crescimento descontrolado de recursos.

A transmissão adaptativa da edição 6.2.1 continua disponível. No Android, a tela
em segundo plano pode interromper a apresentação de imagens para poupar recursos;
a imagem atual é solicitada quando você volta à sessão. A conexão e as
transferências não precisam ser encerradas para trocar de tela.

O Hospedeiro mantém sua política de uma sessão autorizada por vez no PC.
A novidade permite a um Controlador acessar vários PCs, cada um com seu próprio
Hospedeiro e sua própria autorização. Não cria acesso simultâneo de várias
pessoas ao mesmo Hospedeiro.

## Verificação

Verificações concluídas nesta edição:

- 393 testes desktop, incluindo 12 novos casos de sessões.
- 62 testes de interoperabilidade Android/Desktop por TCP/TLS local, sem falhas.
- 23 verificações com duas janelas Tk e duas conexões TCP/TLS locais reais,
  incluindo imagem, confirmação de apresentação, comandos e encerramento isolado.
- 25 verificações Android de redes simultâneas, com JNI simulado e TLS local real.
- 23 verificações de regras de interface, propriedade das sessões e comandos.
- 32 verificações de geometria, viewport e toque, incluindo clique duplo e direito.

[Relatório de interoperabilidade](INTEROP_6_2_2.json),
[relatório de interface desktop](INTERFACE_SESSOES_6_2_2.json) e
[relatório de redes Android](../../android/docs/REDES_SIMULTANEAS_6_2_2.json)
registram os métodos, hashes de fontes e limites desses ensaios.

A interface desktop foi executada em Linux/Xvfb; os testes Android de interface
usam API 36 e substitutos de componentes do Android. Isso não representa teste
físico dos instaladores em Windows, do APK em um celular ou dos retransmissores
pela internet. A disponibilidade da conexão ainda depende da rede dos dois lados.
Os relatórios de compilação e assinatura acompanham os pacotes de distribuição.

A compilação Android release e o lint passaram (54 tarefas, nenhum erro de lint;
37 avisos). A assinatura de atualização, os sete intervalos de SDK 26–37 e o
alinhamento de 16 KiB foram verificados; as quatro bibliotecas nativas mantêm os
hashes da distribuição anterior. Consulte [APK](../../docs/APK_ANDROID_6_2_2.json)
e [assinatura](../../docs/ASSINATURA_ANDROID_6_2_2.json).
