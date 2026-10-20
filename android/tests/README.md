# Ensaios Android e desktop

O ponto de entrada portável para a edição 6.2.0 é
`port_6_1/run_controller_interop.py`; o nome da pasta é histórico. Ele compila
o Kotlin de produção para uma JVM, usa o desktop irmão `../desktop` e exige que
as versões Android/desktop coincidam. As operações TLS e arquivos são reais;
seleção de documentos e injeção de entrada usam doubles. Isso não executa a UI,
AndroidKeyStore, provedores Android, Windows físico ou roteamento público.

Pré-requisitos: JDK 17+, Python 3.11+, Pillow, cryptography e distribuição
Gradle 8.11.1 com bibliotecas do compilador Kotlin. `org.json` para o ensaio pode
ser obtido do Maven Central, sem colocar o JAR no Git:

```sh
mkdir -p /tmp/aliancadx-test-deps
curl --fail --location https://repo.maven.apache.org/maven2/org/json/json/20240303/json-20240303.jar \
  --output /tmp/aliancadx-test-deps/json-20240303.jar
echo '3cf6cd6892e32e2b4c1c39e0f52f5248a2f5b37646fdfbb79a66b46b618414ed  /tmp/aliancadx-test-deps/json-20240303.jar' | sha256sum --check
python3 tests/port_6_1/run_controller_interop.py \
  --desktop ../desktop \
  --kotlin-lib /caminho/gradle-8.11.1/lib \
  --json-jar /tmp/aliancadx-test-deps/json-20240303.jar \
  --output /tmp/aliancadx-controller-qa
```

Os 12 testes da orquestração de assinatura usam ferramentas falsas e nenhum
segredo real:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -p test_release_signing.py -v
```

A criptografia do APK exige a etapa real de assinatura/verificação externa em
`tools/sign_release.py`. Os arquivos de `signing/` são somente públicos.

Os ensaios C/JNI locais estão em `native-sources/tests/run_tests.py`; usam um
FFI falso e JVM real, sem rede EasyTier. As fontes `app/src/androidTest/` e o
laboratório em `port_6_1/` estão disponíveis para futuras execuções Android.
Não interpretamos sua presença ou compilação como teste realizado em aparelho.
Os relatórios em `docs/` preservam a data, versão e limites dos ensaios originais.
