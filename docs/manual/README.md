# Manual do usuário AliançaDX

Edição de 23/11/2026, com 44 páginas para Windows 7.7.0, Android 7.7.0 e iPhone/iPad PWA 7.7.0.

O PDF atualizado tem [download direto no site](https://perabru.github.io/AliancaDX-AcessoRemoto/#manual), [abertura direta do PDF](https://perabru.github.io/AliancaDX-AcessoRemoto/docs/manual/Manual_AliancaDX.pdf) e [visualização no GitHub](Manual_AliancaDX.pdf). Esta pasta contém o PDF, os textos e a diagramação para futuras revisões.

## Gerar o PDF

Requer Python 3, `reportlab` e as fontes DejaVu Sans em `/usr/share/fonts/truetype/dejavu`. Em outras plataformas, ajuste o caminho em `fonts()`.

```sh
python docs/manual/build_manual.py --output /caminho/Manual_AliancaDX.pdf
```

Os cinco arquivos `content_*.json` contêm os textos revisados. A ordem das páginas e os componentes visuais ficam em `build_manual.py`. As ilustrações e o QR são vetoriais; não representam capturas de testes em aparelhos físicos. Os recursos de marca são obtidos de `assets/brand/`.

Antes de publicar uma alteração, renderize todas as páginas, confira margens, tabelas e ilustrações, verifique os destinos do sumário e os links externos. O gerador interrompe se um bloco de texto invadir a margem inferior. Mudanças na ordem das páginas também exigem rever referências numéricas no texto.

Esta edição acompanha os aplicativos 7.7.0. A página 27 explica os modos de imagem: **Mais nitidez** para letras e ícones, **Automático** para equilíbrio e **Resposta rápida** para priorizar menor atraso com menos detalhes. A atualização preserva a preferência salva; quem prioriza leitura deve conferir e alterar o modo explicitamente. Quando uma tela de texto fica estável, a transmissão pode recuperar e preservar os pixels originais com imagem sem perdas, conforme o conteúdo e os limites da sessão. A captura pode chegar a 4K quando a tela, a conexão e os dispositivos permitem; não se promete 4K universal, imagem idêntica em qualquer rede ou atraso zero.

A página 44 distingue a resolução original, a imagem sem perdas e as reduções pela sessão ou pelo aparelho. **Ajustar** mostra a tela inteira, mantendo a proporção; **100%** conserva os pixels disponíveis. O Android oferece **200% · 2x**, e os controles de ampliação e navegação ajudam a ler uma região por vez. Zoom não recria pixels ausentes. A página 6 apresenta o ícone próprio AliançaDX no Windows.

As páginas 39 a 43 explicam **Buscar atualização > Atualizar agora**, com download interno, progresso e conferência do pacote. No Windows, o instalador atualiza o mesmo papel e reabre o programa; antes, encerre as conexões e transferências. Se o Conector iPhone e iPad estiver aberto, use **Encerrar Conector e atualizar** e confira a confirmação, que informa o encerramento de eventuais acessos pelo iPhone/iPad. No Android, o instalador do próprio sistema aplica a atualização, automaticamente quando permitido ou com confirmação quando necessária. Na PWA, a atualização preparada é aplicada e o aplicativo recarrega após encerrar os acessos em todas as abas. O Conector local acompanha a atualização do Hospedeiro Windows.

Quem usa a versão 7.5 ou anterior precisa instalar a versão atual uma vez para receber o instalador interno. As atualizações seguintes podem ser iniciadas e instaladas pelo próprio aplicativo. Os dados e a identidade de atualização são preservados; o usuário não deve desinstalar como primeira tentativa após uma falha.

Mousepad, botões explícitos, modos por sessão, acessos salvos, atualização do código e múltiplas sessões permanecem disponíveis. A aprovação do Hospedeiro e a ocultação de comandos em tela cheia continuam preservadas. O manual distingue testes de software e ensaios em aparelhos físicos.
