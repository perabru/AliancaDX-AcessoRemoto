# Assinatura Android 6.4.0

Esta pasta contém somente certificados públicos, o histórico de rotação
(`minianydesk-release.lineage`) e a configuração pública de assinatura.
Não contém chaves privadas nem senhas.

A chave antiga apareceu nos fontes distribuídos da versão 6.1.0 e deve
ser tratada como exposta. A nova chave RSA de 3072 bits fica no PKCS12 privado,
protegido por senha forte. Importar a chave antiga para esse arquivo preserva
o acesso a ela, mas não desfaz sua exposição anterior.

O PKCS12 privado tem dois aliases: `minianydesk-legacy` e
`minianydesk-release`. Mantenha cópias seguras do arquivo e da senha em locais
separados. Não envie esses arquivos para GitHub, um site ou o ZIP público de
fontes. Não gere outra chave para substituir esta em uma atualização.

A versão 6.4.0 conserva exatamente os certificados e a linhagem preparados
na 6.1.1. A mudança de marca não gera uma nova chave nem troca o aplicativo.

## Compatibilidade das atualizações

O pacote e a identificação do aplicativo são mantidos. O APK usa a chave
anterior no Android 8–12L (APIs 26–32) e a nova chave a partir do Android 13
(API 33), com prova de rotação v3.1. Isso permite verificar a cadeia esperada
de atualização; a instalação real ainda deve ser testada em aparelhos.

O uso da chave exposta no Android antigo é uma limitação de segurança que
permanece enquanto essa compatibilidade for necessária. O histórico permite
preservar dados instalados; compartilhamento de UID, permissões por assinatura,
rollback para a chave anterior e autenticação privilegiada estão desativados.

Um certificado de aplicativo Android é uma identidade de assinatura. Criá-lo
não torna o aplicativo verificado pelo Google e não garante ausência de avisos
do Play Protect. Nenhuma proteção do aparelho deve ser desativada para validar
esta entrega.

## Produzir um APK assinado

Use o APK **release sem assinatura** gerado pelo Gradle, Python 3.11 ou superior,
o JDK 17 com `keytool` e os Android SDK Build Tools oficiais. Primeiro configure
os caminhos dos arquivos privados, sem escrever a senha no comando:

```bash
export MINIANYDESK_ANDROID_KEYSTORE="/caminho/privado/MiniAnyDesk_Assinatura_Privada.p12"
export MINIANYDESK_ANDROID_PASSWORD_FILE="/caminho/privado/Senha_Assinatura_Android_MiniAnyDesk.txt"
python tools/sign_release.py \
  --input app/build/outputs/apk/controlador/release/app-controlador-release-unsigned.apk \
  --output entregas/AliancaDX_Acesso_Remoto_6.4.0.apk \
  --build-tools "$ANDROID_HOME/build-tools/35.0.0"
```

No PowerShell, configure os mesmos dois nomes em `$env:`. Também é possível
fornecer `MINIANYDESK_ANDROID_PASSWORD` pelo ambiente privado do processo, em
vez do arquivo de senha; use somente uma das duas fontes. O arquivo de senha
contém exatamente uma linha. Em Linux, arquivos privados precisam de permissões
restritas ao proprietário.

O script usa o JAR oficial do apksigner diretamente pelo Java, inclusive no
Windows, sem executar um arquivo `.bat` ou interpretar caminhos por um shell.
Ele verifica o pacote, a versão, a atividade de abertura, os dois certificados e o histórico;
alinha as bibliotecas para 16 KB antes de assinar; habilita v2/v3, com rotação
na API 33; e verifica os intervalos completos 26–32, 33–37 e 26–37, além das APIs
32, 33, 36 e 37 isoladas, com `-Werr` (avisos tratados como erros). Um auxiliar
Java usa a biblioteca oficial apksig para conferir os certificados reais dos
blocos v2/v3/v3.1 e o histórico incorporado, cobrindo a identidade antiga na API 26.
Essas verificações são estáticas, sem simular uma instalação nesses sistemas.

Limitar artificialmente o apksigner 35 somente à API 26 gera um aviso sobre a
referência de proteção contra remoção do bloco v3, que surgiu depois dessa API.
Esse exame limitado não é apresentado como um teste estrito aprovado. Os
intervalos completos reconhecem o v3 e verificam também a compatibilidade
v2 da API 26, sem filtrar avisos nem modificar a proteção da assinatura.
A assinatura não fixa um SDK máximo. Ele recusa APK depurável ou `testOnly`, entrada já assinada, arquivos privados na pasta
pública, chaves inesperadas e substituição de arquivos existentes. O relatório
ao lado do APK contém hashes públicos e os resultados das verificações.

Os arquivos finais são publicados completos, sem substituição, por links
atômicos. Use uma pasta local em um sistema de arquivos que suporte hard links
(por exemplo, NTFS ou ext4). Em falha ou interrupção, somente os arquivos
publicados por essa execução são removidos. A inspeção do APK verifica nomes
de arquivos de chave, a presença da senha de produção e cabeçalhos privados
em entradas textuais; isso não é uma garantia universal de ausência de segredos.

Uma nova versão exige revisar a configuração pública e os controles de versão
do script. Isso não exige gerar uma nova chave. Não modifique os bytes de um
APK depois da assinatura; produza uma nova versão assinada se houver alterações.
