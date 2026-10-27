# Imagem e resposta automáticas — AliançaDX 7.2.0

A transmissão ajusta resolução, compressão e ritmo usando o envio e a apresentação
dos próprios quadros. Não executa um teste externo da velocidade da internet.

Quando há capacidade e estabilidade, a qualidade sobe gradualmente até Full HD
(1920 × 1080). Quando aumentam o congestionamento ou o custo de processamento,
o aplicativo reduz o peso das imagens e os quadros em trânsito. A recuperação
exige amostras estáveis para evitar oscilações. Uma latência de base alta é
separada do excesso de atraso para não reduzir a imagem apenas por um ping alto.

## O que mudou

- Captura BGRA convertida diretamente em RGB no Pillow, preservando os pixels
  e evitando a conversão intermediária custosa do MSS.
- JPEG com maior detalhe e cor 4:4:4 nos perfis superiores; os perfis econômicos
  continuam reduzindo resolução, qualidade e amostragem para conter os bytes.
- Limite de aproximadamente 1 MiB por quadro no protocolo, incluindo margem
  para metadados. Imagens complexas são reduzidas antes de entrar na transmissão.
- Até dois quadros em trânsito, somente após negociação explícita e observações
  favoráveis. Congestionamento reduz a janela. Clientes anteriores mantêm um.
- Confirmação cumulativa apenas de um quadro conhecido que foi apresentado;
  quadros de uma captura antiga e confirmações futuras são rejeitados.
- Redimensionamento Windows preparado no worker; a thread Tk apresenta a imagem
  e continua disponível para os comandos. Escala 1:1 evita cópias extras.
- PWA com camada própria para o cursor, canvas limitado aos pixels úteis,
  decodificação sobreposta à apresentação e agrupamento de movimentos pendentes.
- Android preserva ARGB_8888, resolução Full HD e filas limitadas, e participa da
  negociação de dois quadros e das confirmações após o desenho.

## Compatibilidade e validação

O protocolo de transporte permanece 3.0.0. A negociação de fluxo continua na
versão 1, com campos opcionais. Atualize Hospedeiro e Controlador para aproveitar
o conjunto completo. Aprovação no PC, TLS, identificação da captura, sessões
independentes, acessos salvos e mousepad permanecem obrigatórios ou disponíveis.

Os testes incluem JPEGs reais, variação determinística de banda e RTT, controle
de filas, interfaces Tk/TLS em loopback e apresentação Android/PWA. As medições
de captura e JPEG estão em [DESEMPENHO_7_2_0.json](DESEMPENHO_7_2_0.json).
Os cenários sintéticos não representam homologação em Windows, Android ou iPhone
físicos, nem uma medição da internet do usuário.

JPEG 4:4:4 pode aumentar o tamanho do quadro ao melhorar texto e cores. Dados já
entregues ao TCP não podem ser cancelados instantaneamente quando a banda cai.
As otimizações reduzem trabalho e acúmulo evitáveis; não garantem latência zero,
uma redução percentual universal ou a mesma nitidez em qualquer conexão.
