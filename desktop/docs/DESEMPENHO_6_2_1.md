# AliançaDX 6.2.1 — resposta em conexões lentas

A versão 6.2.0 capturava JPEG de até 2560 × 1440, qualidade 65 e 8 quadros por segundo, sem confirmação da interface remota. O transporte já possuía uma posição substituível para a imagem mais recente, mas bytes aceitos pelo socket e um JPEG parcialmente enviado ainda podiam acumular atraso. A versão 6.2.1 adapta a imagem e o ritmo de captura para conter esse atraso.

## Atualizar

Feche os aplicativos e atualize o **Hospedeiro e o Controlador** nos dispositivos usados. O controle de fluxo completo exige os dois lados em 6.2.1. O ajuste é automático: em uma conexão ruim a imagem perde detalhe para responder mais rápido; após respostas estáveis, o detalhe volta gradualmente. A compatibilidade de conexão com 6.2.0 foi preservada, mas um Hospedeiro antigo conserva seu fluxo original.

## O que mudou

- O Host começa em até 1280 × 720, qualidade JPEG 55 e 12 FPS. Cinco perfis variam de 1920 × 1080/20 FPS até 480 × 270/4 FPS. Esses são limites de captura; a rede, o tempo de compressão e a confirmação da interface podem reduzir a taxa efetiva.
- O Controlador negocia `stream_ready` versão 1 e envia `stream_feedback` somente depois de aplicar/desenhar o quadro atual. `frameId` cresce durante a sessão e não é reciclado ao pausar ou trocar de monitor. O Host usa seu relógio monotônico para medir o ciclo completo de envio/confirmação; não compara relógios de máquinas diferentes.
- O Host mantém um quadro aguardando confirmação. Há uma única tentativa de recuperação após dois segundos; ausência de confirmação não inicia uma sequência ilimitada de imagens. Se o socket ainda tem imagem pendente/em escrita, nem a tentativa é capturada.
- O retorno da interface, a liberação de um diálogo ou um descarte recuperável pode solicitar `stream_resume`. Cliente e Host limitam a frequência. A retomada preserva IDs e as verificações de sessão, compartilhamento, pausa e consentimento.
- Resolução/qualidade/FPS caem rapidamente quando o ciclo fica lento e recuperam um nível por vez após amostras rápidas e estáveis. Não se presume que um envio aceito pelo sistema operacional já foi recebido na outra ponta.
- O transporte Windows acorda por um socket local ao enfileirar trabalho; não espera a consulta ociosa de 50 ms. Bytes TLS já decifrados também são drenados sem esperar dados novos. `SO_SNDBUF` solicita 64 KiB para conter a fila do sistema; o sistema operacional pode ajustar esse valor.
- A interface Windows consulta quadros a cada 10 ms enquanto conectada, e agrupa movimentos a aproximadamente 60 Hz. Cliques, teclas, liberações e arquivos conservam a ordem. O Android evita reconstruir controles e o seletor de monitores em todo quadro.

## Verificação desta edição

- Suíte Desktop: **381 testes**, incluindo TLS local real, filas limitadas, escritas parciais, pausas, retomada, captura, comandos e instaladores.
- Interoperabilidade Android/Windows: **62 testes** com Kotlin de produção, JVM/doubles Android e sockets TLS locais reais. [Relatório](INTEROP_6_2_1.json).
- Compilação Kotlin contra API 36 e **47 verificações** de UI/comandos/feedback/geometria/toque/desenho. O shim de Canvas não representa uma GPU Android.
- Orquestração da assinatura Android: **12 testes**, além da assinatura/verificação criptográfica do APK distribuído.
- Modelo reproduzível de banda/RTT, redução de banda, recuperação e retorno da janela: aprovado. [Resultados e premissas](MODELO_REDE_LENTA_6_2_1.json).

O número Desktop inclui os testes dos instaladores e do transporte; essas contagens não devem ser somadas novamente como ensaios independentes. Não houve execução em Windows físico, instalação em celular físico ou medição em dois provedores de internet.

## Imagem sintética e modelo de rede

`tests/benchmark_low_bandwidth.py` gera uma imagem de desktop com painéis, texto, gráfico e textura. Ele usa os perfis do código de produção e mede JPEG e pacote JSON/base64 reais. Os resultados abaixo descrevem **essa imagem**, não uma economia fixa para qualquer tela.

| Perfil | JPEG (bytes) | Pacote (bytes) |
| --- | ---: | ---: |
| Anterior, 2560 × 1440 / qualidade 65 | 427.171 | 569.691 |
| Inicial 6.2.1, 1280 × 720 / qualidade 55 | 83.285 | 111.174 |
| Economia, 960 × 540 / qualidade 44 | 41.150 | 54.993 |
| Baixo, 640 × 360 / qualidade 32 | 16.171 | 21.689 |
| Mínimo, 480 × 270 / qualidade 25 | 8.562 | 11.541 |

O pacote inicial ficou 80,49% menor nessa fixture. O modelo usa banda útil fornecida, RTT de 120/180 ms, tempos fixos de decodificação/desenho e diferentes buffers finitos para o fluxo anterior (64 KiB, 256 KiB e 1 MiB). Ele verifica que o fluxo negociado mantém no máximo dois quadros aguardando exibição/confirmação, incluindo a tentativa única, e que retoma após uma janela ficar oculta. Também verifica redução e recuperação dos perfis e rejeição de confirmação antiga.

Os tempos de exibição no JSON são **resultados simulados**. O modelo não implementa congestionamento/retransmissão TCP, perdas reais, TLS, custo da captura/compressão, tráfego concorrente de comandos ou retransmissores públicos. Não são uma previsão para a rede do usuário.

```sh
python3 -m unittest discover -s desktop/tests -p 'test_*.py'
python3 desktop/tests/benchmark_low_bandwidth.py --output /tmp/modelo-rede-lenta.json
```

## Limites práticos

Latência zero pela internet não é possível. RTT, sinal Wi-Fi, banda de upload do Host, perdas, CPU e caminho direto/retransmitido continuam influenciando a resposta. Um JPEG ou bloco de arquivo já iniciado em TCP precisa terminar; essa atualização reduz tamanho e fila, sem tornar o transporte preemptivo. Em uma conexão extremamente limitada, o primeiro quadro pode demorar antes da adaptação.

Para medir no ambiente real, teste ambos os lados atualizados, registre a estabilidade ao mover/clicar e ao retornar da tela oculta, compare cabo versus Wi-Fi e repita com e sem transferência de arquivos. A confirmação do desenho na aplicação não é uma medição física do tempo entre ação e pixels no monitor.
