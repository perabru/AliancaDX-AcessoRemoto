# Distribuição e avisos do Android — 6.2.0

AliançaDX Acesso Remoto usa a identidade visual fornecida da empresa.
Esta edição é um APK de distribuição direta. Não foi cadastrada, publicada ou
aprovada pela Google Play, Android Developer Console ou Play Protect nesta tarefa.
Não existe uma alteração de nome, certificado ou código que garanta ausência de
avisos. Não foi enviada cópia do aplicativo para serviços externos de análise.

## O que foi preparado

- Pacote `br.com.perabru.minianydesk.controlador`, versão 6.2.0, código 62000.
- Build release, sem depuração, SDK alvo/compilação 36 e Android mínimo 8/API26.
- Apenas as duas permissões de rede; seleção de arquivos pelo Android.
- Autor e tratamento de dados descritos em Sobre e privacidade e PRIVACIDADE.md.
- Assinatura privada separada das fontes, com rotação documentada em signing/.
- Fontes, avisos e licenças correspondentes ao componente nativo EasyTier.

## Assinatura e atualização

A edição 6.2.0 conserva os certificados e a linhagem da 6.1.1. A marca exibida
é AliançaDX Acesso Remoto; o identificador técnico permanece igual para
continuidade das atualizações. Não foi gerada outra chave nesta mudança de marca.

O ZIP antigo da versão 6.1.0 incluía a chave privada de testes. Ela deve ser tratada como
exposta. A edição nova usa uma chave de produção privada, protegida e entregue
separadamente ao proprietário. O código público inclui apenas certificados
públicos, fingerprints e prova de rotação.

No Android 13/API33 ou superior, a assinatura v3.1 usa a chave nova e a prova de
rotação vincula a identidade anterior. No Android 8–12L/API26–32, a assinatura
anterior permanece para compatibilidade de atualização. Esses sistemas antigos
continuam aceitando aquela chave e não recebem a mesma proteção contra seu
comprometimento. Não habilitamos a capacidade de retornar à assinatura antiga
nos sistemas que adotam a rotação.

Quem obtivesse a chave antiga também poderia assinar outro APK ou criar uma
linhagem concorrente antes da primeira atualização. Por isso, obtenha esta
migração pelo canal confiável e confira o hash publicado e o certificado novo.
A rotação protege a continuidade a partir da instalação da chave nova; não
transforma retroativamente todos os APKs antigos em versões autênticas.

Não desinstale a versão anterior antes de tentar a atualização. A identidade do
pacote foi mantida. O resultado da tentativa de validar atualização e dados
está registrado em docs/VALIDACAO_6_2_0.md; não extrapole os resultados
para versões de Android ou fabricantes que não foram testados.

Guarde o backup privado da assinatura e sua senha fora do código e de qualquer
repositório público. Sem as chaves corretas, futuras atualizações podem ficar
impossíveis. Não compartilhe esses arquivos junto do APK ou do projeto.

## Avisos diferentes exigem ações diferentes

Um pedido para autorizar o navegador a instalar APKs é uma autorização da origem
instaladora. Ele pode existir mesmo com uma assinatura Android válida.

Um convite do Play Protect para analisar um app pouco conhecido não equivale a
uma detecção confirmada. Uma classificação como prejudicial exige investigar o
nome exato do alerta e o hash do arquivo; existe um canal oficial de contestação
para o desenvolvedor. Nada foi submetido automaticamente nesta entrega.

A verificação da identidade do desenvolvedor e o registro do pacote são etapas
externas feitas pelo titular da conta. No Brasil, a fase iniciada em setembro de
2026 aplica-se às lojas participantes; a FAQ oficial esclarece que o APK direto
ainda não está coberto por essa fase inicial. Prepare a verificação antes da
expansão global prevista para 2027. Consulte a documentação atual ao publicar.

## Para publicar na Google Play

O SDK alvo 36 atende ao requisito de API vigente na preparação desta edição,
mas não comprova aprovação. Ainda são necessários conta do desenvolvedor,
identidade, ficha do aplicativo, política pública de privacidade com contato,
declarações de dados e eventuais testes exigidos pela conta. A assinatura e a
linhagem da versão já distribuída precisam ser consideradas ao configurar Play
App Signing; não aceite uma nova identidade incompatível sem planejar a migração.

## Fontes oficiais

- https://developer.android.com/tools/apksigner
- https://developer.android.com/studio/publish/app-signing
- https://developer.android.com/distribute/marketing-tools/alternative-distribution
- https://developers.google.com/android/play-protect/warning-dev-guidance
- https://developer.android.com/developer-verification/guides/faq
- https://developer.android.com/google/play/requirements/target-sdk
