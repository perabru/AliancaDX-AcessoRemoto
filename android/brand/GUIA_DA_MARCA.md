# AliançaDX Acesso Remoto

A marca usa o anel geométrico de dois segmentos, com aberturas na parte
superior e inferior, da imagem fornecida. O símbolo foi reconstituído como
vetor; o lettering original não foi redesenhado nem substituído por fonte.

`aliancadx-logo-original.jpg` permanece intacto, com 402 × 94 pixels e SHA256
`f645462fab70ea57c76a051a38053e56ee0a36438dfdaa9f3464f0fcc3b1f92a`.
`aliancadx-logo.png` conserva os pixels decodificados desse JPEG, sobre branco.
Use essa versão horizontal no cabeçalho, mantendo a proporção original.

O vetor `aliancadx-mark.svg` contém somente o símbolo, com fundo transparente.
`aliancadx-app.svg` usa o mesmo símbolo centralizado sobre branco, com margem
adequada às máscaras de ícones adaptativos. As versões PNG 1024/256 e o ICO
16/24/32/48/64/128/256 são derivados dessa mesma geometria por código.

| Uso | Cor |
| --- | --- |
| Verde neon da marca, aproximado do JPEG | `#02EF5F` |
| Esmeralda da marca | `#009966` |
| Botões e links sobre branco | `#007A50` |
| Texto principal | `#123A2A` |
| Texto secundário | `#446557` |
| Fundo suave | `#F4FAF7` |
| Branco | `#FFFFFF` |

Texto branco sobre o verde primário tem contraste **5,39:1**; texto principal
sobre branco, **12,62:1**; texto secundário sobre o fundo suave, **6,12:1**.
Use neon como detalhe ou elemento de marca, sem aplicá-lo a texto pequeno
sobre branco. Em painéis escuros, variantes próximas como `#073B2B` para
fundo e `#087F5B` para ações conservam a direção visual da marca. Os valores
observados no JPEG estão documentados em `brand.json`; compressão e gradiente
fazem as amostras variarem dentro da imagem.

Android: `R.drawable.aliancadx_logo` contém o JPEG original. O ícone adaptativo
usa foreground vetorial de 108 dp, com diâmetro de 63 dp dentro da área segura
de 66 dp, fundo branco e versão monocromática para ícones temáticos no Android
13+. Há recursos base e `mipmap-anydpi-v33`.

Desktop: todos os arquivos gráficos estão replicados em `desktop/assets/brand`.
O instalador usa `aliancadx-wizard.bmp` (164 × 314, símbolo branco sobre verde)
e `aliancadx-header.bmp` (150 × 57, marca original sobre branco).

Para reproduzir: Python 3 e Pillow, executando
`python brand/generate_brand_assets.py` na raiz de qualquer projeto que contenha
esta pasta. **Sem opções, o gerador escreve somente os arquivos da própria
pasta `brand/`.** Isso funciona nos fontes Android ou desktop isolados e não
tenta encontrar nem alterar projetos vizinhos.

A integração com um projeto existente é opcional e explícita:

```bash
# Na raiz de um projeto Android isolado:
python brand/generate_brand_assets.py --android-project .

# Na raiz de um projeto desktop isolado:
python brand/generate_brand_assets.py --desktop-project .

# Na raiz de um pacote que contenha os dois projetos:
python brand/generate_brand_assets.py --android-project android --desktop-project desktop
```

`--android-project` atualiza os recursos de ícone e a logo em `app/src/main/res`.
`--desktop-project` copia os gráficos para `assets/brand`. Os destinos precisam
existir; nenhuma dessas opções é necessária para gerar os assets locais.
Não execute integração sobre uma entrega congelada: alterações de recursos
exigem nova compilação e assinatura. O código gera SVG, PNG, ICO e BMP sem IA,
fontes externas ou imagens de terceiros. Não compila, assina nem altera
certificados.

A imagem original foi fornecida pelo usuário. Seus direitos e os da marca
continuam com seus respectivos titulares. O gerador segue a licença Apache
2.0 do projeto; nenhuma licença anterior ou aviso de dependência foi alterado.
