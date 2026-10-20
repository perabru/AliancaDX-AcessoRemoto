# Biblioteca ADB do instalador USB

`aliancadx-adb.bundle.js` é um bundle ESM local do adaptador autoral
`src/usb-adb-transport.js` com as bibliotecas MIT do projeto
[Tango ADB](https://github.com/yume-chan/ya-webadb). Não carrega código de CDN.

As chaves RSA da autorização ADB existem somente em memória nesta página.
Não é usado o pacote de credenciais persistentes em IndexedDB. Apagar a chave
local ou esquecer WebUSB não revoga as autorizações RSA armazenadas no Android.

Reconstrução, a partir da raiz do repositório:

```sh
cd tools/web-adb_build
npm ci --ignore-scripts
npm run build
npm test
```

O lockfile fixa versões e hashes das dependências. O esbuild usa o binário da
sua distribuição oficial de pacote npm. O bundle foi compilado e conferido
sem conexão a um aparelho físico. A ativação inicial da depuração USB e a
autorização RSA exigem confirmação no celular.

`transport.test.mjs` verifica o bundle local sem instalar dependências.
`transport-sdk.test.mjs` usa o SDK fixado no lockfile para reproduzir a queda
da conexão durante a abertura de `sync:`. Esse segundo teste requer `npm ci`;
nenhum dos testes se conecta a um aparelho físico.

Os textos completos de licença estão em `WEBADB-LICENSES.txt`.

## Componentes incorporados

- `@yume-chan/adb@2.6.4` — MIT
- `@yume-chan/adb-daemon-webusb@2.3.2` — MIT
- `@yume-chan/async@4.1.3` — MIT
- `@yume-chan/event@2.0.0` — MIT
- `@yume-chan/no-data-view@2.0.0` — MIT
- `@yume-chan/stream-extra@2.6.1` — MIT
- `@yume-chan/struct@2.3.2` — MIT
