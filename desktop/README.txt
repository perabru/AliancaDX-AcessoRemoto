ALIANÇADX ACESSO REMOTO 6.2.1
Host e Controlador • Windows 10/11 de 64 bits

COMECE AQUI
Abra COMECE_AQUI.html: o guia visual funciona offline.
Para um passo a passo em texto, leia GUIA_RAPIDO.txt.

QUAL INSTALADOR USAR?
• Computador que será acessado: dist/AliancaDX_Host_Setup.exe.
• Computador de quem vai acessar: dist/AliancaDX_Controlador_Setup.exe.
Python, bibliotecas e componente de rede estão incluídos. A instalação é
para o usuário atual e não abre o acesso remoto automaticamente.
Após instalar, use o atalho AliançaDX Acesso Remoto no menu Iniciar.
Os arquivos Abrir_AliancaDX_Host.cmd e Abrir_AliancaDX_Controlador.cmd
abrem manualmente o aplicativo correspondente, quando já estiver instalado.

CONECTAR EM 3 PASSOS
1. No Host, aguarde ficar disponível e clique em Copiar convite.
   Compartilhe o convite somente com a pessoa autorizada.
2. No Controlador, abra Conectar, cole o convite e continue com o código.
   Digite a senha atual no campo próprio e solicite o acesso.
   Colar o convite importa o código; a senha é informada separadamente.
3. No Host, confira quem está solicitando e autorize o pedido.
   Mantenha os dois aplicativos abertos durante a sessão.

Não é necessário criar conta, configurar IP virtual ou fazer login de VPN.
Em redes diferentes, os dois PCs precisam de internet e de um caminho
direto ou retransmissor público disponível. Na mesma rede, a opção de IP
local continua disponível nas configurações avançadas.

VISUAL MAIS CLARO
A edição 6.2 traz a marca AliançaDX, o logotipo original e uma interface
em verde, com botões e instruções consistentes entre Host e Controlador.
Código, senha e aprovação continuam separados para orientar cada etapa.
Opções de rede e outros detalhes ficam recolhidos até serem necessários.

REDE LENTA — AJUSTE AUTOMÁTICO
A versão 6.2.1 ajusta resolução e qualidade da imagem conforme a conexão.
A tela pode perder um pouco de detalhe para responder mais rápido. Quadros
antigos não ficam em uma fila de exibição. Atualize o Host e o Controlador
para aproveitar o controle de fluxo. A latência depende da internet nos dois
lados; interrupções e retransmissores continuam podendo afetar a sessão.

DURANTE O ACESSO
• Tela cheia: use o botão na Sessão; Esc sai desse modo.
• Controles: escolha o monitor, ajuste a imagem ou pause o compartilhamento.
• Arquivos: envie ou peça um arquivo; quem recebe confirma e escolhe onde salvar.
  O limite é 100 MiB por arquivo, com conferência SHA-256 da integridade.
• No Host, Pausar interrompe tela e controle; Desconectar encerra a sessão.

ATUALIZAR OU REMOVER
Feche os aplicativos e execute o instalador correspondente.
As pastas de instalação continuam em LocalAppData/Programs/MiniAnyDesk
para atualizar versões anteriores sem perder dados. Os caminhos internos
legados são mantidos por compatibilidade; o nome exibido agora é AliançaDX.
Configurações, computadores salvos, certificados e arquivos recebidos
são preservados. Apenas os atalhos antigos conhecidos são substituídos.
A remoção fica em Aplicativos do Windows com o novo nome.

SE NÃO CONECTAR
• Host parado: abra o Host e clique em Iniciar.
• Código inválido: copie um convite novo no Host correto.
• Senha recusada: confira a senha atual e espaços ao digitar.
• Aguardando aprovação: confirme o pedido no Host; se expirou, tente novamente.
• Rede indisponível: confira a internet nos dois PCs e aguarde a preparação.
  Retransmissores públicos e regras de cada rede podem afetar a conexão.

ESTADO DA DISTRIBUIÇÃO
Estes instaladores não possuem assinatura Authenticode. O Windows pode
mostrar avisos de publicador desconhecido ou reputação. Nome, ícone e
metadados de autor não substituem uma assinatura digital verificada.
Consulte CONFIANCA_WINDOWS.txt e VALIDACAO.txt para os testes e limites.
Não foi realizada instalação dos EXEs em Windows real nem varredura Defender.
Não há garantia de conexão em qualquer rede ou de ausência de alertas.

Os fontes e testes acompanham o pacote. installer/REBUILD.txt explica a
construção offline e installer/ASSINATURA_WINDOWS.txt descreve a assinatura
real com certificado legítimo. SHA256.txt identifica os arquivos finais.
Versão: 6.2.1. Protocolo compatível preservado: MiniAnyDesk 3.0.0.
