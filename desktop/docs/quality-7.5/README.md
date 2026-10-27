# Nitidez da transmissão — 7.5.0

A versão 7.5 envia mais pixels reais e preserva melhor o texto colorido. O
Automático começa em até 1920 × 1080 e pode recuperar 2560 × 1440. Mais nitidez
começa em até 2560 × 1440 e pode recuperar 3840 × 2160, conforme a captura,
complexidade da imagem, rede e processamento dos dois aparelhos. Resposta rápida
mantém os perfis leves anteriores, com limite de 1280 × 720.

Esses valores são limites, não resoluções fixas. A imagem nunca é ampliada pelo
Host para inventar pixels. O JPEG continua com perdas; receber a resolução
original significa preservar suas posições de pixels, sem prometer uma cópia
matematicamente idêntica à tela.

## O que mudou no Host

- Auto: inicial 1080p, qualidade JPEG 84; topo 1440p, qualidade 90. Os dois
  preservam as amostras de cor 4:4:4.
- Mais nitidez: inicial 1440p, qualidade 92; topo 4K, qualidade 94. Os quatro
  primeiros perfis preservam 4:4:4 para reduzir manchas em letras coloridas.
- Cada redução usa Lanczos a partir da captura original intacta. Uma tentativa
  de JPEG grande não obriga a próxima a filtrar uma imagem já reduzida.
- Sob pressão de CPU, Auto e Mais nitidez reduzem primeiro a frequência de
  atualização, em até dois passos. Mais nitidez também prefere essa cadência
  menor diante de pressão moderada de rede. Congestão persistente, um envio
  muito lento ou o orçamento de bytes ainda reduzem a resolução.
- A recuperação exige 12 observações favoráveis e pelo menos quatro segundos
  desde a última mudança. Um RTT alto e estável, acompanhado de envio rápido,
  não é tratado isoladamente como uma conexão com pouca capacidade.
- Dimensões do JPEG e dimensões físicas do computador seguem separadas. A
  troca de resolução não muda a correspondência do mouse com a tela física.

Não há formato, modo ou permissão nova no protocolo. Permanecem os limites de
1 MiB de imagem estimada em Auto, 384 KiB em Resposta rápida e 2 MiB em Mais
nitidez, incluindo base64 e 32 KiB reservados para metadados. O limite geral de
mensagem permanece 4 MiB. Quadros acima de 1 MiB usam apenas uma reserva; o
limite absoluto continua dois quadros conhecidos, com ACKs e consentimento.

## Comparação reproduzível

O [relatório JSON](DESEMPENHO_7_5_0.json) compara o encoder desta versão com os
perfis e a filtragem exatos da 7.4, commit
`cf9cb920cd419eb02c45f97e7235c7fec45a369f`. São desktops sintéticos com código
colorido de 13 pixels e numeração de 11 pixels. O tamanho das letras na fonte
não aumenta ao criar a captura 4K.

Ambas as imagens recebidas são reconstruídas no mesmo tamanho da fonte, com
Lanczos, e comparadas ao mesmo recorte nativo de 640 × 360. Assim, o resultado
mede também os detalhes perdidos por redução de resolução. SSIM é a média RGB
em janelas uniformes de 8 × 8, não sobrepostas, com covariância populacional;
esta definição está explicitada para não confundir o resultado com SSIM de
luminância e janelas gaussianas de outras bibliotecas.

| Fonte e prioridade | Pixels recebidos 7.4 → 7.5 | PSNR RGB 7.4 → 7.5 | SSIM RGB 7.4 → 7.5 |
| --- | --- | --- | --- |
| Full HD, Auto inicial | 1280 × 720 → 1920 × 1080 | 19,84 → 32,83 dB | 0,7592 → 0,9571 |
| 1440p, Mais nitidez inicial | 1600 × 900 → 2560 × 1440 | 20,38 → 37,38 dB | 0,7908 → 0,9808 |
| 4K, Auto no topo | 1920 × 1080 → 2560 × 1440 | 18,94 → 21,38 dB | 0,7042 → 0,8374 |
| 4K, Mais nitidez no topo | 1920 × 1080 → 3840 × 2160 | 19,15 → 39,42 dB | 0,7287 → 0,9872 |

Os quatro [comparativos PNG](comparison_3840x2160_quality_detail.png) exibem
fonte, resultado 7.4 e resultado 7.5, ampliados em 2× apenas por vizinho mais
próximo. Não contêm nitidez artificial, geração de pixels ou super-resolução.

Na medição local de cinco iterações com Pillow 12.3.0, o desktop 4K de texto no
topo de Mais nitidez gerou um pacote estimado de 1.380.152 bytes. Codificar seu
tamanho nativo levou mediana de 47,54 ms; reduzir a mesma captura para 1440p
levou 253,22 ms. Isso mostra por que um resize intermediário caro não deve
impedir automaticamente a recuperação do tamanho nativo. Há teste temporal
com resize de 300 ms, codificação nativa de 80 ms e RTT estável de 350 ms que
confirma recuperação de 4K e da cadência.

Uma captura 4K de ruído determinístico também foi testada: cada prioridade
reduziu o JPEG antes do envio até caber no seu orçamento. Os tamanhos,
dimensões efetivas e custo dessas tentativas estão no JSON. Conteúdo complexo
pode exigir menos pixels, mesmo numa conexão rápida.

Para reproduzir a medição, com Pillow e NumPy instalados:

```bash
python desktop/tests/benchmark_stream_fidelity.py --iterations 5
```

As medições usam um processador Linux compartilhado e conteúdo sintético.
Não medem internet real, FPS em um aparelho físico, escala do Windows ou a
aparência da tela de cada celular. Mais detalhes podem consumir mais bytes ou
reduzir a frequência de atualização; estes números não representam promessa
de atraso zero ou redução universal de atraso.
