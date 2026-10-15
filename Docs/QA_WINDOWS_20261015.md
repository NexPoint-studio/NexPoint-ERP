# Gate Windows QA — 15/10/2026

Status atual: **GO para o gate QA de sanitização e update A→B**. Somente laboratório local; sem push, deploy, PROD ou alteração
da entrega Nil lav.

## Reteste com novos instaladores autorizados

Correção local nos commits `e65819a` e `b7b5ed5`: o identificador canônico
QA-A/QA-B/PROD seguido de 12 hexadecimais só é preservado quando corresponde
ao commit completo validado. Textos livres continuam sujeitos à sanitização.
Aplicado no adapter, payload da outbox e receptor local de heartbeat.
Regressões cobrem SHA numérico, SHA do incidente, três prefixos, sanitização
repetida e ingestão/reabertura real do repositório local. Focados: 49 PASS.

Novos instaladores, exclusivamente QA/internal, fonte
`b7b5ed5675f7f98d9fdf711f36a3f27c03a6cc19`:

- A: 1.1.0, `QA-A-b7b5ed5675f7`.
- B: 1.1.2, `QA-B-b7b5ed5675f7`.
- Diretórios locais: `artifacts/windows-qa-installer/1.1.0-b7b5ed5675f7`
  e `artifacts/windows-qa-installer/1.1.2-b7b5ed5675f7`.
- Inventário, hash de transferência, secret scan e fronteira QA: PASS.
- Build intermediária e instaladores antigos não foram usados no reteste.

A instalação anterior com quatro dead letters foi preservada em
`C:\QA\bug-run-20261015`; a identidade antiga foi arquivada no backend QA.
Foi feito dump QA antes da preparação. Releases antigas foram copiadas para
`qa_evidence.releases_before_build_fix_20261015` e retiradas do catálogo QA ativo,
evitando oferecer artifacts antigos com a mesma versão. Nenhuma fila foi limpa.
O publicador auxiliar antigo esperava nove migrations: o harness local conferiu
explicitamente as treze migrations QA já existentes antes de adaptar apenas
essa contagem, mantendo as verificações de tenant, instalação, canal e origem.

Reteste real na VM:

- Instalação limpa A: exit 0, sem SQLite pré-existente; ativação HTTP 303.
- Empresa, ausência de login operacional (404), Admin Lock e Recovery: PASS.
- Recovery: autorização consumida, senha antiga 401, nova senha aceita.
- A: 30 eventos synced, incluindo quatro heartbeats com build intacta.
- B publicada apenas no Supabase local QA/internal; detecção e download reais.
- Hash verificado, estado READY; instalação pelo updater e reinício do ERP.
- B executada 1.1.2, estado CURRENT; nova verificação não oferece update repetido.
- B: 49 eventos synced; pending=0, sending=0, failed=0, dead_letter=0.
- Backend QA confirma heartbeat `1.1.2 / QA-B-b7b5ed5675f7`.
- SQLite integrity_check ok e foreign_key_check sem violações em A e B.
- Cliente fictício, 18 configurações de Empresa e hash do Admin Lock iguais.
- DPAPI decifrada em A/B: identidade e hash do arquivo cifrado idênticos.
- Admin Lock também desbloqueou a administração na versão B.

Hash inválido: evidência automatizada em
`test_failed_download_removes_partial_artifact_without_execution[hash]`, usando
`download_release`, a mesma função ligada ao updater empacotado por
`run_desktop._make_update_manager`. A verificação rejeita o arquivo e remove
o parcial sem executar. Não foi feita manipulação manual do hash pela UI.
O reinício comprovado neste ciclo é do ERP pelo updater, não um reboot do Windows.

Evidências ignoradas: `artifacts/qa-build-fix-20261015/` e snapshots
`snapshot-A-fixed.sqlite3`, `snapshot-B-fixed.sqlite3`, `identity-*-fixed.json`
em `artifacts/windows-qa-vm/clean-20261014/`. Não versionar dados/credenciais.

Suíte final: **1402 PASS**, zero failures/errors/skips, 832,061 s; relatório
`artifacts/qa-build-fix-20261015/full-suite-final.xml`. Secret scan: 542 candidatos
Git PASS; `git diff --check` PASS. GO limitado ao laboratório; nenhuma promoção
stable, publicação remota, alteração de PROD ou da entrega Nil lav está incluída.

## Histórico anterior à correção (NO-GO superado no reteste funcional)

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
