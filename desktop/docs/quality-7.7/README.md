# Nitidez e refinamento da imagem - 7.7.0

O modo Automático passa a preservar mais detalhes e as cores do texto nos
recebedores atualizados. A transmissão usa 4:4:4 em todos os seus patamares e
reduz primeiro a frequência quando há pressão moderada. Mais nitidez conserva
uma resolução maior nos patamares de redução. Resposta rápida mantém a política
anterior, que prioriza a resposta e usa menos dados.

Uma tela de texto que fica estável pode receber uma imagem PNG em sua resolução
original, até 3840 x 2160 / 8,3 milhões de pixels. O refinamento não aumenta o
orçamento normal das imagens em movimento: usa um limite separado de 3 MiB
incluindo base64 e espaço para os metadados, apenas com apresentação confirmada,
escrita medida e capacidade recente suficiente. A projeção é conservadora:
inclui o tempo completo de ida, apresentação e volta. Não há expansão apoiada
somente em uma escrita rápida no buffer do sistema.

O refinamento fica sozinho em trânsito. Se a confirmação, a capacidade ou a
memória do aparelho não permitirem, a imagem continua adaptada. A resolução
original não é prometida em toda sessão, e o ajuste não elimina o atraso físico
da rede nem recupera detalhes ausentes em uma imagem recebida menor.

Depois de uma imagem original sem perdas ser apresentada, uma variação do tempo
da confirmação não a substitui por um JPEG menor quando os pixels continuam
iguais. Os aplicativos atualizados negociam `unchangedFrames: true` e recebem
uma confirmação pequena, com o identificador exato da imagem e da captura, a
cada dois segundos. Isso mantém a verificação de saúde da tela sem reenviar todo
o PNG. O pulso não confirma uma imagem nova, não reativa permissões nem libera
entrada. Pausa, consentimento, troca de monitor, retomada e reconexão continuam
exigindo uma imagem válida. Clientes antigos recebem as atualizações de imagem
compatíveis com o comportamento anterior.

Uma mudança pequena, como o cursor de texto piscando, conserva os pixels
originais quando a capacidade recém-confirmada comporta o próximo PNG. Mudanças
reais nunca são descartadas por uma comparação de amostra: o teste de igualdade
usa o resumo criptográfico de todos os pixels RGB e o contexto da captura.

## Validação reproduzível

`VALIDACAO_7_7_0.json` registra ensaios locais com Pillow 12.3.0, cenas sintéticas
e confirmações de entrega simuladas executadas no laço real do Hospedeiro. Não
representa uma sessão em aparelhos físicos nem uma medição de internet.

Para reproduzir a comparação, com o objeto Git da versão anterior disponível:

```sh
git fetch --depth=1 origin tag v7.6.1
python desktop/docs/quality-7.7/benchmark_quality.py --baseline-ref v7.6.1 \
  --output /tmp/qualidade-7-7.json --comparison /tmp/qualidade-7-7.png
```

Os testes de regressão cobrem a resolução original e todos os pixels, a
conservação da nitidez após uma confirmação mais lenta, o cursor de texto
piscando, a negociação dos pulsos, a falta de confirmação, a pressão do
decodificador, a expiração das medições, o limite de bytes e as filas de
transferência. A transmissão JPEG dos clientes antigos permanece idêntica
com a mesma versão do Pillow e o mesmo perfil legado.
