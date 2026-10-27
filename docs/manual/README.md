# Manual do usuário AliançaDX

Edição de 27/10/2026, com 44 páginas para Windows 7.5.0, Android 7.5.0 e iPhone/iPad PWA 7.5.0.

O PDF atualizado tem [download direto no site](https://perabru.github.io/AliancaDX-AcessoRemoto/#manual), [abertura direta do PDF](https://perabru.github.io/AliancaDX-AcessoRemoto/docs/manual/Manual_AliancaDX.pdf) e [visualização no GitHub](Manual_AliancaDX.pdf). Esta pasta contém o PDF, os textos e a diagramação para futuras revisões.

## Gerar o PDF

Requer Python 3, `reportlab` e as fontes DejaVu Sans em `/usr/share/fonts/truetype/dejavu`. Em outras plataformas, ajuste o caminho em `fonts()`.

```sh
python docs/manual/build_manual.py --output /caminho/Manual_AliancaDX.pdf
```

Os cinco arquivos `content_*.json` contêm os textos revisados. A ordem das páginas e os componentes visuais ficam em `build_manual.py`. As ilustrações e o QR são vetoriais; não representam capturas de testes em aparelhos físicos. Os recursos de marca são obtidos de `assets/brand/`.

Antes de publicar uma alteração, renderize todas as páginas, confira margens, tabelas e ilustrações, verifique os destinos do sumário e os links externos. O gerador interrompe se um bloco de texto invadir a margem inferior. Mudanças na ordem das páginas também exigem rever referências numéricas no texto.

Esta edição acompanha os aplicativos 7.5.0. A página 27 explica os novos níveis de imagem: Automático até 1440p e Mais nitidez até 4K, conforme a captura original, a rede e os dispositivos. A página 44 explica Ajustar, 100%, zoom, navegação e os avisos de limitação do dispositivo. Não se promete 4K em toda sessão, atraso zero ou imagem idêntica em qualquer rede.

As páginas 39 a 43 mantêm o guia de **Buscar atualização**: consulta manual, resultados e falhas, download pelo navegador, atualização Android com a mesma assinatura, aplicação da PWA após encerrar todas as sessões e atualização do Conector local pelo Windows.

Mousepad, botões explícitos, modos por sessão, acessos salvos, atualização do código e múltiplas sessões permanecem disponíveis. A aprovação do Hospedeiro e a ocultação de comandos em tela cheia continuam preservadas. O manual distingue testes de software e ensaios em aparelhos físicos.
