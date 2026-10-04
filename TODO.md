## A Fazer

- [ ] Concluir recuperacao vinculada a empresa e instalacao, sem conta operacional {weight:8} {sector:SETOR_SEGURANCA}
- [ ] Concluir transferencia generica de tenant com backup e handoff idempotente; inventariar notebook real antes de alteracao {weight:18} {sector:SETOR_BANCO_DADOS}
- [ ] Validar updater com abertura direta e preservacao de dados em Windows QA limpo {weight:12} {sector:SETOR_QA_TESTES}
- [ ] Atualizar manuais e preparar instalacao/entrega oficial em Entrega/NilLavLavanderia {weight:8} {sector:SETOR_COORDENACAO}
- [ ] Executar regressao completa, RLS, Edge, scans de codigo/build/instalador/entrega e revisar Git {weight:12} {sector:SETOR_QA_TESTES}
- [ ] Aplicar PROD somente apos Windows QA, backups, migrations, RLS e rollback validados; verificar Nil lav sem duplicacoes {weight:10} {sector:SETOR_COORDENACAO}

## Em andamento

- [ ] Simplificar acesso e identidade empresarial preservando historico e isolamento {sector:SETOR_COORDENACAO}
  - [x] Auditar autenticacao, papeis, Admin Lock, empresa, ativacao, sync e updater; classificar legado e infraestrutura {weight:5} {sector:SETOR_COORDENACAO}
  - [ ] Abrir ERP diretamente com ator tecnico nao autenticavel, remover rotas/menus de contas e preservar protecao administrativa {weight:10} {sector:SETOR_BACKEND}
  - [ ] Separar modulos operacionais do Admin Lock e adaptar manutencao, Nexa e recovery {weight:8} {sector:SETOR_SEGURANCA}
  - [ ] Persistir empresa e Outbox atomicamente; refletir nome exibido e sincronizar metadados sem trocar IDs {weight:12} {sector:SETOR_BANCO_DADOS}
  - [ ] Implementar ativacao por codigo aleatorio, expiravel, revogavel e one-time com DPAPI e emissao no Control Center {weight:15} {sector:SETOR_SEGURANCA}
  - [ ] Validar abertura direta, ausencia de RBAC operacional, CSRF, bloqueio, expiracao, rename/offline e ativacao {weight:10} {sector:SETOR_QA_TESTES}

## Concluídas

- [x] Preservar trabalho anterior de Administracao, updater e plano de reassociacao; evidencias anteriores em Docs/ADMIN_TENANT_UPDATER.md {weight:2} {sector:SETOR_COORDENACAO}
