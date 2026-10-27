# Manual do usuário AliançaDX

Edição de 27/10/2026, com 44 páginas para Windows 7.6.1, Android 7.6.1 e iPhone/iPad PWA 7.6.1.

O PDF atualizado tem [download direto no site](https://perabru.github.io/AliancaDX-AcessoRemoto/#manual), [abertura direta do PDF](https://perabru.github.io/AliancaDX-AcessoRemoto/docs/manual/Manual_AliancaDX.pdf) e [visualização no GitHub](Manual_AliancaDX.pdf). Esta pasta contém o PDF, os textos e a diagramação para futuras revisões.

## Gerar o PDF

Requer Python 3, `reportlab` e as fontes DejaVu Sans em `/usr/share/fonts/truetype/dejavu`. Em outras plataformas, ajuste o caminho em `fonts()`.

```sh
python docs/manual/build_manual.py --output /caminho/Manual_AliancaDX.pdf
```

Os cinco arquivos `content_*.json` contêm os textos revisados. A ordem das páginas e os componentes visuais ficam em `build_manual.py`. As ilustrações e o QR são vetoriais; não representam capturas de testes em aparelhos físicos. Os recursos de marca são obtidos de `assets/brand/`.

Antes de publicar uma alteração, renderize todas as páginas, confira margens, tabelas e ilustrações, verifique os destinos do sumário e os links externos. O gerador interrompe se um bloco de texto invadir a margem inferior. Mudanças na ordem das páginas também exigem rever referências numéricas no texto.

Esta edição acompanha os aplicativos 7.6.1. A página 27 explica a adaptação da imagem e a transmissão sem perdas para telas de texto leves quando ela cabe nos limites da sessão. A captura nativa pode chegar a 4K conforme a tela, a conexão e os dispositivos; conteúdos mais pesados continuam usando compressão adaptativa. A página 44 explica que Ajustar ocupa a área disponível mesmo com quadros pequenos, enquanto 100% conserva um pixel recebido por pixel da tela. Inclui zoom, rotação, tela cheia e os avisos de limitação do dispositivo. A página 6 apresenta o ícone próprio AliançaDX no Windows. Não se promete 4K em toda sessão, atraso zero ou recuperação de detalhes que já chegaram reduzidos.

As páginas 39 a 43 explicam **Buscar atualização > Atualizar agora**, com download interno, progresso e conferência do pacote. No Windows, o instalador atualiza o mesmo papel e reabre o programa; antes, encerre as conexões e transferências. Se o Conector iPhone e iPad estiver aberto, use **Encerrar Conector e atualizar** e confira a confirmação, que informa o encerramento de eventuais acessos pelo iPhone/iPad. No Android, o instalador do próprio sistema aplica a atualização, automaticamente quando permitido ou com confirmação quando necessária. Na PWA, a atualização preparada é aplicada e o aplicativo recarrega após encerrar os acessos em todas as abas. O Conector local acompanha a atualização do Hospedeiro Windows.

Quem usa a versão 7.5 ou anterior precisa instalar a versão atual uma vez para receber o instalador interno. As atualizações seguintes podem ser iniciadas e instaladas pelo próprio aplicativo. Os dados e a identidade de atualização são preservados; o usuário não deve desinstalar como primeira tentativa após uma falha.

Mousepad, botões explícitos, modos por sessão, acessos salvos, atualização do código e múltiplas sessões permanecem disponíveis. A aprovação do Hospedeiro e a ocultação de comandos em tela cheia continuam preservadas. O manual distingue testes de software e ensaios em aparelhos físicos.
