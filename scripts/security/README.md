# Scripts de Security Discovery

Retestes posteriores, separados do snapshot original: `remediation_tests.py`
exporta código isolado e bloqueia rede externa; `remediation_full_suite.py`
distribui todos os arquivos entre até quatro exports, verificando hashes e JUnit;
`remediation_scanners.py` executa ferramentas selecionadas com projeção sanitizada.
Usam `artifacts/security/remediation/`, sem sobrescrever discovery. Resultados:
[Security Remediation](../../Docs/Security/SECURITY_REMEDIATION_REPORT.md).

Somente auditoria autorizada em cópias descartáveis. Não importar `.env`, cofres,
SQLite real ou credenciais de produção. Não executar o scanner contra URL externa.
Nenhum script aqui corrige o runtime. Resultados e ferramentas ficam em
`artifacts/security/`, que já está ignorado pelo Git.

Baseline auditada: `b09c48e2f5d6e7ecfb9d1aef91ef95753cf1f2c6`.
Criar a fonte com `git archive <baseline>`, extraída em `artifacts/security/source`;
não copiar a pasta de trabalho inteira (ela contém dados locais ignorados).
Os scripts assumem essa estrutura e os pacotes documentados em
[SECURITY_TOOL_MATRIX.md](../../Docs/Security/SECURITY_TOOL_MATRIX.md).

| Script | Escopo e saída |
| --- | --- |
| web_api/qa_server.py | Servidor 127.0.0.1:18771, dois tenants TEST, contas geradas, SQLite exclusivo por execução; arquivo de credenciais apenas de QA ignorado. |
| web_api/discover.py | 135 verificações HTTP no endereço fixo acima. Modifica apenas notas/incidentes fictícios. |
| web_api/reproduce_sessions.py | Replay de cookie fictício em memória após logout/troca de senha; flags e códigos apenas. |
| web_api/unicode_csrf.py | Três entradas CSRF sintéticas via ASGI; não grava credenciais. |
| web_api/bounded_query.py | Uma consulta ASGI de 55.297 caracteres, sem rede, concorrência 1. |
| web_api/property_fuzz.py | 12 exemplos/operação, autenticação QA, 500 propriedades origin; requer venv Schemathesis com runtime alinhado ao lock. |
| web_api/zap_scan.py | Registro reproduzível da rodada ZAP anterior: localhost, protect, limite de duração e quatro regras. Não necessário reexecutar para ler relatório; proxy deve ser iniciado exclusivamente para QA. |
| desktop_network/bounded_sanitizer.py | Processos de no máximo 5 s, sem alvo HTTP; texto TEST_ONLY não contém chave privada. |
| desktop_network/run_discovery.py | Regressões no snapshot com env limpo e rede externa bloqueada. `--web` seleciona 114 testes web; sem opção seleciona 139 desktop. |
| desktop_network/trace_restore.py | Tipos/linhas/errno da falha do teste de restore, sem mensagem contendo dados. |
| desktop_network/backup_cases.py | SQLite gerado, cópias malformadas e restore offline em subprocesso; nunca lê banco do usuário. |
| database_sync/run_python.py | 36 testes Sync/Recovery, transportes mockados, sem conexão de rede. |
| database_sync/discovery.sql | 11 asserções pgTAP, transaction rollback; aplicar apenas no Postgres QA descartável. |
| database_sync/recovery_cases.py | Container nomeado e sem rede, fixture upstream; casos negativos/concorrência. Exige banco vazio inicialmente. `--resume-qa-fixture` somente retoma a fixture auditada, não é opção para banco real. |
| export_inventory.py | Projeção allowlist dos metadados de scanners para CSV; nenhum Match/Secret/snippet/cookie copiado. |
| review_candidates.py | Scanner de release nos candidatos Git, sem invocar leitura de `.env`; prepara cópia para Gitleaks/TruffleHog finais. |
| final_auth/discover.py | 149 checks + timing delimitado em ASGI/SQLite fictício, diretório novo por execução. Resultado esperado inclui falha conhecida SD-001; não é certificado de segurança. |
| final_database/data_api.py | PostgREST 16.2 + Postgres 17 em rede Docker interna, containers exclusivos rotulados e encerrados. 90 casos HTTP/RPC/RLS. Preservar data-api.json antes de repetir: o harness original usa esse nome fixo. |
| final_tls.py | Cinco verificações com TLS real loopback do Sync. Certificado QA gerado no venv tools-sast, sem instalar CA no sistema; diretório único. |
| final_triage.py | Confere 31 hashes anteriores; classifica cada ocorrência em CSVs FINAL_ separados; candidato histórico somente em memória, saída allowlist sem valores. |
| final_coverage.py | Gera matriz de fronteiras e gaps a partir da revisão documentada, incluindo linhas parciais/não testadas ASVS/WSTG. Não executa scanner. |
| final_review.py | Export único dos candidatos Git, regras de release + Gitleaks redigido e TruffleHog sem verificação; nunca salva objetos brutos TruffleHog. |

Os scripts SQL não devem ser apontados ao Supabase remoto. O laboratório desta
rodada usou `nexpoint-security-db`, Postgres 17.11, rede none e sem portas publicadas.
A migração original foi aplicada nesse container, com papéis/auth.jwt mínimos de
teste. Isso não substitui validação de uma stack Supabase completa.

Comandos locais típicos, após preparar snapshot/ferramentas:

```powershell
.venv/Scripts/python.exe scripts/security/web_api/reproduce_sessions.py
.venv/Scripts/python.exe scripts/security/web_api/unicode_csrf.py
.venv/Scripts/python.exe scripts/security/desktop_network/run_discovery.py --web
.venv/Scripts/python.exe scripts/security/desktop_network/backup_cases.py
.venv/Scripts/python.exe scripts/security/export_inventory.py
```

Não executar regressões apontando `--basetemp` para pasta existente importante:
pytest pode limpar a pasta. Os defaults são exclusivos sob artifacts/security.
Não publicar logs/raw outputs. Preserve falhas anteriores e registre a razão de
reexecução; uma nova execução não apaga findings.

**Retenção:** os JSONL antigos TruffleHog conservam SecretParts e não podem ser
tratados como sanitizados. Não imprimir/republicar esses arquivos; usar apenas a
projeção de final_triage. Nenhum raw, banco, certificado/chave QA ou container export
entra no Git. A nova tarefa não autoriza reexecutar testes ativos/custosos antigos;
a revisão final utiliza os resultados preservados e testes defensivos delimitados.


## Reteste e expansão autorizados em 30/09/2026

Os novos runners preservam a rodada anterior e escrevem diretórios únicos em
`artifacts/security/retest/`, salvo a projeção sanitizada oficial de dependências.
Não executar scripts históricos que sobrescrevam evidências por rotina.

| Runner | Escopo |
| --- | --- |
| retest_runtime.py desktop | Export novo, appdata fictício, quatro ciclos GUI existentes; sockets Python somente loopback |
| retest_webview.py | WebView2 real com perfil e páginas locais; sem memória/clipboard e com abertura do navegador interceptada |
| retest_restore.py | Dois bancos/cofres DPAPI fictícios; rede proibida; rastreia identidade e envelopes rejeitados antes do transporte |
| retest_database.py + retest_database_extended.py | Rede Docker interna exclusiva; 22 tabelas, Auth, Edge, gateway QA TLS, PostgREST e clientes reais; containers próprios encerrados no finally |
| retest_tls.py | Quinze checks TLS em clientes atuais; CA apenas no processo; sem trust global |
| retest_codeql.py | Export validado de candidatos, ferramentas/queries locais, novo banco CodeQL e evidência preservada |
| retest_supply.py | Reconcilia metadados Trivy e histórico somente em memória; nunca imprime o candidato nem o usa em provedor |

Os runners não recebem configuração ou dados PROD. Código de harness não altera
contratos do produto. Resultado da etapa: `Docs/Security/SECURITY_RETEST_REPORT.md`.

O executável congelado RC recebeu roteiro específico em artefato local ignorado,
com hash em `Docs/Security/RETEST_EVIDENCE_MANIFEST.csv`: appdata/DPAPI/SQLite
fictícios, proxy deny-only, dois ciclos e 28 checks. Esse roteiro não deve ser
reutilizado apontando dados reais nem sem o bloqueio de rede descrito no relatório.
Builds/backups/outputs e scripts transitórios de execução permanecem fora do Git.
