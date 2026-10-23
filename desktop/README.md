# AliançaDX Acesso Remoto — código-fonte Desktop

Esta pasta contém os fontes e testes da versão 6.2.2. Os binários de execução,
wheels e arquivos compactados de fontes de terceiros ficam fora do Git.

Leia [README_FONTE_PUBLICO.md](README_FONTE_PUBLICO.md) para preparar o cache
verificado, construir os instaladores e executar os testes. Os links dos
instaladores prontos estão no [README principal](../README.md).

Os arquivos `README.txt`, `GUIA_RAPIDO.txt` e `COMECE_AQUI.html` são os guias do
pacote distribuído e do aplicativo instalado; suas referências a componentes
incluídos descrevem esse pacote completo.

A edição 6.2.1 ajusta resolução, compressão e ritmo de captura conforme o retorno do Controlador. O envio negociado mantém um quadro aguardando apresentação e uma única tentativa de recuperação; o transporte e a interface mantêm somente a imagem mais recente. Atualize os dois lados. Consulte [docs/DESEMPENHO_6_2_1.md](docs/DESEMPENHO_6_2_1.md) para protocolo, testes e limites.

A edição 6.2.2 permite até **8 janelas independentes do Controlador**. Use **Nova conexão** e o seletor de sessões. Cada janela mantém seu próprio acesso, imagens, comandos, arquivos e reconexão; fechar uma preserva as demais. O Host continua aceitando uma sessão por vez. Consulte [o guia das sessões](docs/SESSOES_6_2_2.md).
