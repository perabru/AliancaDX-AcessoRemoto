# Manual do usuário AliançaDX

Edição de 27/10/2026, com 38 páginas para Windows 7.3.0, Android 7.3.0 e iPhone/iPad PWA 7.3.0.

O PDF atualizado tem [download direto no site](https://perabru.github.io/AliancaDX-AcessoRemoto/#manual), [abertura direta do PDF](https://perabru.github.io/AliancaDX-AcessoRemoto/docs/manual/Manual_AliancaDX.pdf) e [visualização no GitHub](Manual_AliancaDX.pdf). Esta pasta contém o PDF, os textos e a diagramação para futuras revisões.

## Gerar o PDF

Requer Python 3, `reportlab` e as fontes DejaVu Sans em `/usr/share/fonts/truetype/dejavu`. Em outras plataformas, ajuste o caminho em `fonts()`.

```sh
python docs/manual/build_manual.py --output /caminho/Manual_AliancaDX.pdf
```

Os quatro arquivos `content_*.json` contêm os textos revisados. A ordem das páginas e os componentes visuais ficam em `build_manual.py`. As ilustrações e o QR são vetoriais; não representam capturas de testes em aparelhos físicos. Os recursos de marca são obtidos de `assets/brand/`.

Antes de publicar uma alteração, renderize todas as páginas, confira margens, tabelas e ilustrações, verifique os destinos do sumário e os links externos. O gerador interrompe se um bloco de texto invadir a margem inferior. Mudanças na ordem das páginas também exigem rever referências numéricas no texto.

Esta edição acompanha os aplicativos 7.3.0. A engrenagem de cada sessão oferece **Automático** (padrão), **Resposta rápida** e **Mais nitidez**, com aplicação sem reconectar. A página 27 explica a escolha por sessão, a confirmação e a necessidade de atualizar Hospedeiro e Controlador. O modo automático continua acompanhando envio e apresentação dos quadros, sem teste externo de velocidade. Não se promete atraso zero ou imagem idêntica em qualquer rede.

Mousepad de tela inteira, botões explícitos, acessos salvos, atualização do código e múltiplas sessões foram preservados. Os indicadores de tráfego e a ocultação de comandos em tela cheia continuam disponíveis. A aprovação do Hospedeiro e os limites de cada plataforma permanecem. O manual distingue testes de software e ensaios em aparelhos físicos.
