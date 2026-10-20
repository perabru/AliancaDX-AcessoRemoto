# Auditoria do runtime Windows — 6.1.1

O pacote remove apenas `easytier-cli.exe`, um utilitário de gerenciamento de
9.973.248 bytes que o aplicativo não executava. O processo de rede continua sendo
o `easytier-core.exe` oficial, sem alteração de bytes, acompanhado da `Packet.dll`
necessária para seu carregamento. A verificação de disponibilidade continua usando
a conexão TLS do MiniAnyDesk.

## O que foi alterado

- O validador do runtime exige o core e, no Windows, `Packet.dll`; não exige o CLI.
- O campo interno sem uso `_cli` foi removido. `verify_runtime()` retorna o caminho
  do core. Os auxiliares de teste foram adaptados à mesma interface.
- Os dois manifestos idênticos, o aviso e a proveniência refletem os dez arquivos
  distribuídos. Fontes e licenças permanecem no pacote.
- O teste de negação do RPC pode usar o CLI oficial como dependência de diagnóstico
  separada, indicada por `--diagnostic-cli`; ele não é parte do runtime instalado.

## Por que a DLL permanece

O diretório normal de importação do core contém onze funções `Packet*`. O diretório
de importações adiadas está vazio. Assim, a DLL precisa estar disponível para o
carregador do Windows antes que a configuração `no_tun = true` seja processada.
Todos os onze nomes importados existem na DLL distribuída. Excluí-la quebraria o
core oficial em computadores sem uma DLL compatível disponível.

O MiniAnyDesk não inclui nem chama um instalador de driver de captura. A inspeção
estática não demonstra que nenhuma função Packet seja chamada em execução. Não
foi realizado teste de execução no Windows nesta auditoria.

## Integridade e assinaturas

| Arquivo | SHA-256 | Constatação |
| --- | --- | --- |
| `easytier-core.exe` | `da7eb2d24b5416f3d3407636949e964a0750e3f9dc53a828cb6799a57ead445d` | Idêntico ao arquivo do ZIP oficial v2.6.4; sem tabela Authenticode embutida |
| `Packet.dll` | `ab3cce674f5216895fd26a073771f82b05d4c8b214a89f0f288a59774a06b14b` | Bytes anteriores preservados; tabela PKCS#7 embutida presente |
| CLI omitido | `d8783e851e944b44a9b71b39fd02f227ec0a2a82b3165c55ead5dd32dcde53a1` | O arquivo anterior era idêntico ao oficial; sem tabela Authenticode embutida |

O ZIP oficial tem SHA-256
`27af91e270e554709b048bd32327fefd2dfce5062ae1e8701af7550c6f525f84`.
A comparação local confirmou o core distribuído. A tabela da DLL contém
certificados Riverbed e de carimbo de tempo Symantec; extraí-los não valida o
digest Authenticode, a cadeia de confiança, a revogação ou o carimbo de tempo no
Windows. A DLL não foi novamente extraída do instalador WinPcap nesta rodada.

Origem do core: [release oficial EasyTier v2.6.4](https://github.com/EasyTier/EasyTier/releases/tag/v2.6.4).
A proveniência da DLL permanece registrada em `easytier_bundle/PROVENANCE.json`,
com a origem [WinPcap 4.1.3](https://www.winpcap.org/install/). O fornecedor informa
que o WinPcap não recebe manutenção; a dependência foi mantida por exigência do
binário oficial e não foi substituída por uma DLL inventada ou por um instalador
de driver.

## Verificações executadas

- 28 testes de `test_mesh.py` passaram. Os dois novos casos verificam que o Windows
  aceita o bundle sem CLI, mas rejeita a ausência da `Packet.dll` e também um
  manifesto que tente omiti-la.
- Os dez arquivos do manifesto conferem em tamanho e SHA-256; nenhum arquivo extra
  existe no diretório. O manifesto empacotado e sua cópia de origem são idênticos.
- A inspeção PE confirma arquitetura x64, onze importações Packet normais,
  compatibilidade dos nomes exportados e diretório de importações adiadas vazio.
- Nove componentes do protocolo/configuração têm AST idêntica à versão 6.1.0:
  validação de porta e fingerprint, HMAC, `HostPairing`, geração e leitura MDA6,
  ID público, configuração do túnel e observação TLS do Host. Esta comparação
  não substitui um novo teste Android/Windows de ponta a ponta.

O MDA6, a configuração do túnel, o vínculo do certificado, a senha e a aprovação
local do Host não foram alterados por esta minimização. A versão do produto é
6.1.1; o formato de rede continua o mesmo.

Resultados reproduzíveis:

- [Relatório de integridade e testes](evidencias_6_1_1/runtime-bundle-audit.json)
- [Saída dos 28 testes](evidencias_6_1_1/runtime-bundle-audit.log)
- [Inspeção PE e certificados](evidencias_6_1_1/pe-runtime.json)

```sh
python tests/pe_static_audit.py --bundle easytier_bundle --output /tmp/pe-runtime.json
python tests/runtime_bundle_audit.py --output /tmp/runtime-bundle-audit.json
```

O segundo comando aceita ainda `--original-project-zip` para repetir a comparação
com o ZIP 6.1.0 e `--official-runtime-zip` para comparar com o arquivo oficial já
disponível localmente. Os scripts não fazem requisições de rede nem executam PE.
O Python `cryptography` é opcional na leitura dos nomes dos certificados.

Não houve varredura antivírus, execução do instalador Windows nem validação em PC
físico. A redução de arquivos sem uso e a verificação de proveniência não garantem
aceitação por Defender, SmartScreen ou outro antivírus.
