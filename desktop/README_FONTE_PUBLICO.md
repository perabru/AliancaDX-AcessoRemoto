# AliançaDX Acesso Remoto — Desktop 6.2.0

Código-fonte do Host e do Controlador para Windows 10/11 x64. A interface, o
protocolo, os caminhos de atualização e os scripts originais da versão 6.2.0
foram preservados. O aplicativo exige senha e aprovação no computador remoto.

Os instaladores prontos estão nos links de download do [README principal](../README.md).
Este checkout contém o código autoral, os testes, a marca, os instaladores em
fonte NSIS e os avisos de terceiros. O cache de binários e fontes de terceiros
acompanha os instaladores distribuídos e fica fora do Git.

`README.txt`, `GUIA_RAPIDO.txt` e `COMECE_AQUI.html` são os guias incluídos no
pacote instalado. Quando mencionam Python, bibliotecas ou `dist/*.exe`
"incluídos", referem-se ao pacote distribuído, não a este checkout de fontes.

## Estrutura

| Caminho | Conteúdo |
| --- | --- |
| `host.py`, `controller.py` | Aplicativos que recebem e iniciam o acesso |
| `desktop_ui.py`, `mesh_ui.py`, `assets/brand/` | Interface e identidade AliançaDX |
| `net.py`, `mesh.py`, `transfer.py`, demais módulos | TLS, transporte, arquivos e preferências |
| `installer/`, `installer_python.py`, `instalar_*.py` | Fontes dos instaladores e assinatura legítima opcional |
| `tests/` | Testes unitários, integração local e roteiros de interface |
| `easytier_bundle/` | Licenças integrais, inventário, proveniência e manifesto de terceiros |
| `docs/` | Arquitetura e evidências históricas da edição distribuída |

## Preparar o cache e construir os instaladores

Use Python 3.12+, 7-Zip e NSIS com suporte `amd64-unicode`. Baixe **um dos
instaladores 6.2.0** pelos links do projeto. O comando abaixo apenas extrai os
componentes; não executa o instalador e não usa a rede:

```powershell
python installer/prepare_assets.py `
  --from-installer 'C:\Downloads\AliancaDX_Controlador_Setup.exe' `
  --output 'C:\AliancaDX-cache-6.2.0' `
  --sevenzip 'C:\Program Files\7-Zip\7z.exe'
```

A pasta de saída deve ser nova e ficar fora deste checkout. O helper exige o
SHA-256 exato da versão publicada e confere os 30 arquivos do cache, incluindo
os arquivos completos de fontes e licenças de terceiros. Também é possível
usar o instalador Host da mesma versão. Os hashes estão em `SHA256.txt` e
`installer/release_artifacts_6_2_0.json`.

Depois, a partir desta pasta `desktop`:

```powershell
python installer/build_installers.py `
  --assets 'C:\AliancaDX-cache-6.2.0' --reuse-assets `
  --output-dir 'C:\AliancaDX-build-6.2.0' `
  --makensis 'C:\Program Files (x86)\NSIS\makensis.exe'
```

No Linux, os mesmos scripts aceitam caminhos locais para `7z` e `makensis`.
Os resultados são `AliancaDX_Controlador_Setup.exe` e `AliancaDX_Host_Setup.exe`.
O builder original não foi alterado: valida o cache e congela os hashes dos
fontes durante a compilação. A saída normal permanece sem Authenticode.

Para obter dependências diretamente das origens, entender os arquivos de
fontes correspondentes ou usar o modo online já existente no builder, leia
[installer/FONTES_DEPENDENCIAS.md](installer/FONTES_DEPENDENCIAS.md).

## Desenvolver e testar

No Windows, com o cache verificado disponível:

```powershell
py -3.12 -m venv .venv-dev
.venv-dev\Scripts\python -m pip install --no-index --find-links 'C:\AliancaDX-cache-6.2.0\wheelhouse' setuptools wheel
.venv-dev\Scripts\python -m pip install --no-index --find-links 'C:\AliancaDX-cache-6.2.0\wheelhouse' -r requirements.txt
.venv-dev\Scripts\python -m unittest discover -s tests -p 'test_*.py'
```

Os testes de interface e rede externa em `tests/gui_*.py` e
`tests/mesh_relay_integration.py` são roteiros separados, com os requisitos
indicados em cada arquivo. Os wheels distribuídos são para Windows x64;
para testes em Linux, instale as dependências compatíveis do ambiente.

O protocolo interno e os diretórios `MiniAnyDesk` foram mantidos para
compatibilidade com versões anteriores. Os dados pessoais não são movidos
nem apagados na atualização. A identidade visível é AliançaDX Acesso Remoto.

## Validação e assinatura

As evidências da edição 6.2.0 incluem 337 testes Desktop e 62 verificações de
interoperabilidade. O novo helper do checkout tem quatro testes adicionais e
foi conferido contra o instalador publicado. Os relatórios históricos não
representam uma nova execução em Windows a cada clone deste repositório.

Os instaladores distribuídos não possuem Authenticode. Os scripts de assinatura
usam certificado legítimo fornecido pelo proprietário; não há chave privada ou
credencial no repositório. Consulte `installer/ASSINATURA_WINDOWS.txt`,
`CONFIANCA_WINDOWS.txt` e `VALIDACAO.txt`. A compilação e extração em Linux não
comprovam instalação em Windows, aprovação do Defender ou disponibilidade em
todas as redes.
