# Privacidade — AliançaDX Acesso Remoto 6.2.0

Projeto acadêmico independente de Bruno Michel Pera. Atualizado em 19/10/2026.

O aplicativo permite visualizar e controlar um PC cujo responsável autoriza a
sessão no AliançaDX Host. Também permite enviar e solicitar arquivos escolhidos
pelas pessoas participantes. O celular funciona como controlador do PC.

## Dados usados durante o acesso

O app processa o código de conexão, a senha atual do Host, o nome apresentado ao
PC, imagens da tela remota, comandos de teclado/mouse e arquivos selecionados.
Esses dados são necessários para executar as ações solicitadas. A senha é
enviada somente depois da conferência da identidade TLS do Host; o PC precisa
aprovar cada sessão. O conteúdo da sessão usa TLS entre celular e Host.

A rede EasyTier pode usar conexões diretas ou retransmissores comunitários de
terceiros. Eles podem observar endereços IP e informações de conexão. O app não
controla a política de registros desses operadores, a disponibilidade da rede ou
o que a pessoa autorizada no outro computador faz com os dados recebidos.

## Dados guardados no aparelho

Os computadores salvos, nomes/endpoints, preferências e fingerprints dos
certificados ficam no armazenamento privado do aplicativo. O código privado só
é lembrado por opção explícita e é cifrado com AndroidKeyStore. A senha não é
gravada nas preferências: pode permanecer em memória durante a sessão e,
quando autorizada a reconexão, nas tentativas por até dois minutos.

Os arquivos são escolhidos nas janelas do Android. Durante a transferência, o
app usa arquivos temporários privados e verifica SHA-256 antes de concluir.
Temporários identificáveis deixados por um processo anterior são limpos na
primeira preparação das transferências do processo seguinte. O Android ou uma
interrupção abrupta pode impedir limpeza imediata. Arquivos concluídos ficam no
destino escolhido por quem recebe e precisam ser removidos nesse destino.

## Permissões

O APK solicita apenas INTERNET e ACCESS_NETWORK_STATE. Não solicita acesso geral
ao armazenamento, câmera, microfone, contatos, localização ou serviço de
acessibilidade. Não instala uma VPN do sistema. O seletor de documentos limita
o acesso aos arquivos/destinos escolhidos.

## Interromper e apagar

Use Encerrar para terminar a sessão. Em Salvos, Editar > Excluir remove o
computador e seu código privado lembrado. Para apagar todos os dados locais,
use Configurações do Android > Aplicativos > AliançaDX Acesso Remoto >
Armazenamento > Limpar dados. Isso também remove preferências e identidades
salvas, e não apaga arquivos que você já exportou para outros destinos.

## Distribuição e contato

Esta é uma distribuição direta de APK, sem publicação ou aprovação da Google
Play nesta entrega. A assinatura autentica a versão e suas atualizações; não
representa uma análise do Play Protect ou verificação de identidade pelo Google.
O mantenedor responsável por esta edição é Bruno Michel Pera. Para suporte,
utilize o canal por onde recebeu o aplicativo. Antes de publicar em uma loja,
o mantenedor deve informar um contato público de suporte e disponibilizar esta
política em um endereço público, conforme os requisitos da loja.
