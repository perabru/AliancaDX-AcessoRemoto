# AliançaDX 7.7.1 — fluidez com detalhe progressivo

Esta correção trata o custo excessivo da transmissão introduzido na 7.7.0. A
nitidez de uma tela parada continua importante, mas a compressão e a adaptação
não devem transformar movimento, digitação ou rolagem em uma sequência lenta.

## O que mudou

- A prévia em movimento usa redimensionamento bilinear direto, com redução
  inteira em C quando possível. A captura original permanece intacta para o
  refinamento posterior. Não há uma sequência de três reduções Lanczos.
- Um JPEG acima do orçamento recebe uma correção de precisão na mesma
  resolução antes de tentar diretamente o limite menor. São no máximo duas
  resoluções temporárias, liberadas após o uso.
- Uma oportunidade de PNG em movimento tem limite de tempo, uma única tentativa
  por quadro e espera de dois segundos após uma tentativa sem sucesso. Um
  refinamento estático tem orçamento de tempo total de 200 ms, incluindo ambas as
  tentativas de compressão. Essas verificações são cooperativas, não prazos de
  execução rígidos do sistema operacional.
- Clientes atuais começam com uma prévia de até 128 KiB em Automático ou 192 KiB em
  Mais nitidez. O tamanho cresce com apresentações e escritas realmente
  observadas, no máximo duas vezes por nova confirmação. Três apresentações
  completas rápidas podem autorizar uma sondagem maior, sozinha na transmissão.
  Esse aprendizado pode causar uma variação breve de qualidade e atraso.
- Sob pressão de CPU, escrita ou decodificação, a prévia reduz primeiro seus
  pixels. O modo atual deixa de preservar um quadro grande a apenas dois
  quadros por segundo. Os limites de captura são tetos, não FPS garantido.
- A recuperação restaura a frequência antes de subir novamente a resolução. Os
  perfis de clientes antigos que negociam somente JPEG continuam preservados.

## Nitidez quando a tela para

Textos e interfaces que se estabilizam podem receber um PNG dos pixels
originais, até3840×2160, se a capacidade observada e o orçamento completo do
pacote permitirem. O refinamento fica sozinho em trânsito e tem limite de 3 MiB.
Uma tela original sem perdas já apresentada não é substituída por uma prévia
menor apenas porque o próximo ACK demorou.

A continuação imediata de um PNG exige alteração em uma única faixa de 64 linhas,
amostra visual estável e capacidade recente. Isso favorece pequenas mudanças,
como um cursor de texto, sem classificar uma rolagem de tela inteira como um
detalhe parado. A amostra nunca decide sozinha se houve mudança: os hashes
exatos cobrem todos os bytes da captura.

Fotografias e imagens complexas usam JPEG. Antes de reter uma fotografia parada,
o Host exige ao menos três apresentações e permite que a prévia alcance 1920×1080
ou o nível disponível, ou que o orçamento aprendido se estabilize. Assim, uma
miniatura da primeira conexão não é congelada prematuramente.

## Trabalho em repouso

Após um segundo sem mudança, a leitura da tela reduz para cinco verificações por
segundo. Quando o provedor MSS disponibiliza seu buffer BGRX, uma captura igual
pode ser comparada antes de criar outro bitmap RGB. A mesma captura nunca paga
duas vezes pelo mesmo hash. Não se mantém uma segunda cópia integral do desktop.

Entradas remotas autorizadas acordam a espera longa de repouso e restauram a
frequência ativa. Movimentos de mouse não interrompem a espera do período
ativo, preservando o limite de captura. Alterações feitas localmente continuam
sendo detectadas na próxima leitura; os 200 ms do intervalo de repouso não incluem
captura, processamento, entrega ou apresentação.

O aviso `screen_unchanged` referencia somente uma imagem realmente apresentada e
confirmada. Mantém a saúde da imagem sem gerar ACK artificial, ampliar
permissões ou redesenhar o bitmap. Pausa, novo monitor, retomada e fim da sessão
continuam invalidando as identidades anteriores.

Uma fotografia retida não mede continuamente mudanças na internet. Conteúdo
novo, alteração das configurações ou retomada da sessão atualizam novamente a
evidência de capacidade.

## Validação

`desktop/tests/test_stream_performance.py` exercita os limites de tentativas,
pacotes reais entre 24 KiB e 1 MiB, crescimento por ACK, pressão de CPU, retomada,
retorno da frequência, detecção de um pixel e qualidade progressiva de fotos 4K
com ciclos de 50 ms e 350 ms. Também cobre entrada autorizada e a preservação do
período ativo durante movimento de mouse.

`desktop/tests/test_refinement_quality.py` preserva as provas de pixels originais
sem perdas, cursor de texto, pulso de saúde, negociação, tamanho completo do
pacote e fila limitada. As medições de CPU usam cenas sintéticas e Pillow 12.3.0;
não representam uma garantia de latência em aparelhos ou redes reais.


## Medições comparadas com a 7.7.0

Resultados aprovados na auditoria independente, com Pillow 12.3.0 fixado. O
processamento executou de verdade na máquina de testes. Cenas, capturador,
relógio, rede e confirmações de apresentação são sintéticos.

| Cenário | Medida de CPU | 7.7.0 | 7.7.1 | Redução |
| --- | --- | ---: | ---: | ---: |
| Movimento, JPEG 2560×1440 | Mediana por quadro | 199,38 ms | 99,81 ms | 49,94% |
| Texto 4K parado | Trabalho em 15 s simulados | 3.087,91 ms | 1.579,35 ms | 48,85% |
| Foto 4K parada | Trabalho em 15 s simulados | 9.355,18 ms | 1.574,54 ms | 83,17% |

O teste de movimento alternou três execuções de 20 quadros por versão,
totalizando 120 quadros nas duas versões. Fixou
resolução, codec e orçamento de 1 MiB, e desativou a oportunidade PNG para comparar
o custo completo da conversão, hash, redimensionamento, JPEG, base64 e montagem
do quadro. A interpolação mudou de Lanczos para bilinear: resolução igual não
significa pixels de movimento idênticos.

No repouso, o texto terminou em PNG 4K com RGB exato nas duas versões. A foto
terminou em 1080p na correção, contra 720p na 7.7.0. A versão corrigida fez 75 leituras
por janela de 15 s, sem reconverter o mesmo buffer em RGB. A manutenção de saúde
continuou ativa sem recodificar a tela parada.

A auditoria também verificou 90 cenários pareados de rede modelada, oito trajetos
pelo Host real com ciclos simulados de 25 ms e 350 ms, mudança de um único pixel,
pausa, retomada e rejeição de ACK atrasado. A fila permaneceu com no máximo duas
imagens aguardando ACK e um pacote em escrita. Trinta cargas JPEG de clientes
legados permaneceram idênticas byte a byte.

Os tempos de entrega e os intervalos entre imagens do modelo de rede não são
latências medidas na internet ou em celulares físicos. Os números de CPU e os
hashes exatos dos fontes medidos estão em [EVIDENCIAS_7_7_1.json](EVIDENCIAS_7_7_1.json).
O ajuste final de despertar explícito afeta somente a fronteira entre repouso e
período ativo; a execução sintética sem evento de despertar mantém o mesmo
caminho de processamento, com o caso de legado de 2–5 FPS coberto separadamente.
