# Scripts de Security Discovery

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
