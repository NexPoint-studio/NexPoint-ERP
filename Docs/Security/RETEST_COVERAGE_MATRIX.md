# Coverage review do reteste

Revis?o das 39 categorias e oito superf?cies originais. Em andamento: build RC, su?te completa final e prepara??o PROD. Nenhuma linha parcial equivale a aprova??o universal. Evid?ncias e limites: [relat?rio](SECURITY_RETEST_REPORT.md).

| Categoria/superf?cie | Estado atual | Evid?ncia | Limite residual |
| --- | --- | --- | --- |
| AUTHENTICATION | PARTIALLY_TESTED | 145 focused; integrated 121 | Real GoTrue JWT and Control Center Supabase authentication; no production timing census |
| AUTHORIZATION | PARTIALLY_TESTED | integrated 121 | 22 tables and server RPCs deny browser roles; finance action matrix remains sampled |
| SESSION | PARTIALLY_TESTED | 145 focused; desktop 4 cycles; integrated 121 | Two persisted sessions, selective revocation and HTTPS logout/replay; not prolonged multi-worker load |
| CSRF | PARTIALLY_TESTED | 145 focused; integrated 121 | Real TLS Unicode rejection without 500 and preserved cookies/origin; not every form/browser combination |
| CORS | PARTIALLY_TESTED | WebView2 8 checks | Actual foreign local origin cannot read application response; managed gateway CORS not certified |
| ORIGIN | PARTIALLY_TESTED | integrated 121 | Local reverse proxy and configured HTTPS origin accepted; foreign origin rejected |
| HOST | PARTIALLY_TESTED | integrated 121; WebView2 | Unexpected Host rejected through TLS; no system DNS rebinding experiment |
| PROXY | PARTIALLY_TESTED | integrated 121 | Actual TLS termination and trusted loopback backend; proxy overwrites spoofed protocol; not Render topology |
| INPUT_VALIDATION | PARTIALLY_TESTED | Existing tests plus full suite pending | Schemas HTML do gerador são pouco restritivos; amostra não exaustiva |
| INJECTION | PARTIALLY_TESTED | Existing tests plus full suite pending | Nem todo sink DOM/SQL/template recebeu fluxo completo |
| FILESYSTEM | PARTIALLY_TESTED | restore run-9ad560bf34 | Only synthetic data/vault directories; different Windows account ACL not tested |
| DATABASE | PARTIALLY_TESTED | Existing tests plus full suite pending | Nem todo constraint/estado financeiro/concorrência foi exercitado |
| SUPABASE | PARTIALLY_TESTED | integrated 121 | GoTrue, Deno handlers, PostgREST and PostgreSQL over real QA gateway/TLS; no managed Kong parity |
| RLS | PARTIALLY_TESTED | integrated 121 | All 22 private tables, FORCE RLS, actual Auth token and temporary transaction grants; no PROD probing |
| RPC | PARTIALLY_TESTED | integrated 121 | Official SQL assertions, scoped signed ingestion and denied browser execution; no exhaustive argument combinations |
| SYNC | PARTIALLY_TESTED | integrated 121; TLS 15 | Actual Python client to Deno to RPC and real ACK; cross-tenant rejected before network |
| OUTBOX | PARTIALLY_TESTED | Existing tests plus full suite pending | Execução prolongada, falha abrupta e espaço em disco não testados |
| ACK | PARTIALLY_TESTED | integrated 121 | Real duplicate ACK plus concurrent identical idempotency and nonce; no exhaustive interleavings |
| OFFLINE | PARTIALLY_TESTED | Existing tests plus full suite pending | Longa indisponibilidade e reconexão real do pacote ausentes |
| RECOVERY | PARTIALLY_TESTED | Existing tests plus full suite pending | Cadeia UI/DPAPI/Edge/PostgREST completa não exercitada |
| NEXA | PARTIALLY_TESTED | Existing tests plus full suite pending | Provider e comportamento de modelo reais não avaliados; --no-check não é typecheck |
| DESKTOP | PARTIALLY_TESTED | desktop 4 cycles; WebView2 8 checks | Remember/Admin Lock/recovery/logout exercised; debug/downloads off and no exposed Python methods; frozen RC pending |
| NETWORK | PARTIALLY_TESTED | TLS run-00c829acb5 15/15; integrated 121 | Sync/Recovery, Nexa and CC reject untrusted/mismatched certificates and redirects; process-only QA CA |
| BACKUP | PARTIALLY_TESTED | restore run-9ad560bf34; desktop 4 cycles | Actual synthetic backup/restore; cross-user confidentiality remains untested |
| RESTORE | PARTIALLY_TESTED | restore run-9ad560bf34 | Two installations, DPAPI vault preservation, runtime identity B, foreign outbox denied before transport, integrity/FK/audit valid |
| LOGS | PARTIALLY_TESTED | Existing tests plus full suite pending | Sem inspeção de destinos/retention reais; não todos os caminhos de exceção |
| OBSERVABILITY | PARTIALLY_TESTED | Existing tests plus full suite pending | Código preserva pending além do orçamento; operação offline longa não medida |
| SECRETS | PARTIALLY_TESTED | scanners run-924479b801; supply run-0e677fbf84 | No new candidates; SD-004 historical UUID still unresolved and not sent to a provider |
| SUPPLY_CHAIN | PARTIALLY_TESTED | Trivy DB 2026-09-30; pip-audit current | 271 image pairs: 16 scoped N/A and 255 unresolved; no confirmed ERP exploit |
| DOCKER | PARTIALLY_TESTED | internal integration containers; previous build smoke | No host ports, synthetic keys; new RC image validation remains pending |
| BUILD | PARTIALLY_TESTED | Existing tests plus full suite pending | Nesta rodada não foi gerado/assinado/executado novo pacote Windows |
| GIT | PARTIALLY_TESTED | remote baseline 9e6bcf18; focused browser guard | No history rewrite; original browser history residual remains open |
| PRIVACY | PARTIALLY_TESTED | Existing tests plus full suite pending | Não acesso a registros pessoais históricos nem aos destinos reais |
| AVAILABILITY | PARTIALLY_TESTED | Existing tests plus full suite pending | Failover e capacidade real fora de escopo |
| RESOURCE_EXHAUSTION | PARTIALLY_TESTED | Existing tests plus full suite pending | Não executar DoS/carga; outros padrões CodeQL unresolved |
| CONCURRENCY | PARTIALLY_TESTED | Existing tests plus full suite pending | Interleavings e locks financeiros/sessão completos não verificados |
| ERROR_HANDLING | PARTIALLY_TESTED | Existing tests plus full suite pending | Nem todos os erros de I/O/cloud/cancelamento possuem fixture |
| CONFIGURATION | PARTIALLY_TESTED | integrated TLS/production middleware | Same production security contracts with synthetic QA values; no effective PROD secrets/settings collected |
| DEPLOYMENT | PARTIALLY_TESTED | integrated TLS/production middleware | No Render deploy or PROD smoke; release/PROD preparation pending |
| WINDOWS_CROSS_USER | NOT_TESTED | Current retest scope | No safe second account/VM; process is not elevated and Windows Sandbox is absent. No host accounts created |
| RESTORE_CROSS_INSTALLATION | PARTIALLY_TESTED | Current retest scope | Completed two synthetic DB/vault restores; identity maintained, four foreign envelopes denied before transport. User/admin-authorized data transfer is accepted by design; cross-user ACL not certified |
| WEBVIEW_RUNTIME | PARTIALLY_TESTED | Current retest scope | Real WebView2 8/8 and four desktop UI cycles; file navigation via privileged QA API is allowed. Custom OS schemes, memory/clipboard and frozen RC remain limited |
| SUPABASE_FULL_STACK | PARTIALLY_TESTED | Current retest scope | Actual GoTrue + Deno + PostgREST + PostgreSQL through QA TLS router; managed gateway/storage/realtime not represented |
| INGRESS_TLS | PARTIALLY_TESTED | Current retest scope | Real local TLS proxy, HSTS/headers/cookies/host/origin/logout/readiness; production ingress not inspected |
| PROCESS_MEMORY | NOT_TESTED | Current retest scope | No dedicated isolated Windows VM/secondary principal. No memory or clipboard of user processes captured |
| NEXA_LIVE_MODEL | NOT_TESTED | Current retest scope | No authorized provider QA fixture; local bridge contracts and TLS exercised, no real provider/model request |
| REAL_FAILOVER_CAPACITY | NOT_TESTED | Current retest scope | Sustained load/DoS outside authorization; bounded failure tests are not capacity certification |
