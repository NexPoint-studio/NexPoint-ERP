# Security Release Gate

**QA: PASS_WITH_KNOWN_LIMITATIONS.** Reteste e preparação técnica da RC
1.0.1rc1 concluídos em 30/09/2026. O proprietário confirmou o deploy manual Live
de `da653f9be3101f67cc50de2df2497295bfd774b2`; o
[smoke PROD e aceite técnico](FINAL_PROD_SECURITY_SMOKE.md) passaram com as
limitações conhecidas, sem novo deploy pelo agente.

Baseline: `9e6bcf18fed4a7b5350016fc8d80eab59ec0cf42`.
Reteste e limites: [SECURITY_RETEST_REPORT.md](SECURITY_RETEST_REPORT.md).
Artefatos/rollback: [release](../RELEASE_1.0.1rc1.md).
Preservação: [hashes de evidências](RETEST_EVIDENCE_MANIFEST.csv).

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
limitados conforme relatório. Este resultado permitiu a preparação aditiva
autorizada do Supabase e builds RC; não certifica controles não testados.

Preparação técnica concluída:

- Windows RC: 28 checks do executável imutável, dois ciclos com appdata/cofre/SQLite
  fictícios e proxy que bloqueia tráfego externo. Reconexão/ACK verificados na
  integração QA separada; sem tráfego cloud do executável.
- Docker RC: usuário 10001, read-only, sem rede/portas/mounts; health/login/logout/
  CSRF/replay aprovados. Readiness e Supabase na stack integrada acima.
- Guard de distribuição: 915 arquivos Windows e 245 Docker; scans Gitleaks e
  TruffleHog sem novos secrets. Trivy RC mantém 271 pares: 16 N/A, 255 UNRESOLVED.
- Supabase: nove backups cifrados DPAPI com roundtrip/ACL; migration aditiva
  aplicada. As 21 tabelas/dados/funções/policies preexistentes permanecem iguais.
- Release/rollback documentados; Git/secret scan conferidos e push main verificado.

FAIL se houver Critical confirmado, High confirmado sem mitigação, regressão
de finding remediado, quebra de tenant/RLS, secret real exposto, sessão revogada
reutilizável ou integridade comprometida. Limitações de cobertura/advisories
inconclusivos não serão convertidas em testes aprovados nem apagadas.

Dados PROD preservados; migration aditiva de sessões aplicada e verificada.
Edge Functions não mudaram; não há motivo para redeploy por rotina. Blueprint
mantém autoDeploy false e Environment preservado. O smoke posterior autorizado
passou em 74/74 verificações do painel e 10/10 do retry/ACK real.

Este gate não é certificação integral ASVS/WSTG, aceitação irrestrita de risco
nem certificação integral do runtime PROD. Limitações admitidas pelo escopo continuam
visíveis e não foram convertidas em controles aprovados.
