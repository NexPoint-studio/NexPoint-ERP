# Triagem final de dependências e secrets

29/09/2026. Continuação de `277a5b2`, sem atualização de dependências, credenciais,
imagem ou infraestrutura. Inventários originais permanecem intactos. As classificações
atuais por ocorrência estão em FINAL_DEPENDENCY_TRIAGE.csv, FINAL_SAST_TRIAGE.csv e
FINAL_SECRET_TRIAGE.csv; não somar ocorrências com findings SD.

## Dependências: presença não equivale a caminho vulnerável alcançável

| Pacote / advisory | Presença e uso conferidos | Decisão no escopo examinado |
| --- | --- | --- |
| setuptools 80.9.0 / CVE-2026-59890 | Versão no windows-build.lock. build_windows_prod.ps1 executa PyInstaller e empacota ZIP; não publica sdist. pyproject.toml declara backend setuptools, mas isso sozinho não executa o caminho do advisory | SD-006 NOT_APPLICABLE ao fluxo oficial Windows examinado. Reabrir se houver publicação sdist, exclusões MANIFEST ou outro filesystem |
| setuptools 70.3.0 / CVE-2026-59890 e CVE-2025-47273 | Metadata vendorizada no pip da imagem; módulo setuptools de topo ausente, pkg_resources presente. Servidor não usa PackageIndex.download nem gera sdist; rg no app/control_center e build não encontrou esses consumidores | Duas ocorrências NOT_APPLICABLE ao serving da imagem observada; não declaração universal sobre todos os usos de pip/build |
| msgpack 1.1.2 / GHSA-6v7p-g79w-8964 | Somente pip._vendor.msgpack; Unpacker é pip._vendor.msgpack.fallback. Extensão C _cmsgpack ausente. Produto não importa msgpack | NOT_APPLICABLE ao artefato observado: a correção upstream afeta _unpacker.pyx/unpack_template.h, não esse fallback. Sem CVE informado no advisory |
| zlib1g 1:1.2.13.dfsg-1 / CVE-2023-45853 | Pacote presente; nenhum libminizip instalado na imagem consultada. Debian informa que contrib/minizip não é compilado pelo pacote bookworm afetado no nível de fonte | NOT_APPLICABLE ao binário observado; não descartar outros advisories zlib |
| Demais 254 ocorrências | Versões inventariadas pelo Trivy, principalmente pacotes Debian; libsqlite3-0 3.40.1-2+deb12u2 e perl-base 5.36.0-7+deb12u3 confirmados por dpkg. SQLite é usado pelo produto; isso não prova todas as pré-condições de cada CVE | UNRESOLVED por ocorrência: falta correspondência entre função vulnerável, backport, entrada controlável e caminho efetivo. Não promover alertas CRITICAL/HIGH a findings ERP confirmados |

Resultado: **259 ocorrências, 5 NOT_APPLICABLE e 254 UNRESOLVED**. SD-005 permanece
UNRESOLVED/MEDIUM provisório. Nenhuma CVE teve aplicabilidade explorável end-to-end
confirmada no ERP. Isso não significa que as versões estejam livres de vulnerabilidades.
As classificações referem-se ao digest local registrado em SD-005, não à imagem LIVE.

Fontes primárias consultadas: [setuptools, sdist/Unicode](https://github.com/pypa/setuptools/security/advisories/GHSA-h35f-9h28-mq5c),
[setuptools, PackageIndex](https://github.com/pypa/setuptools/security/advisories/GHSA-5rjg-fvgr-3xxf),
[msgpack](https://github.com/msgpack/msgpack-python/security/advisories/GHSA-6v7p-g79w-8964),
[correção upstream msgpack](https://github.com/msgpack/msgpack-python/commit/2c56ddb5d0025ed481d962c0f5d62d19dec7476d),
[Debian zlib/minizip](https://security-tracker.debian.org/tracker/CVE-2023-45853).
A página primária setuptools ainda informa patched=None, enquanto o
[índice revisado](https://github.com/advisories/GHSA-h35f-9h28-mq5c) e o scanner apontam
83.0.0. Preservada a discrepância; nenhuma correção foi instalada ou verificada.
A consulta adicional ao tracker CVE-2025-7458 falhou; não inferimos status do fornecedor
a partir dessa falha. Os demais advisories não receberam leitura individual de fonte
primária: permanecem explicitamente UNRESOLVED, com fonte/versão no CSV original.

Evidência: `artifacts/security/final/image-reachability.json`, obtida em container
local read-only, sem rede, sem capabilities adicionais, executando apenas inspeção
de módulos e dpkg. Não foram executadas provas de conceito de CVEs.

## Secrets históricos

O candidato NpmToken de SD-004 foi localizado no blob exato do commit e arquivo já
registrados. Tem formato UUID de 36 caracteres, sem prefixo moderno npm_. Esse formato
também pode ser um identificador comum: **não prova credencial real nem falso positivo**.
Fingerprint permanece `2f8f8034d985ceb23562971640b7a7ada4827e6becc4e22b660fc65723a60601`.
Busca binária exata nos arquivos rastreados atuais não encontrou o valor. Nenhum
provedor foi consultado; atividade, proprietário e permissões são desconhecidos.

SD-004 fica **UNRESOLVED/INFO**. Se a proveniência futura confirmar uma credencial,
avaliar revogação/rotação com o proprietário em tarefa separada. Ausência no HEAD e
antiguidade não provam inatividade. Não há confirmação de secret PROD real vazado.
SD-003 continua CONFIRMED porque a presença do perfil no Git independe dessa conclusão.

Gitleaks: 871 localizações preservadas (árvore + história), 14 fixtures/exemplos
FALSE_POSITIVE e 857 ocorrências históricas UNRESOLVED quanto a credencial.
TruffleHog: 3 ocorrências nesses dois escopos, 2 URI de fixture FALSE_POSITIVE e
1 NpmToken UNRESOLVED. Não são 857 credenciais distintas nem 857 findings confirmados.
O scanner tem candidatos repetidos entre commits; não foi feito inventário de dados
pessoais de navegador nem descriptografia de material do perfil.

## Qualidade da evidência e alertas estáticos

A inspeção identificou que os JSONL locais antigos do TruffleHog mantêm SecretParts
mesmo após redigir Raw/RawV2. Portanto esses arquivos **não são sanitizados nem aptos
à publicação**. O agente auxiliar relatou saída inadvertida de um candidato na
inspeção anterior; nenhum valor é repetido aqui. Os originais continuam ignorados e
inalterados para preservar evidência; nenhum foi adicionado ao Git. A nova projeção
`final/supply/secret-triage.json` usa uma lista explícita de campos permitidos.
Não utilizar dump de objetos do scanner em logs, relatórios ou futuras inspeções.

SAST: 146 ocorrências, **140 FALSE_POSITIVE**, **1 CONFIRMED** (padrão da linha 108
associado a SD-002), **5 UNRESOLVED** (demais regexes do mesmo grupo não reproduzidas).
FALSE_POSITIVE refere-se à alegação de vulnerabilidade do scanner no contexto revisado,
não à inexistência de limitações de design. Justificativas por grupo permanecem no
SECURITY_FINDINGS.md. Não reexecutamos casos custosos para tentar confirmar os outros
padrões. Nenhum alerta foi fechado apenas porque outro scanner ficou silencioso.

Os 31 hashes do manifesto anterior foram reconferidos sem divergência.
Scripts reprodutores: `scripts/security/final_triage.py` e os harnesses anteriores.
