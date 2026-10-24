# Tráfego da sessão — 6.4.0

O Host e cada janela do Controlador mostram **Sessão ↓ recebido ↑ enviado Mbps**.
Os números são a média aproximada dos últimos três segundos, atualizada a cada
segundo. Um Mbps equivale a 1.000.000 bits por segundo. Uma sessão parada pode
mostrar zero: isso não indica, por si só, um erro de conexão.

A medida inclui os bytes de aplicação que o transporte da sessão realmente
recebeu ou entregou ao socket: tela codificada, comandos, arquivos, mensagens de
controle e seus cabeçalhos de quatro bytes. Não conta mensagens apenas na fila,
frames substituídos antes do envio ou tentativas de envio sem progresso. Não
inclui a negociação inicial, cabeçalhos TLS/IP nem o tráfego próprio da rede
privada. Portanto, **não é a velocidade máxima da internet, um teste de conexão,
latência ou uma confirmação de que o outro dispositivo já exibiu a imagem**.
O ACK de apresentação continua responsável por essa confirmação.

Os contadores pertencem a cada Peer e zeram em uma nova conexão. Um bloqueio curto
protege os totais; até 32 baldes de 100 ms mantêm a memória limitada. A borda da
janela tem precisão de 100 ms. Não há pacotes de teste, alterações no protocolo,
novas permissões, gravação de conteúdo ou interferência na fila de transmissão.

No Controlador, a tela cheia mostra a medida no canto superior esquerdo e os
comandos à direita. **Ocultar comandos / F8** libera a imagem de sobreposições.
**F8** mostra os comandos novamente e **Esc** sempre sai da tela cheia. Ao mudar
essa apresentação, o Controlador libera mouse e teclas remotos pressionados.

Verificação: `python -m unittest discover -s desktop/tests -p 'test_connection_metrics.py'`.
Os testes exercitam janela, repouso, isolamento, concorrência e transporte real
em sockets locais com escritas parciais, bloqueios, frames substituídos e leituras
fragmentadas. Não são uma medição de desempenho de internet ou uma instalação
real no Windows.
