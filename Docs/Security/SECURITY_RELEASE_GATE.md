# Security Release Gate

**QA: PASS_WITH_KNOWN_LIMITATIONS.** Preparação de release em andamento;
não liberar deploy Render antes dos artefatos e da preparação Supabase abaixo.

Baseline: `9e6bcf18fed4a7b5350016fc8d80eab59ec0cf42`.
Reteste e limites: [SECURITY_RETEST_REPORT.md](SECURITY_RETEST_REPORT.md).

Já verificados: 145 testes focados; 121 verificações de integração real QA;
15 transportes/certificados; oito checks WebView2 e quatro ciclos desktop;
restore cruzado preservando cofre/identidade e recusando envelopes estrangeiros.

Suíte completa `run-b30b2b4d22`: **1.018/1.018**, zero falhas, erros ou skips;
origem estável e hashes de todos os exports conferidos. Edge Deno: 8/8 com
typecheck habilitado. CodeQL final `run-358639a563`: 32 alertas idênticos.
Scanners `run-85dbfd6a92`: Gitleaks 12/TruffleHog 1/Semgrep 3 conhecidos;
Bandit 217, com 31 alertas adicionais restritos aos harnesses QA (subprocess sem
shell e argumentos controlados, SQL de fixtures, /tmp interno de containers).
Sem novos alertas runtime. 101/101 hashes anteriores preservados.

Nenhum critério FAIL foi confirmado. SD-003 histórico, SD-004/005 inconclusivos,
DPAPI entre usuários, provider real e capacidade continuam explicitamente
limitados conforme relatório. Este resultado permite somente a preparação
aditiva autorizada do Supabase e builds RC, não certifica controles não testados.

Pendências para entrega final:

- builds Windows/Docker RC, evidência de runtime e scans dos artefatos;
- migração de sessões PROD: backup/configuração, schema diff, projeto confirmado
  e aplicação aditiva somente depois do gate QA;
- documentação de release/rollback, commit/push e working tree limpo.

FAIL se houver Critical confirmado, High confirmado sem mitigação, regressão
de finding remediado, quebra de tenant/RLS, secret real exposto, sessão revogada
reutilizável ou integridade comprometida. Limitações de cobertura/advisories
inconclusivos não serão convertidas em testes aprovados nem apagadas.

Dados PROD preservados; migration aditiva de sess?es aplicada e verificada. Edge Functions não mudaram; não há motivo para redeploy
por rotina. Render permanece exclusivamente para a etapa manual posterior.
