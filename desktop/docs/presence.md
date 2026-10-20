# Nome do computador e disponibilidade — desktop 3.1

No Hospedeiro, edite **Nome deste computador** e pressione **Salvar nome**. O nome público fica em `%LOCALAPPDATA%\MiniAnyDeskNativo\Host\host-settings.json` (no perfil do usuário em outras plataformas). Esse arquivo contém somente o nome; a senha continua pertencendo à sessão. O nome é independente de qualquer apelido que o controlador use em sua lista pessoal.

Uma consulta de disponibilidade abre TLS 1.2 ou posterior e anuncia apenas o protocolo ALPN `minianydesk-status/1`. O Hospedeiro 3.1 responde com nome, versão e se há uma sessão ou aprovação em andamento; encerra a conexão antes do login. A consulta não envia senha, pedido de autorização, imagem, comando de controle ou mensagem de aplicação. Não altera o contador de senhas incorretas nem ocupa uma sessão.

Um status **Online** significa que o hospedeiro respondeu. Antes da primeira conexão, sua identidade ainda exige conferência do certificado na janela normal de conexão. A consulta nunca salva ou substitui uma impressão de certificado. Se uma impressão já conhecida mudou, o resultado é **Identidade não verificada** mesmo quando o servidor está acessível.

Desktop 3.0 e Android 3.0 continuam compatíveis com conexões normais. Ao consultá-los, o controlador fecha TLS sem enviar dados: um certificado conhecido e igual permite indicar disponibilidade; sem uma identidade já conhecida, uma porta TLS sozinha recebe **Identidade não verificada**. Essas versões não informam nome ou ocupação nessa consulta.

O aplicativo e a resposta de disponibilidade têm versão **3.1.0**. As mensagens normais de login mantêm o protocolo **3.0.0** para conservar a compatibilidade com Android e desktops existentes. A consulta tem prazo de três segundos, incluindo resolução de nome, TLS e resposta. A resolução DNS usa no máximo quatro workers daemon e quatro tarefas pendentes; uma resolução do sistema atrasada não impede a consulta de retornar nem segura o encerramento do aplicativo.

Nome e estado podem ficar desatualizados entre consultas, e um estado online não garante que uma conexão será aceita. Consentimento local, senha, pausa e proteções do sistema continuam sendo necessários. Como o nome é uma informação pública retornada antes de autenticar, escolha um nome que possa ser mostrado a quem alcançar a porta TLS pela rede.
