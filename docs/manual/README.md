# Manual do usuário AliançaDX

Edição de 27/10/2026, com 38 páginas para Windows 7.1.0, Android 7.1.0 e iPhone/iPad PWA 7.1.0.

O PDF atualizado tem [download direto no site](https://perabru.github.io/AliancaDX-AcessoRemoto/#manual), [abertura direta do PDF](https://perabru.github.io/AliancaDX-AcessoRemoto/docs/manual/Manual_AliancaDX.pdf) e [visualização no GitHub](Manual_AliancaDX.pdf). Esta pasta contém o PDF, os textos e a diagramação para futuras revisões.

## Gerar o PDF

Requer Python 3, `reportlab` e as fontes DejaVu Sans em `/usr/share/fonts/truetype/dejavu`. Em outras plataformas, ajuste o caminho em `fonts()`.

```sh
python docs/manual/build_manual.py --output /caminho/Manual_AliancaDX.pdf
```

Os quatro arquivos `content_*.json` contêm os textos revisados. A ordem das páginas e os componentes visuais ficam em `build_manual.py`. As ilustrações e o QR são vetoriais; não representam capturas de testes em aparelhos físicos. Os recursos de marca são obtidos de `assets/brand/`.

Antes de publicar uma alteração, renderize todas as páginas, confira margens, tabelas e ilustrações, verifique os destinos do sumário e os links externos. O gerador interrompe se um bloco de texto invadir a margem inferior. Mudanças na ordem das páginas também exigem rever referências numéricas no texto.

Esta edição acompanha os aplicativos 7.1.0: mousepad relativo em toda a área remota e botões explícitos para clicar e arrastar. Mantém salvamento protegido antes do primeiro acesso, reconexão pelo cartão, atualização do código mantendo o cadastro e favoritos da PWA com novo pareamento. Os indicadores de tráfego e a ocultação de comandos em tela cheia continuam disponíveis. As instruções mantêm a aprovação do Hospedeiro, os limites de cada plataforma e as limitações de validação da PWA em aparelhos físicos.
