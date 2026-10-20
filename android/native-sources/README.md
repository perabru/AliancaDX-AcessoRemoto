# Transporte nativo Android: fontes e reconstrução

Este diretório contém o adaptador autoral `easytier_jni.c`, preparação/build,
testes, licenças e inventário. O núcleo de rede é **EasyTier 2.6.4**, de seus
contribuidores, sob LGPLv3; não é apresentado como código autoral AliançaDX.
Não há `.so`, APK ou arquivos grandes de terceiros neste checkout.

O ponto de entrada é [BUILD_ANDROID.md](BUILD_ANDROID.md). Antes do Gradle,
execute, a partir de `android/`:

```sh
python3 native-sources/bootstrap_native_sources.py --verify-manifest
python3 native-sources/bootstrap_native_sources.py
# Instale/configure Rust 1.95.0, NDK r27c e protoc 30.2 conforme o guia.
export ANDROID_NDK_ROOT=/caminho/android-ndk-r27c
export PROTOC=/caminho/protoc-30.2/bin/protoc
bash native-sources/build_android.sh arm64-v8a
bash native-sources/build_android.sh x86_64
```

O bootstrap usa HTTPS, commit imutável e SHA-256 do projeto oficial; cada
dependência é conferida pelo hash em `source-inventory.json`. O `Cargo.lock`
original está em `Cargo.lock.upstream`. Os dois arquivos de fontes do pacote
distribuído não são recriados byte a byte: seus hashes históricos continuam
em `NATIVE_PROVENANCE.json`. Fontes baixadas ficam em `.build/` e os arquivos
compactados em `.downloads/`, ambos ignorados pelo Git. O bootstrap não obtém
bibliotecas Android pré-compiladas. Artefatos Windows e keystore GUI não usados
do arquivo upstream são excluídos durante a extração.

Os builds usam o mesmo núcleo upstream, recursos Cargo e C ABI. A preparação
altera caminhos/workspace/recursos de build, sem alterar fontes Rust de runtime.
O aplicativo configura `no_tun=true` e encaminhamento TCP local; não cria VPN
de sistema. O contrato Kotlin/JNI está em [JNI_README.md](JNI_README.md).

Após reconstruir, execute `verify_binaries.py` para alinhamento ELF/exportações.
Ele falha se qualquer uma das quatro bibliotecas estiver ausente e grava
`NATIVE_REBUILD_VERIFICATION.json`, sem substituir a proveniência histórica.
Não se exige hash idêntico ao binário anterior: caminhos e ferramentas podem
mudar os bytes. É necessário validar a cópia reconstruída separadamente.

Licenças: [LICENSE.txt](LICENSE.txt), [NOTICE.txt](NOTICE.txt) e
[THIRD_PARTY_LICENSES.txt](THIRD_PARTY_LICENSES.txt). Você pode modificar,
recompilar e substituir a biblioteca sob essas licenças. Não é necessária uma
chave de produção para compilar fontes; use sua própria assinatura para instalar
uma cópia modificada. Uma assinatura diferente não atualiza a instalação oficial.
