# AliançaDX 7.6 — nitidez e custo de transmissão

Comparativo reproduzível com o encoder de produção da versão 7.5, no commit `9a66467a77bb7c43bc7d56e5b6821bfc24adcf57`, e o fluxo 7.6. As imagens são sintéticas: código colorido com fonte de 13 pixels, foto sintética e o mesmo texto deslocado para representar uma alteração da tela. Não houve teste em um PC Windows, celular físico ou conexão de internet real.

## O que mudou

- PNG RGB de 8 bits, sem perdas, somente após o controlador negociar `imageFormats: ["jpeg", "png"]` em `stream_ready`. Clientes antigos continuam recebendo JPEG.
- Uma amostra de cores limitada reconhece interfaces de texto e superfícies planas. Fotos, vídeo e o modo Resposta rápida mantêm JPEG, sem copiar a captura inteira para calcular um hash.
- A opção de preservar pixels nativos até 3840 × 2160 depende do tamanho e tempo medidos da escrita anterior e de um ACK real de apresentação de quadro atual, do orçamento do modo, dos dois primeiros níveis de qualidade e da ausência de um probe ou de pressão profunda de CPU. O primeiro nível de redução da frequência ainda permite essa tentativa: em uma tela 4K, eliminar o redimensionamento pode custar menos CPU.
- O PNG nativo precisa caber na projeção conservadora da próxima escrita. A projeção usa o menor entre o orçamento do escritor (metade do limite de escrita rápida do modo) e os bytes do quadro efetivamente reconhecido multiplicados pelo limite de ciclo do modo e divididos pelo tempo real até sua apresentação. O ACK precisa chegar em até 220 ms no Automático ou 320 ms em Mais nitidez; ausência de ACK, bytes desconhecidos, mudança de modo/geração ou retomada desabilitam a expansão até nova evidência. Assim, apenas colocar rapidamente um JPEG no buffer do sistema não autoriza um PNG maior sobre uma conexão lenta. O cálculo não representa uma medição da velocidade total da internet. Se ele não couber, permanece o JPEG com resolução adaptada; não se tenta conservar por PNG uma imagem que já perdeu detalhes no redimensionamento.
- Em um tamanho nativo que já cabe no perfil, PNG só substitui JPEG se seu arquivo real não for maior. Isso pode exigir mais CPU de compressão, mas reduz os bytes transmitidos.
- Texto apto a permanecer nativo usa SHA-256 sobre todos os pixels RGB para evitar recompressão e transmissão de capturas idênticas por até 0,5 segundo. Um pixel alterado, mudança física de monitor, modo, geração ou retorno da tela força nova apresentação. O hash não guarda uma segunda captura e só é lembrado depois do envio. Fotos e fallback 4K redimensionado não pagam esse custo.
- Orçamentos de bytes, ACKs de apresentação, máximo de dois quadros, probe único, consentimento e coordenadas físicas permanecem limitados. Os receptores verificam a dimensão realmente codificada.

## Medições locais

Cada linha usa cinco pares intercalados, alternando qual versão executa primeiro. O valor abaixo é a mediana de CPU da thread. O JSON também registra todos os tempos de CPU, os tempos decorridos e suas medianas. A coluna 7.6 inclui o classificador do Host e, quando aplicável, a cópia RGB e o hash completo antes da compressão. A captura do sistema operacional e a conversão RGB, comuns às duas versões, ficam fora dessas medições.

| Conteúdo / modo | 7.5: pixels / bytes / CPU | 7.6: pixels / bytes / CPU | Pixels nativos exatos |
| --- | --- | --- | --- |
| text_fullhd / auto | 1920 × 1080 / 501,322 / 16.87 ms | 1920 × 1080 / 365,604 / 53.45 ms | Sim |
| text_4k / auto | 1920 × 1080 / 203,674 / 293.65 ms | 3840 × 2160 / 486,653 / 128.14 ms | Sim |
| text_4k / quality | 2560 × 1440 / 463,896 / 402.63 ms | 3840 × 2160 / 486,653 / 145.85 ms | Sim |
| text_4k_no_writer_headroom / auto | 1920 × 1080 / 203,674 / 263.28 ms | 1920 × 1080 / 203,674 / 240.02 ms | Não |
| synthetic_photo / auto | 1280 × 720 / 110,294 / 101.67 ms | 1280 × 720 / 110,294 / 99.37 ms | Não |
| changed_text_scroll / auto | 1920 × 1080 / 203,345 / 257.95 ms | 3840 × 2160 / 486,840 / 228.60 ms | Sim |

Os casos nativos marcados no JSON simulam explicitamente um orçamento de escrita suficiente. Um teste de integração separado exerce a sequência real do Host: primeiro JPEG 4K reduzido com custo de 250 ms, redução para 6 fps, feedback de escrita rápida e, então, PNG 4K nativo. Assim, a redução inicial de frequência não bloqueia para sempre o caminho nativo.

O PNG nativo 4K ocupa mais bytes que o JPEG reduzido do modo automático. Em uma conexão lenta, o custo desses bytes pode superar a economia de redimensionamento. Por isso, ele exige folga medida no escritor; o relatório não promete menos atraso em todas as condições. Já o exemplo Full HD mantém todos os pixels e reduz o arquivo de 501.322 para 365.604 bytes, ao custo de mais CPU na primeira escolha de codec.

As estimativas em 1, 5, 10 e 100 Mbps usam apenas tempo local decorrido e bytes divididos pela taxa. Não incluem RTT, captura, congestionamento, decodificação ou desenho. São aritmética ilustrativa, não resultados de rede; a presença de uma coluna de baixa taxa não significa que a produção liberaria PNG nativo nessa taxa.

## Capturas idênticas

O rastreador de produção recebeu 60 capturas candidatas idênticas em timestamps de uma janela de 5 segundos e liberou 10 envios, com atualização periódica. A diferença de contagem e de bytes no JSON compara essa mesma sequência oferecida; não é uma simulação de agendamento por CPU, nem prova de que uma máquina lenta conseguiria codificar os 60 quadros da versão anterior em 5 segundos. O modo 4K nativo transmite mais pixels por envio, portanto a economia de tráfego total não é universal.

## Reprodução

O benchmark usa Pillow, NumPy e as fontes DejaVu instaladas em `/usr/share/fonts/truetype/dejavu`; NumPy e essas fontes são dependências de medição, não do aplicativo.

```bash
python3 desktop/tests/benchmark_lossless_stream.py --output desktop/docs/quality-7.6 --iterations 5
python3 -m unittest discover -s desktop/tests -p "test_lossless_stream.py"
```

`DESEMPENHO_7_6_0.json` contém resultados e limitações. `comparison_auto_4k.png` e `comparison_quality_4k.png` mostram o mesmo recorte de texto nativo: original, 7.5 e 7.6. Os painéis usam apenas ampliação de 2× pelo vizinho mais próximo, sem nitidez artificial, super-resolução ou pixels gerados.
