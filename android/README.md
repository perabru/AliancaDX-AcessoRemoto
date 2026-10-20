# AliançaDX Acesso Remoto — Android 6.2.0

Controle seu PC pelo celular usando o Host do AliançaDX Acesso Remoto Desktop 6.2.0.
O aplicativo inclui a rede privada por código, sem conta ou login de VPN.

## Instalar

Requer **Android 8 ou superior, de 64 bits** (arm64-v8a; x86_64 para teste).
Este checkout público contém os fontes, sem APK e sem bibliotecas `.so`.
Os instaladores são distribuídos separadamente; consulte os [Releases do projeto](https://github.com/perabru/AliancaDX-AcessoRemoto/releases) quando publicados.
Para construir seu próprio APK, siga **Projeto e reconstrução** abaixo.
No PC Windows,
use o **AliançaDX Host 6.2.0** já fornecido no pacote desktop. Os componentes
de rede estão dentro do APK; não é necessário instalar outra VPN.

Instale sobre a versão anterior, sem desinstalá-la primeiro. O identificador
do aplicativo foi mantido, o código de versão aumentou e a assinatura usa uma
linhagem de atualização vinculada à anterior. Os dados continuam no mesmo
armazenamento. Feche o aplicativo antes de atualizar. A rotação da chave e
os limites nos Androids antigos estão em `docs/DISTRIBUICAO_ANDROID_6_2_0.md`.

## Melhorias desta edição

- Identidade AliançaDX: logo da empresa, verde esmeralda, verde vivo e branco.
- Novo nome no aplicativo, ícone, navegação e informações de privacidade.
- Convites AliançaDX e MiniAnyDesk aceitos pelo aplicativo atualizado.

- Chave de assinatura privada separada do projeto e das senhas de compilação.
- Rotação da assinatura no Android 13+, com prova de continuidade.
- Compilação release sem depuração e SDK alvo/compilação Android 16/API36.
- Painel recolhido **Sobre e privacidade**, com autor, dados e permissões.
- Proteção das confirmações de certificado e arquivos contra toques encobertos.
- Limpeza de temporários identificáveis deixados por um processo interrompido.

O APK continua sendo uma distribuição direta. A assinatura válida não equivale
a publicação ou aprovação pela Google Play/Play Protect, e não há garantia de
ausência de alertas. Consulte as orientações de distribuição no documento acima.

## Conectar em 3 passos

1. **No PC:** abra o Host, deixe-o disponível e clique em **Copiar convite**.
   Envie o convite à pessoa autorizada por um canal de sua confiança.
2. **No Android:** abra **Conectar**, toque em **Colar convite**, confira a senha
   e toque em **Conectar ao PC**. Um convite completo pode preencher a senha;
   se você colar apenas o código, informe a senha manualmente.
3. **No PC:** confira quem está pedindo o acesso e aprove. Mantenha o Host
   e o aplicativo Android abertos durante a sessão.

Em redes diferentes, ambos precisam de internet. A rede procura um caminho
direto e pode usar retransmissores públicos quando necessário. A disponibilidade
desses serviços e as regras de cada rede podem impedir ou interromper o acesso.
O aplicativo não garante comunicação permanente em toda rede.

## Usar a tela

| Ação | Como fazer |
|---|---|
| Clicar | Toque com um dedo. |
| Arrastar no PC | Mantenha/mova um dedo na tela remota. |
| Ampliar ou reduzir | Faça pinça com dois dedos, até 6×. |
| Mover a área ampliada | Desloque os dois dedos juntos. |
| Ajustar toda a tela | Controles > Ajustar. |
| Clique direito ou rolagem | Use os botões em Controles. |
| Teclado e atalhos | Controles > Teclado e atalhos. |
| Tela inteira na horizontal | Tela cheia; Voltar sai desse modo. |
| Monitor | Escolha o monitor no painel Controles. |
| Arquivos | Arquivos > Enviar ou solicitar; o destinatário confirma. |
| Encerrar | Toque em Encerrar. O Host continua sob controle da pessoa no PC. |

Texto para o PC usa caracteres básicos sem acentos, seguindo o teclado do
Host atual. Arquivos têm limite de 100 MiB, escolha de destino e conferência
SHA-256. A pausa feita no Host só pode ser desfeita por quem está no PC.

## Computadores salvos e reconexão

O nome/endereço público do PC pode ser salvo depois de uma sessão autorizada.
**Lembrar código privado neste celular** é uma opção explícita; o código é
cifrado usando AndroidKeyStore. A senha não é gravada nas preferências.
Sem lembrar o código, uma próxima conexão pede um convite atualizado.

A opção **Reconectar após queda de rede** tenta recuperar apenas falhas de
transporte identificadas, por até 2 minutos, com a mesma identidade e nova
aprovação no PC. Encerrar, recusa, senha incorreta ou identidade diferente
interrompem as tentativas. Transferências parciais não são retomadas sozinhas.

## Mesma rede

Abra a opção avançada de conexão por IP, informe o IP selecionado no Host,
a mesma porta (padrão 64321) e a senha. O código é o caminho indicado para
redes diferentes; IPs locais não criam acesso pela internet.

## Segurança e permissões

A identidade TLS do PC é conferida antes de enviar a senha. No modo por
código, o certificado está vinculado ao convite. Cada sessão exige aprovação
no Host. Compartilhe o convite somente com a pessoa que poderá acessar.
O manifesto do Controlador solicita internet e estado da rede. Arquivos são
escolhidos nas janelas do Android; o aplicativo não pede acesso geral ao armazenamento.
`PRIVACIDADE.md` descreve os dados locais, os retransmissores e como remover dados.

## Projeto e reconstrução

O código autoral Kotlin, os recursos, o adaptador C JNI, os scripts, os testes e
as licenças estão neste diretório. APK/AAB, bibliotecas nativas `.so`, chaves
privadas, caches e arquivos grandes de fontes de terceiros ficam fora do Git.
O Gradle wrapper está incluído e fixa o SHA-256 da distribuição Gradle.

A aplicação conserva versão **6.2.0 (62000)**, o identificador
`br.com.perabru.minianydesk.controlador` e os certificados/lineage públicos da
edição distribuída. Nenhuma chave privada está disponível no repositório.

Use JDK 17, Gradle 8.11.1, AGP 8.10.1, Kotlin 2.1.20, SDK 36 e Build Tools 35.0.0.
Antes de compilar o aplicativo, reconstrua as duas bibliotecas JNI de cada ABI:

```sh
# Neste diretório android/, Linux x86_64, Python 3.12+
python3 native-sources/bootstrap_native_sources.py --verify-manifest
python3 native-sources/bootstrap_native_sources.py
# Configure Rust 1.95.0, NDK r27c e protoc 30.2 conforme o guia abaixo.
export ANDROID_NDK_ROOT=/caminho/android-ndk-r27c
export PROTOC=/caminho/protoc-30.2/bin/protoc
bash native-sources/build_android.sh arm64-v8a
bash native-sources/build_android.sh x86_64
./gradlew :app:assembleControladorRelease :app:lintControladorRelease
```

O bootstrap consulta o EasyTier oficial por commit fixo e os pacotes crates.io/Git
registrados no inventário, verifica cada SHA-256 e prepara as fontes locais.
A compilação Cargo seguinte usa `--offline`. Ele não busca bibliotecas Android
pré-compiladas. Veja [native-sources/README.md](native-sources/README.md) e
[BUILD_ANDROID.md](native-sources/BUILD_ANDROID.md) para requisitos, licenças,
espaço de disco e verificações. A primeira preparação da cadeia Android/Gradle
pode baixar ferramentas e dependências Java/Kotlin.

O release produzido pelo Gradle ainda não tem assinatura. O proprietário pode
usar `tools/sign_release.py` com suas chaves externas protegidas. Outros
colaboradores podem gerar um Debug com `./gradlew :app:assembleControladorDebug`,
usando o identificador separado `.debug`, ou assinar uma cópia própria. Uma chave
própria não substitui a identidade do APK distribuído e pode exigir outra
instalação. `signing/` contém somente certificados públicos e prova de rotação.

Os ensaios portáveis atuais usam a pasta irmã [`../desktop`](../desktop), não
um diretório privado da máquina de desenvolvimento. As instruções estão em
[tests/README.md](tests/README.md). Os relatórios em `docs/` são históricos da
edição 6.2.0 e anteriores; a preparação deste checkout não é um novo teste em
aparelho real. `docs/ARCHIVE_SHA256SUMS_6_2_0.txt` descreve o pacote original,
inclusive binários/arquivos de fontes não incluídos neste clone.

## Continuidade da marca

O identificador técnico Android continua `br.com.perabru.minianydesk.controlador`,
para atualizar a instalação anterior. O nome apresentado é **AliançaDX Acesso Remoto**.
MDA6, os identificadores de rede e as chaves de armazenamento não foram renomeados.
Os clientes 6.2.0 aceitam convites completos com o cabeçalho antigo ou AliançaDX.
Ao usar um cliente antigo, envie apenas o código MDA6 e a senha separadamente.
As configurações e a identidade de assinatura foram mantidas; não é necessário
desinstalar a versão anterior. A execução e atualização em aparelho real continuam
pendentes de validação.
