# Reassociação segura de instalação entre empresas

Estado: contrato implementado localmente e exercitado em QA sintético;
nenhuma reassociação PROD executada. O SQLite e o DPAPI do notebook da Nil lav
ainda precisam ser inventariados e copiados no próprio computador. O estado
físico atual do notebook não foi verificado.

Destino previsto: `nil-lav-lavanderia`, nome `Nil lav lavanderia`, kind customer.
Preservar instalação `nil-lav-pc-01`, histórico de autoria,
segredo da instalação, UUIDs e dados. Contas operacionais deixam de ser usadas.
`nexpoint-admin` permanece administrador da
plataforma. Não criar usuário de plataforma para a operadora do ERP.

## Dependências confirmadas

A foundation cloud possui 16 tabelas filhas, com 18 FKs compostas imediatas:

- Diretas: `np_installation_credentials`, `np_sync_envelopes`, `np_heartbeats`,
  `np_health_snapshots`, `np_installation_versions`, `np_observability_events`,
  `np_diagnostic_events`, `np_fingerprints`, `np_risks`, `np_incidents`,
  `np_support_tickets`, `np_admin_recovery_requests`, `np_request_nonces`.
- Indiretas: `np_sync_acks`, `np_support_ticket_events`,
  `np_admin_reset_authorizations`.

Também reconciliar o activation grant consumido por `installation_key`, sem
apagar consumo, credential_id ou janela de retry. Preservar a auditoria anterior
e acrescentar um registro da transferência. Não copiar `np_platform_user_tenants`
da NexPoint: esses vínculos pertencem aos operadores do Control Center.

O canal de ambiente continua `prod`. Definir explicitamente `update_channel=stable`
na transferência para cliente; o default de releases só decide no INSERT.

Localmente há mais de um estado persistido:

- SQLite operacional: usuários, configurações e outbox com tenant no payload;
- DPAPI: tenant, instalação, segredo e tipo de empresa;
- sidecar Control Center: identidade derivada de tenant/instalação e registros;
- SQLite de observabilidade: tenant/instalação e eventos pendentes;
- `installation.json`: identidade pública e configuração da empresa.

`local_session_secret()` depende de tenant/instalação. `requester_ref` de suporte
e recovery usa esse segredo como salt. Uma troca ingênua invalida contexto de
cookies e muda o solicitante das recuperações anteriores. O contrato da
transição precisa preservar namespace estável do ator e continuidade de sessões
administrativas a invalidar, sem alterar registros de autoria para facilitar testes.
Após a simplificação, recovery possui uma referência por solicitação. O handoff
precisa encerrar ou reconciliar solicitações pendentes antes de trocar esse escopo.

O inventário foi ampliado e conferido em PostgreSQL 17 descartável, sem rede,
com todas as migrations locais em 04/10/2026. São **19 tabelas com escopo
composto e 21 FKs compostas**, mais duas tabelas de autorização de ativação
ligadas indiretamente pela credencial. O catálogo de dependências transitivas,
colunas de escopo, triggers e RLS é produzido por
`scripts/security/test_handoff_schema.py`. As nove verificações passaram.
`scripts/security/test_handoff_database.py` executa transferências sintéticas;
isso ainda não valida arquivos reais do notebook ou o Windows instalado.

Além das 16 tabelas originais, preservar:

- `np_company_profiles`: snapshot por instalação. Após a mudança, publicar uma
  revisão maior no destino; não reutilizar a mesma revisão com outro escopo.
- `np_company_profile_receipts`: manter os hashes e revisões recebidos. Seus
  hashes representam o payload original; trocar tenant não autoriza recalculá-los.
- `np_company_profile_sources`: a chave primária é o tenant, não a instalação.
  Não basta trocar `tenant_id` em massa. Se a instalação transferida for a fonte
  da origem, escolher explicitamente a substituta pela data/chave ou remover a
  fonte quando não restar instalação. Preservar a fonte já existente no destino;
  se não existir, atribuir a instalação transferida. O trigger atual atua somente
  no INSERT de instalação, portanto não faz essa reconciliação no UPDATE.

`np_installation_activation_grants` e `np_installation_activation_codes` possuem
`tenant_id` e `installation_key`, mas não têm FK composta para instalação.
São alcançadas pela FK de `credential_id` quando consumidas. Preservar o recibo
de consumo, UUID, credencial e auditoria; bloquear nova ativação ou retry no escopo
antigo após o handoff. O grant legado possui trigger que troca `auth_version`
quando o escopo muda, o que precisa fazer parte da validação.

`np_installations.update_channel`, estado e timestamps do updater permanecem
associados ao mesmo UUID. O catálogo global de releases não deve ser duplicado.
`np_request_nonces` também contém proteção contra replay do updater: não limpar.
`nexa_private.erp_replay_nonces` tem chaves hash globais, sem coluna de tenant ou
instalação; conservar os recibos existentes, sem tentar reassociá-los pelo hash.
As janelas globais de limite de ativação também permanecem intactas.

O inventário falha se surgir uma dependência não revisada. Um teste acrescenta
uma filha sintética para confirmar a descoberta. Todas as tabelas afetadas
continuam com RLS e FORCE RLS. A migration torna somente as 21 FKs compostas
revisadas DEFERRABLE INITIALLY IMMEDIATE; nenhuma constraint foi desligada.

## Contrato implementado em código local, ainda sem liberação PROD

Handoff autenticado com a credencial existente, domínio HMAC próprio, prazo,
nonce, instalação imutável e versão monotônica da identidade. A empresa de
destino vem da autorização administrativa, nunca de escolha livre do cliente.

Estados: PREPARED → CLOUD_COMMITTED → LOCAL_COMMITTED → CONFIRMED.
O journal deve retomar após crash entre DPAPI e os bancos; não há transação única
entre esses arquivos. Sync não pode abrir com metade da identidade alterada.
Somente o endpoint restrito de retomada reconhece o contexto antigo autorizado.
As APIs normais continuam recusando escopo antigo ou tenant diferente.

Antes do commit cloud, pausar escritores, drenar/reconciliar filas e verificar
backups. ACK perdido deve ser recuperado pelo recibo original. Não reescrever
payloads históricos nem recalcular `np_sync_envelopes.payload_hash`: o hash é
parte do tombstone imutável de idempotência, mesmo após redação do payload.
Itens comprovadamente não recebidos precisam de tratamento transacional próprio.
Se a drenagem não terminar, a transferência permanece em preparação.

`20261004110000_installation_reassociation.sql` torna as 21 FKs compostas
inventariadas DEFERRABLE INITIALLY IMMEDIATE e adia somente essas constraints
dentro da transação coordenada. Atualiza as tabelas de escopo, reconcilia
separadamente a fonte canônica, instalação e autorizações de ativação, e volta
as constraints a IMMEDIATE antes do commit.
Não desligar triggers, constraints ou RLS. A implementação precisa coordenar os
locks de provisionamento, sync, recovery e Nexa, e registrar recibo idempotente.
Uma instalação movida sem recibo compatível é caso de reconciliação, não sucesso.

## Backup, QA e reversão

Inventário operacional disponível, somente leitura, para uma cópia SQLite:

```powershell
.venv\Scripts\python.exe scripts/security/inspect_handoff_copy.py --operational-copy CAMINHO_DA_COPIA --expected-tenant TENANT_ATUAL --expected-installation INSTALACAO_ATUAL
```

O relatório não inclui nomes de clientes, valores de configurações, credenciais,
hashes de senhas ou mensagens internas do SQLite. Registra contagens permitidas,
integridade, FKs, comparação do escopo, revisão e fila Outbox. Qualquer item não
sincronizado, estado desconhecido ou item sincronizado sem ACK bloqueia a
validação da cópia. Um banco anterior ao perfil sincronizado exige migração
testada de uma cópia, não inicialização silenciosa pela ferramenta.

`copy_checks_passed=true` significa somente que essas verificações passaram;
`transfer_ready` permanece falso. DPAPI no Windows de destino, sidecars,
observabilidade, recovery, backup coordenado, recibos remotos e pausa dos
escritores continuam gates separados. Não executar esta análise no banco real
antes dos gates PROD. A ferramenta foi testada apenas com bancos sintéticos:
15 testes passaram, incluindo preservação byte a byte, falta de ACK, fila
pendente, escopo incorreto, FK quebrada, corrupção e supressão de dados sensíveis.

Snapshot consistente e protegido das tabelas afetadas, grants e empresas;
restauração desse snapshot em PostgreSQL isolado. No notebook, backup SQLite API
dos bancos e cópia local protegida de DPAPI/configuração, com integrity_check e
foreign_key_check. Nunca transportar DPAPI de outro Windows.

QA deve conter linhas nas 19 tabelas de escopo e nas duas tabelas de ativação,
incluindo destino já com fonte canônica, e validar concorrência, retries, ACK perdido,
crash em cada etapa, RLS A/B/anon, sessões, ator técnico, Admin Lock, recovery pendente
e replay, heartbeat, versões, suporte, Nexa e riscos sem duplicação.

Antes do commit cloud, cancelar preparação preserva o estado anterior. Depois de
tráfego no destino, reverter exige transferência inversa controlada, preservando
eventos novos. Não restaurar dump antigo por cima dos dados operacionais.

Aceite final exige evidência do notebook: tenant correto no heartbeat/sync,
abertura direta e dados anteriores intactos. A leitura da nuvem não substitui isso.

## Evidência de QA sintético e gates reais

O checkpoint local anterior à implementação está em
`D:\NexStudio\ERP-Recovery\nexpoint-erp-pre-handoff-20261004T192453Z.zip`;
SHA-256 `3b300fbd35a8d42c7a3b3240b9f9be6a33da224e5a7c70e7a1464f94bfb312c1`.
O ZIP foi reaberto e os hashes de seus 154 arquivos foram conferidos. Ele não é
backup do notebook nem do Supabase PROD.

A migration cria RPC restrita a `platform_admin` via service_role, recibo
idempotente e endpoint HMAC de leitura/confirmação. O helper instalado cria
backup SQLite por API, confere integridade/FKs e filas, protege journal com
DPAPI e bloqueia abertura durante handoff parcial. Crash antes do journal deixa
a cópia parcial preservada em `.preparing-*`; uma nova preparação usa outra
pasta. Após commit cloud, a operação deve ser retomada, sem restaurar dump
antigo sobre dados novos. O operador só recebe relatório sem segredo.

No PostgreSQL descartável, 83 verificações passaram: linhas sintéticas nas 19
tabelas de escopo e dois recibos de ativação, mudança atômica com UUID estável,
hashes históricos, source canônico em origem/destino, empresa de destino já
existente, idempotência concorrente, nonce/replay, RLS de instalações e de todas
as tabelas de escopo, e futura mudança de nome apenas no destino. No Python,
43 testes focados de handoff, relatório e
inventário passaram. A suíte Python completa posterior passou em 1.227/1.227,
zero falhas, erros ou skips. Edge: 49 testes combinados de ativação, update e
handoff passaram. Esses números se referem a rodadas locais e QA sintético.

Ainda faltam QA Windows limpo do Setup e updater A→B, concorrência e falhas de
processo com binários instalados, backup e inspeção no próprio notebook, backup
consistente/restaurável do backend PROD, inventário atual de `nil-lav-pc-01`,
teste de restore isolado, deploy da migration/Edge e confirmação ponta a ponta
no Control Center. O arquivo de atestado aceito pelo script do operador confere
escopo e SHA-256; **não prova sozinho** que o dump PostgreSQL é completo ou
restaurável. Essa prova precisa ser feita antes de `--execute`.
O roteiro e o bloqueio do ensaio instalado estão em `WINDOWS_QA_ENTREGA.md`.

Se o notebook nunca recebeu o ERP, não haverá SQLite/DPAPI anterior a transferir.
Nesse caso, é necessário reconciliar a instalação cloud preexistente e o fluxo
de primeira ativação sem duplicar `nil-lav-pc-01`; não executar o caminho de
handoff local como se houvesse dados. Esta condição permanece pendente de
inventário físico e impede declarar a entrega pronta.
