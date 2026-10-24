# Acessos salvos — 7.0.0

Em **Conectar**, cole o código ou o convite completo do Hospedeiro. Para voltar a
usar esse computador sem colar o código novamente, marque **Salvar este acesso em
Meus computadores** e, se desejar, dê um nome ao computador. Clique em Continuar.
O acesso já aparece em Meus computadores antes da primeira autenticação, mesmo
que você ainda não tenha a senha ou o Hospedeiro esteja indisponível.

No Windows, o código privado fica no cofre protegido pelo DPAPI do usuário atual.
O cadastro contém apenas o nome, o endereço público da conexão e as datas. **A
senha nunca é gravada**: informe a senha atual ao conectar. O convite pode conter
uma senha, mas a importação extrai somente o código privado. Em outros sistemas,
o mecanismo existente usa arquivo restrito ao usuário, sem alegar proteção DPAPI.

Em **Meus computadores**, escolha **Conectar** ou **Reconectar**. O aplicativo
recupera o código do cofre, abre a etapa da senha e mantém a conferência TLS e a
aprovação no Hospedeiro. Uma senha já digitada na mesma janela só pode ser usada
para a mesma identidade. Encerrar a sessão limpa a senha. Uma sessão já aberta
ganha foco; escolher outro acesso durante uma sessão abre uma janela independente,
até o limite existente de oito. Encerrar ou cancelar uma tentativa não apaga o
código salvo.

Se o Hospedeiro revogar o convite, selecione o computador e clique em **Atualizar
código**. Cole o novo código ou convite e continue com Salvar marcado. O aplicativo
mantém o mesmo registro, nome e histórico, atualizando o código e o destino. O
botão **Cancelar atualização**, ou sair dessa página antes de continuar, preserva
o acesso anterior. Um cofre ausente também oferece essa recuperação sem excluir
o computador. Mudar o certificado limpa a senha em memória; uma impressão digital
salva diferente continua exigindo a confirmação explícita da nova identidade.

Salvar grava e relê o cofre para confirmar que o código pode ser recuperado antes
de gravar o cadastro sob seu bloqueio compartilhado. Se uma escrita falhar, o
aplicativo restaura e confere os bytes anteriores do cofre e do cadastro, inclusive
se o arquivo já tiver sido substituído. Se não conseguir confirmar a restauração,
informa isso e não inicia a conexão. As duas gravações são substituições atômicas separadas;
isso não constitui uma transação garantida contra interrupção de energia. Um
código revogado antigo pode permanecer no cofre, mas o registro atualizado passa a
usar somente o novo destino. Desmarcar Salvar usa o código só na janela atual e não
apaga um código previamente lembrado.

Um cofre corrompido ou inacessível é informado como erro e oferece Atualizar código;
o aplicativo não o trata como um código esquecido nem usa uma cópia antiga da
memória silenciosamente. A mudança somente da porta de um único Hospedeiro mantém
o mesmo registro. As janelas recarregam o registro pelo ID salvo, de modo que uma
reconexão encontre o convite atualizado em outra janela.

A versão 7.0.0 preserva os caminhos e formatos da série 6: convite MDA6, derivação
de identidade, endereço público, esquema 1 do cadastro, cofre DPAPI, certificados
e protocolo de sessão 3.0.0. Não exige apagar os acessos existentes.

Verificações: `PYTHONPATH=desktop python -m unittest discover -s desktop/tests -p
'test_saved_pairings.py'`; o roteiro `tests/gui_saved_pairings.py` exercita Tk,
persistência e TLS locais com estado temporário. Esses testes não substituem a
instalação real no Windows ou uma prova de conexão pela internet.
