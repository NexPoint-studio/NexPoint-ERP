# Ferramentas e execução — Security Discovery

## Complemento da triagem final

Mesmas versões e evidências anteriores preservadas; nenhuma dependência do ERP
atualizada. Esta rodada acrescenta:

| Ferramenta | Execução adicional / limite |
| --- | --- |
| Python do ERP + TestClient | final_auth: 149/149 checks esperados e 1 observação de timing. Dois oracles anteriores corrigidos para 404 legítimo; nenhum código de produto alterado |
| PostgreSQL 17.11 + PostgREST v16.2 oficial Supabase | 90/90 casos HTTP/RPC, rede Docker internal, sem portas host. Não representa Auth/gateway/Edge completos nem PROD |
| ssl/urllib do runtime | final_tls: 5/5 checks com handshake real loopback e certificado QA temporário; apenas cliente Sync, sem CA instalada no sistema |
| cryptography no venv tools-sast existente | Somente geração de certificado/chave QA local ignorada para os testes TLS; não gera nem lê credencial operacional |
| Docker | Inspeção read-only sem rede/capabilities de imports/dpkg na imagem já auditada; não nova build ou deploy |
| Git + Python final_triage | 31 hashes preservados; blob candidato e ausência no tracked atual; classificação por ocorrência sem imprimir valores |
| Fontes upstream/Debian | Leitura de advisories e patch msgpack, não uso de PoCs. Falha ao consultar CVE-2025-7458 registrada; sem inferir status |

Os CSVs finais acrescentam classificação a cada ocorrência dos inventários anteriores.
SAST não foi reexecutado sobre produto inalterado. Secrets dos candidatos finais foram
revistos antes do commit; nenhum cofre/.env real foi lido. Originais TruffleHog retêm
SecretParts: não publicar ou imprimir objetos brutos, mesmo se Raw estiver redigido.
Usar a projeção allowlist `final/supply/secret-triage.json`.

Revisão final de candidatos: regras de release sem ocorrências; Gitleaks com os
mesmos 7 exemplos/fixtures e 3 hashes conferidos por `git cat-file`; TruffleHog com
a mesma URI de fixture. Nenhum alerta novo pendente na árvore candidata. História
permanece com SD-003/004; scan limpo de novos arquivos não elimina esses findings.

Scripts: final_auth/discover.py, final_database/data_api.py, final_tls.py,
final_triage.py e final_coverage.py sob scripts/security. O último gera documentação,
não executa testes. As limitações e técnicas não executadas constam integralmente em
[SECURITY_COVERAGE_GAPS.md](SECURITY_COVERAGE_GAPS.md).

## Ferramentas da rodada anterior (registro preservado)

29/09/2026. Ferramentas em `artifacts/security/`, fora das dependências do ERP.
Nenhuma alteração de lock/runtime do produto. Downloads em GitHub oficial,
PyPI e imagens oficiais; hashes dos scanners de secrets confrontados com release.
Binários Sysinternals tiveram assinatura Authenticode válida. Artefatos grandes ignorados.

| Ferramenta / versão | Disponibilidade e procedência | Execução, resultado e limite |
| --- | --- | --- |
| Python 3.13.3; pytest 8.4.2 | Existentes | Snapshot isolado; 36 testes Sync/Recovery, 114 web e 139 desktop (138 pass, 1 fail). |
| Git 2.49.0.windows.1 | Existente | Baseline, archive, árvore/histórico, diff e revisão dos candidatos. |
| Docker client/server 29.7.2 | Instalado, daemon iniciado | Postgres e Deno sem rede; build e scanner somente de imagem local. |
| CodeQL 2.27.1; python-queries 1.8.11 | [GitHub oficial](https://github.com/github/codeql-cli-binaries/releases/tag/v2.27.1) | 198/198 fontes Python extraídas; security-extended, 32 alertas. Banco local, sem upload SARIF. Não extraiu JS/TS. |
| Semgrep 1.178.0 | [PyPI oficial](https://pypi.org/project/semgrep/1.178.0/) | ERP: 225 regras, 135 arquivos, zero resultados. Nexa: 74 regras, 48 arquivos, 1 falso positivo CORS condicionado a allowlist. Métricas e version-check desativados. |
| Bandit 1.9.4 | [PyPI](https://pypi.org/project/bandit/1.9.4/) | app/control_center/scripts; 113 alertas por padrão, classificados por contexto. |
| pip-audit 2.10.1 | [PyPI](https://pypi.org/project/pip-audit/2.10.1/) | Locks com --no-deps --disable-pip: runtime zero avisos; build um advisory único, duas entradas brutas. |
| Trivy 0.74.0 | [Aqua oficial](https://github.com/aquasecurity/trivy/releases/tag/v0.74.0) | Filesystem/config e imagem construída localmente; 258 candidatos SCA; 1 alerta HEALTHCHECK contextual. |
| Gitleaks 8.30.1 | [Release oficial](https://github.com/gitleaks/gitleaks/releases/tag/v8.30.1) | --redact=100; árvore 7, histórico 864 alertas em 22 commits. Candidatos finais: 10 alertas, 7 fixtures/exemplos e 3 hashes Git. |
| TruffleHog 3.97.9 | [Release oficial](https://github.com/trufflesecurity/trufflehog/releases/tag/v3.97.9) | filesystem + git; --no-verification --no-update. Árvore 1 fixture, histórico 2 candidatos, um deles fixture. Candidatos finais: mesma URI fictícia. Sem uso de credencial detectada. |
| OWASP ZAP 2.17.0 | [Release oficial](https://github.com/zaproxy/zaproxy/releases/tag/v2.17.0) | Proxy local 18080, modo protect, 15 páginas autenticadas, spider/passivo; execução anterior de 4 regras ativas limitada a consulta QA, 3 min, 2 threads. 5 alertas informativos. Nenhuma nova rodada agressiva após reforço de escopo. |
| Nuclei 3.11.1; templates 10.3.1 | [ProjectDiscovery oficial](https://github.com/projectdiscovery/nuclei/releases/tag/v3.11.1) | Templates de misconfiguration/headers/cors/exposure no loopback, OAST/atualização desativados, rate 3/s, concorrência 2. 327 templates carregados, 2 erros de template; 10 observações informativas de headers. |
| Schemathesis 4.28.0 | [PyPI](https://pypi.org/project/schemathesis/4.28.0/) | OpenAPI exportado em memória da aplicação QA; 27 operações, 258 exemplos por rodada, máximo 12 por operação; login/logout fora do gerador para manter autenticação. SD-007 reproduzido independentemente. |
| Hypothesis 6.168.3 | [PyPI](https://pypi.org/project/hypothesis/6.168.3/) | 350 casos de parser de origin e 150 de controles Unicode, além da geração Schemathesis. Sem banco persistente de exemplos. |
| PostgreSQL 17.11 / pgTAP 1.3.4 | [Imagem oficial](https://hub.docker.com/_/postgres), pacote Debian postgresql-17-pgtap | Container nexpoint-security-db sem porta/rede, migração original. 1 contrato umbrella, 11 verificações adicionais e 10 cenários Recovery, incluindo 2 clientes concorrentes. |
| Deno 2.7.5 | [Imagem oficial](https://hub.docker.com/r/denoland/deno) | --network none: 8 testes Edge ERP e 166 testes Nexa (26 steps), sem provider. Nexa --no-check: execução não equivale a typecheck. |
| TCPView/Tcpvcon 4.19 | [Microsoft](https://learn.microsoft.com/en-us/sysinternals/downloads/tcpview) | Captura de conexões restrita ao PID da própria QA: listener e pares loopback. |
| Procmon 4.11 | [Microsoft](https://learn.microsoft.com/en-us/sysinternals/downloads/procmon) | Baixado e assinatura validada, captura nativa não executada; evitar coleta ampla de outros processos. Alternativa: trace Python de restore, ACL de fixture, TCPView e revisão de fonte. |
| Burp Suite | Não utilizado | Não necessário para repetir proxy/DAST já coberto por ZAP; nenhum bloqueio por licença. Teste manual aprofundado não realizado. |
| mitmproxy / Wireshark / tshark | Não instalados para esta rodada | ZAP e Tcpvcon cobrem HTTP local/conexões; contratos usam transports mockados. Inspeção TLS/WebView2 real permanece parcial, sem instalar CA no sistema. |
| Scanner de release do projeto | Existente, Python | Regras de caminhos/extensões e secrets nos candidatos; executado por harness sem leitura de .env/cofres reais. |

## Correções de execução das ferramentas, sem corrigir produto

- Semgrep inicialmente ignorou a cópia dentro de artifacts; repetido com
  `--no-git-ignore`, com contagem efetiva de arquivos registrada.
- CodeQL exigiu instalação do pack de queries e caminho de suite correto.
- pgTAP exigiu `search_path=public,extensions`; roles receberam acesso temporário
  às funções de asserção dentro de transação rollback, sem alteração dos grants do produto.
- Bloqueio total de sockets impediu o socketpair local do asyncio no Windows. Harness
  foi limitado a loopback e a execução anterior inválida foi descartada como prova do produto.
- O venv inicial do Schemathesis herdava pacotes globais e FastAPI 0.115.6. Foi alinhado
  ao lock do ERP (FastAPI 0.141.1/Starlette 1.6.0) somente no venv de ferramentas e repetido.
  O pip relatou conflito com gtts herdado, não utilizado; isso limita reprodutibilidade
  do ambiente de ferramenta, não indica falha de dependência do ERP. Reproduções dos
  findings também executadas com `.venv` do projeto sem alterá-lo.
- Contagens do fuzz variaram entre rodadas apesar de derandomize por operação; não se
  usa a contagem de 500 como número de falhas distintas. Registro final: 258 casos,
  31 respostas 500 com a mesma causa CSRF e um 503 esperado de bridge desativada.
- ZAP precisou ajustar regex de escopo para incluir `/login`. Nuclei teve dois erros
  de templates, sem evidência de cobertura desses templates; demais resultados preservados.
- Teste adicional de expiração Recovery inicialmente violou constraint da própria
  fixture. Corrigida apenas a data de início fictícia para manter expires_at > authorized_at;
  a execução final negou a autorização expirada. Não houve alteração da migration.
- PowerShell registrou NativeCommandError por warnings em stderr em algumas execuções;
  resultado validado pelo JSON/JUnit/log final, sem converter warning em vulnerabilidade.

## Reprodução e retenção

Consultar [README dos scripts](../../scripts/security/README.md) e
[manifesto de hashes](EVIDENCE_MANIFEST.csv). Downloads/bancos/cofres QA não são
versionados. Não copiar outputs brutos: podem conter cookies fictícios, caminhos
locais, endereços de autores Git e snippets de scanner. Os relatórios publicados
contêm somente projeções permitidas e evidência suficiente para revisar as conclusões.
