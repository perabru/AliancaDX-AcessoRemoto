# Dependências e fontes correspondentes — Desktop 6.2.0

Este repositório publica o código autoral do AliançaDX e os arquivos de
licenciamento e proveniência usados na distribuição. EXEs, DLLs, wheels e
arquivos compactados de fontes de terceiros ficam fora do Git. O inventário
e os textos integrais de licença são mantidos, sem recortes.

## Caminho recomendado: recuperar o cache da entrega

Os dois instaladores 6.2.0 contêm o mesmo cache completo: Python, 18 wheels e
o bundle de rede com os arquivos de fontes correspondentes. Os links ficam
no README principal. `prepare_assets.py` exige um dos dois SHA-256 publicados,
extrai somente os 30 caminhos conhecidos via 7-Zip e valida cada arquivo
antes de disponibilizar a pasta de cache. Não executa código desses arquivos,
não instala programas e não baixa componentes.

```text
python installer/prepare_assets.py --from-installer /caminho/AliancaDX_Host_Setup.exe --output /cache/aliancadx-6.2.0 --sevenzip /caminho/7z
python installer/build_installers.py --assets /cache/aliancadx-6.2.0 --reuse-assets --output-dir /build/aliancadx-6.2.0 --makensis /caminho/makensis
```

`--reuse-assets` é offline: todos os arquivos precisam existir e coincidir com
os manifestos. Somente copiar a pasta parcial `easytier_bundle` deste Git não
satisfaz essa verificação. Não remova os arquivos de fontes do cache, pois
eles também acompanham os instaladores reconstruídos.

## Manifestos e integridade

- `assets_manifest.json`: versão, URL e SHA-256 do instalador Python, além dos
  18 nomes/versões/hashes dos wheels da entrega.
- `easytier_bundle_manifest.json` e `../easytier_bundle/manifest.json`:
  arquivos exatos do bundle de rede, tamanhos e SHA-256. As duas cópias
  precisam ser idênticas.
- `../easytier_bundle/PROVENANCE.json`: origens, alterações de empacotamento,
  hashes dos executáveis originais e detalhes da substituição de `Packet.dll`.
- `../easytier_bundle/source-inventory.json`: inventário de 671 pacotes,
  incluindo 669 arquivos externos e duas entradas locais; contém URL,
  revisão/hash e provas de conteúdo dos arquivos de dependências.

Os hashes fixam a edição distribuída; não substituem assinatura Authenticode
nem são uma avaliação de reputação por antivírus.

## Origens registradas

Os URLs e hashes abaixo foram preservados da proveniência da edição. A
preparação deste checkout não refez esses downloads nem compilou EasyTier.

| Componente | Origem | SHA-256 do download |
| --- | --- | --- |
| EasyTier 2.6.4, Windows x64 | [release oficial](https://github.com/EasyTier/EasyTier/releases/download/v2.6.4/easytier-windows-x86_64-v2.6.4.zip) | `27af91e270e554709b048bd32327fefd2dfce5062ae1e8701af7550c6f525f84` |
| Fonte original EasyTier 2.6.4 | [tag oficial](https://codeload.github.com/EasyTier/EasyTier/tar.gz/refs/tags/v2.6.4) | `352c0866da709415a837405a6ce4f51b8dfae27e5d5c1da1fb4d8f7338e46795` |
| WinPcap 4.1.3, instalador oficial | [WinPcap](https://www.winpcap.org/install/bin/WinPcap_4_1_3.exe) | `fc4623b113a1f603c0d9ad5f83130bd6de1c62b973be9892305132389c8588de` |
| Fonte WinPcap 4.1.3 | [fonte oficial](https://www.winpcap.org/install/bin/WpcapSrc_4_1_3.zip) | `346a93f6b375ac4c1add5c8c7178498f1feed4172fb33383474a91b48ec6633a` |
| Bibliotecas de importação WinPcap SDK 4.1.2 | [SDK oficial](https://www.winpcap.org/install/bin/WpdPack_4_1_2.zip) | `ea799cf2f26e4afb1892938070fd2b1ca37ce5cf75fec4349247df12b784edbd` |

O `easytier-core.exe` usado conserva o SHA-256 oficial
`da7eb2d24b5416f3d3407636949e964a0750e3f9dc53a828cb6799a57ead445d`.
O CLI não utilizado e os drivers/adaptadores do ZIP upstream são excluídos.
O executável importa `Packet.dll` ao carregar, mesmo em modo `--no-tun`.
A distribuição usa a variante x64 Vista do WinPcap 4.1.3, sem dependência
`NPPTools`, com SHA-256
`ab3cce674f5216895fd26a073771f82b05d4c8b214a89f0f288a59774a06b14b`.
Essa DLL é extraída do instalador oficial como arquivo; nenhum driver é
instalado. A DLL Npcap do ZIP upstream não faz parte desta distribuição.

## Arquivos de fontes fora do Git

| Arquivo presente no cache e nos instaladores | Tamanho | SHA-256 da entrega |
| --- | ---: | --- |
| `easytier-2.6.4-source.tar.gz` | 4.587.778 bytes | `4f1134e7d12a9c2722f78cf6021f8d973d8eb8cd48e1dcbb09ec562bc2087c2b` |
| `easytier-2.6.4-dependency-sources.tar.xz` | 70.232.992 bytes | `e47c98f7ebd0c9fa8b28f477cb5cc23982b8072c8be7de525bf7beb325b240f1` |
| `winpcap-4.1.3-source.zip` | 3.834.716 bytes | `346a93f6b375ac4c1add5c8c7178498f1feed4172fb33383474a91b48ec6633a` |

O primeiro arquivo é um reempacotamento da tag oficial: remove os binários
pré-compilados de terceiros registrados em `PROVENANCE.json` e inclui as
bibliotecas de importação permissivas ali identificadas. Portanto, seu hash
é diferente do tar.gz original do GitHub; não renomeie o upstream e espere
que passe como o arquivo da entrega.

O tar.xz contém 40.367 membros de fontes externos expandidos, com seus
caminhos, conteúdo, modos e links preservados sob namespaces por pacote.
Não é um arquivo oferecido diretamente pelo upstream. O inventário registra
os hashes dos arquivos originais e de suas árvores semânticas; as provas de
cada pacote permitem verificar o conteúdo completo, além do hash do tar.xz.

## Coleta e reconstrução independente dos fontes de terceiros

`collect_easytier_licenses.py` lê o `Cargo.lock` do fonte EasyTier e coleta as
licenças e a cadeia conservadora de dependências, sem executar Cargo ou código
baixado. Os pacotes de registro usam o checksum do lock; dependências Git
usam a revisão do lock. Compare também cada download com o SHA-256 do
`source-inventory.json` da entrega.

```text
python installer/collect_easytier_licenses.py --source /fontes/EasyTier-2.6.4 --cache /cache/cargo-originais --output /build/licencas
```

`repack_easytier_sources.py` expande os arquivos verificados e confere
independentemente cada membro do resultado. Requer `xz`. O arquivo
`locked-sources/supplemental-notices.json` usado como entrada suplementar
está dentro do tar.xz da entrega; recupere-o pelo cache verificado para uma
reconstrução dessa edição.

```text
python installer/repack_easytier_sources.py --inventory /caminho/source-inventory.json --cache /cache/cargo-originais --output /build/dependency-sources.tar.xz --supplemental /caminho/supplemental-notices.json --output-inventory /build/inventario-refeito.json
```

Esses utilitários preservados permitem auditar/reconstruir a coleção de fontes.
Não há promessa de que um novo arquivo comprimido, outro toolchain ou um
novo download GitHub tenha os mesmos bytes do arquivo histórico. Para usar
os manifestos exatos da edição 6.2.0, recupere os arquivos dos instaladores.
Para compilar o próprio EasyTier, siga as instruções e a licença do upstream;
o build NSIS deste projeto empacota o binário oficial, não compila Rust.

## Modo online já existente no builder

Sem `--reuse-assets`, o builder original pode obter o instalador Python pela
URL fixada, conferir seu SHA-256 e obter/buildar wheels das versões registradas.
Ele **não** baixa ou reconstrói sozinho o bundle completo EasyTier. É necessário
fornecer esse bundle previamente, com todos os seus hashes válidos.

```text
python installer/build_installers.py --assets /cache/externo --output-dir /build/externo --makensis /caminho/makensis
```

Wheels construídos de fontes podem ter timestamps ZIP diferentes. Por isso,
o modo online não é a reprodução dos bytes exatos dos wheels históricos.
O caminho recomendado para a edição distribuída é `prepare_assets.py` seguido
de `--reuse-assets`. Não altere manifestos para ignorar um erro de integridade.

## Licenças e limites

`LICENSE.txt`, `NOTICE.txt` e `THIRD_PARTY_LICENSES.txt` permanecem completos
em `easytier_bundle`. O último tem cerca de 6,5MB de texto: a integridade e a
completude dos avisos têm prioridade sobre a preferência por arquivos pequenos.
O uso/redistribuição de cada componente continua sujeito à sua própria licença.

Este checkout não contém chave privada, certificado de assinatura de código
com chave, keystore de produção, senha, cache de build ou binário de execução.
Os relatórios em `docs` são evidências históricas públicas. Logs operacionais e
dumps de ambiente não foram copiados. Nenhum novo teste em Windows ou download
integral de dependências foi realizado durante a preparação do repositório.
