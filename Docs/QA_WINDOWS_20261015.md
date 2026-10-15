# Gate Windows QA — 15/10/2026

Status: **NO-GO**. Somente laboratório local; sem push, deploy, PROD ou alteração
da entrega Nil lav.

## Ambiente e execução confirmados

- VM antiga preservada, desligada e desconectada; nova VM com disco separado.
- Conta local `qa-clean`: primeiro login e PowerShell Direct confirmados.
- Windows Evaluation ativado oficialmente: LicenseStatus 1 e 90 dias disponíveis.
  O desligamento anterior foi registrado por wlms com licença expirada.
- Conexão temporária usada para ativação Windows/WebView2 removida antes do ERP.
- Origem QA HTTPS: 18 checks PASS no host e HTTP 200 dentro da VM.
- Instalador A 1.1.0 de `be0742475703651b2244c41cf7e91832aa99a0a9` reutilizado;
  hash confirmado, instalação exit 0, nenhum SQLite operacional anterior.
- Ativação QA, Empresa, ausência de login operacional, Admin Lock e Recovery: PASS.
- Recovery: senha anterior rejeitada, nova aceita, autorização consumida uma vez.
- Cliente fictício criado e conferido. SQLite: integrity_check ok, zero violações FK.
- Credencial DPAPI decifrada com a conta QA; baseline preservada para comparação B.
- Suíte final: 1389 PASS, zero falhas/erros/skips, 1026,562 s.
  Houve antes uma falha por evento legítimo de latência em teste que exigia
  exatamente um evento. Ajustado apenas o teste de isolamento; módulo NEXA 30 PASS
  e suíte integral posterior verde. Instaladores não foram modificados.
- Secret scan: 541 candidatos PASS. git diff --check PASS.

## Falha bloqueante: heartbeat da Build A

Snapshot consistente por API SQLite backup registra quatro heartbeats em
dead_letter, todos com HTTP 400 (invalid_payload). Os demais tipos de evento
apresentam entregas aceitas; havia também dois eventos risk pendentes no snapshot.

Causa reproduzida: `sanitize_text("QA-A-be0742475703")` resulta em
`QA-A-be[conteudo protegido]`. A regra `_PHONE` interpreta os dígitos do SHA curto
como telefone. O adapter sanitiza `settings.build` como texto livre, e a outbox
também sanitiza o payload. O backend `erp-sync` exige um identificador válido
para `build`, rejeitando espaços/colchetes introduzidos pela redação.

Componentes: `app/services/control_center_adapter.py`,
`control_center/sanitization.py`, contrato SQL `np_identifier_is_valid` aplicado
ao heartbeat. Não é falha de senha, DPAPI, tenant ou disponibilidade de PROD.

Os heartbeats não foram apagados ou adulterados. Não houve publicação B:
o gate A de sync/heartbeat ainda falha. Update A→B, preservação após update e
reinício continuam **PENDENTES**, sem reaproveitar resultados da VM antiga.

Próxima decisão: autorizar correção e novos instaladores QA A/B, pois os atuais
contêm a falha e não podem concluir este gate apenas sendo reutilizados.
Nenhuma correção do produto ou rebuild foi feita enquanto essa decisão está pendente.

Evidências locais ignoradas: `artifacts/windows-qa-vm/clean-20261014/`.
Não versionar snapshots SQLite, credenciais, imagens de disco ou backups.
