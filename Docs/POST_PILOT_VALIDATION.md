# Validação pós-piloto — 14/10/2026

## Resultado local

- Suíte Python: 1389 casos; primeira execução 1384 PASS e 5 FAIL em 1207s.
- As cinco falhas foram corrigidas e reexecutadas: 5 PASS. Não é uma execução
  única integralmente verde. Evidências `final-suite.xml` e `final-corrections.xml`
  em `artifacts/post-pilot-20261014/`.
- Correções de teste: reabertura autorizada, ausência de diagnóstico sem
  consentimento, allowlist dos novos adapters, composição do transporte sync
  preservando credencial provisionada e fixture de datas independente do dia atual.
- Edge suporte/anexos: 21 PASS em nova execução.
- PostgreSQL isolado: conversa/anexos, RLS, replay, idempotência e paginação PASS;
  updater/rollout: 75 checks PASS, conforme relatórios dos respectivos harnesses.
- Backup/restore atual: 16 PASS em cópias. Script histórico cross-restore bloqueado
  pela proteção de identidade atual; não constitui PASS desse roteiro antigo.
- Regressão de interrupção anexo/outbox: PASS; recuperação após restart e recibo
  impedindo reenfileiramento após purge, com bytes preservados.
- Secret scan e diff check passaram antes do fechamento local.

## Gates externos

Windows A→B deste código: PENDENTE. A VM inicia, porém a credencial QA salva foi
rejeitada; acesso ao guest e validade da licença precisam ser confirmados.
Origem HTTPS QA passou nos checks de isolamento na preparação anterior.

PROD, push, deploy e entrega Nil lav: NÃO EXECUTADOS neste ciclo. Não promover
antes da prova Windows, backup/restore PROD, baseline, auditoria das migrations e
revisão de identidade/canal da instalação. GO final ainda não concedido.

Consulte `POST_PILOT_SUPPORT.md`, `POST_PILOT_UPDATER.md` e
`POST_PILOT_SECURITY.md` para contratos e limitações.
