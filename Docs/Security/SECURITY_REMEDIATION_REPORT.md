# Security Remediation — QA/local

Marco preservado no remoto antes das alterações:
`f91edf983b151f494c113cfabe8344252c626c73`, branch `main`,
`NexPoint-studio/NexPoint-ERP`. Os relatórios de discovery permanecem como
evidência histórica; este documento registra a remediação, sem substituir suas
limitações de cobertura. Nenhuma alteração de PROD ou deploy está autorizada
nesta etapa.

## SD-001 — revogação de sessão

**REMEDIATED_VERIFIED** em QA/local; gates globais ainda pendentes.
Transição: CONFIRMED → REMEDIATED_PENDING_RETEST → REMEDIATED_VERIFIED.

- Causa: cookie assinado autossuficiente; logout apagava apenas a cópia do cliente.
- Correção: token aleatório por login, somente SHA-256 persistido, consulta de
  titular/hash/expiração em cada requisição autenticada e exclusão no logout.
  SQLite e Supabase compartilham o contrato; não há cache por processo.
- Preservados: flags de cookie, CSRF, Origin, credential_version, usuário ativo,
  papel permitido e independência entre duas sessões. Cookies antigos exigem
  novo login. Expiração absoluta de oito horas; falhas de storage retornam 503.
- Arquivos: `control_center/{repository,local_repository,supabase_repository,web}.py`,
  migration `20260929010000_platform_sessions.sql` e testes correspondentes.
- Regressão antes: `run-7430d87005`, cookie reutilizado após logout retornou 200,
  falhando a expectativa 303 no baseline f91edf9. Evidências em
  `artifacts/security/remediation/tests/` (ignoradas pelo Git).
- Reteste: `run-128f8a6eac`, 62 casos passaram, incluindo dez cenários HTTP,
  dezesseis de repositório e 36 de configuração/health de produção simulada.
- PostgreSQL 17 QA descartável, sem portas publicadas, rede desconectada durante
  os testes: migrations aplicadas e `prod_foundation.sql`/`platform_sessions.sql`
  passaram. pgTAP verifica privilégios, RLS forçada, revogação, expiração,
  constraints e preservação de outra sessão. Logs em
  `artifacts/security/remediation/database/`. A primeira execução do runner
  não incluiu `extensions` no search_path do pgTAP; o runner foi corrigido e
  os dois contratos passaram sem mudança no SQL do produto.
- Regressão do componente: `run-260009c72e`, 146/146, incluindo Control Center,
  Origin, hardening, logs, health e auditoria de sessão desktop.
- Revisão estática independente: nenhuma falha introduzida comprovada.
- Implantação futura: aplicar a migration antes do código. Ela NÃO foi aplicada
  em PROD. ERP desktop/remembered sessions/Admin Lock não foram modificados.

## Pendências da etapa

SD-002, SD-003, SD-007 e SD-008 ainda em processamento; SD-004 e SD-005 serão
reavaliados apenas no escopo já descoberto. SD-006 continua NOT_APPLICABLE.
Suíte completa, scans, builds, consolidação e push das correções ainda pendentes.
