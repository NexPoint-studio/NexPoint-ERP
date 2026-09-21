# ERP — Tarefa Atual

> Primeira subida real do NexPoint ERP. PROD usa exclusivamente o projeto Supabase NexPoint-ERP; QA permanece isolado. Mercado Pago, billing, site comercial e ASTRA estão fora do escopo.

## A Fazer

- [ ] Provisionar e validar o primeiro uso real controlado {sector:SETOR_COORDENACAO}
  - [ ] Receber diretamente no terminal os dados reais mínimos da empresa e do administrador {weight:3} {sector:SETOR_SEGURANCA}
  - [ ] Provisionar tenant e installation com credencial DPAPI exclusiva {weight:8} {sector:SETOR_SEGURANCA}
  - [ ] Instalar a cópia validada do SQLite no diretório PROD externo {weight:5} {sector:SETOR_BANCO_DADOS}
  - [ ] Executar os cenários temporários online, financeiro, suporte, offline, Sync e ACK {weight:10} {sector:SETOR_QA_TESTES}
  - [ ] Cancelar ou identificar de forma auditável somente os registros temporários {weight:4} {sector:SETOR_COORDENACAO}
- [ ] Publicar e validar o Control Center remoto {sector:SETOR_COORDENACAO}
  - [ ] Autorizar o repositório no host compatível com Docker {weight:3} {sector:SETOR_COORDENACAO}
  - [ ] Inserir as variáveis e segredos diretamente no cofre do host {weight:5} {sector:SETOR_SEGURANCA}
  - [ ] Validar HTTPS, health, host allow-list, login e acesso por tenant {weight:6} {sector:SETOR_QA_TESTES}

## Em andamento

- [ ] Promover a infraestrutura preparada para PROD {sector:SETOR_COORDENACAO}
  - [x] Validar migration PostgreSQL, RLS, grants, isolamento, idempotência e retenção localmente {weight:10} {sector:SETOR_BANCO_DADOS}
  - [ ] Aplicar a migration aditiva no projeto Supabase NexPoint-ERP {weight:5} {sector:SETOR_BANCO_DADOS}
  - [ ] Publicar `erp-sync`, `erp-admin-recovery` e `erp-chat` {weight:5} {sector:SETOR_BACKEND}
  - [ ] Configurar providers da Nexa diretamente no secret manager {weight:3} {sector:SETOR_SEGURANCA}

## Concluídas

- [x] Conectar e travar a configuração ao projeto Supabase NexPoint-ERP {sector:SETOR_COORDENACAO}
  - [x] Confirmar branch, remotes, autenticação CLI e identidade do projeto {weight:4} {sector:SETOR_COORDENACAO}
  - [x] Preservar os bancos, dados, histórico Git e alterações oficiais existentes {weight:4} {sector:SETOR_SEGURANCA}
- [x] Implementar a fundação Cloud PROD {sector:SETOR_COORDENACAO}
  - [x] Criar tabelas, constraints, índices, RLS e policies fail-closed {weight:10} {sector:SETOR_BANCO_DADOS}
  - [x] Implementar nonce persistente, replay protection, idempotência e tenant isolation {weight:9} {sector:SETOR_SEGURANCA}
  - [x] Separar PROD de QA e bloquear seed, reset e fault injection em PROD {weight:7} {sector:SETOR_SEGURANCA}
- [x] Integrar o ERP offline-first ao remote PROD {sector:SETOR_COORDENACAO}
  - [x] Implementar SupabaseSyncRemote, ACK validado, retry, backoff e recovery após restart {weight:12} {sector:SETOR_BACKEND}
  - [x] Implementar identidade e credencial por instalação protegida por DPAPI {weight:8} {sector:SETOR_SEGURANCA}
  - [x] Ligar conexão, heartbeat, health, versões e diagnóstico aos estados reais {weight:8} {sector:SETOR_INTERFACE_UX}
- [x] Preparar Control Center PROD stateless {sector:SETOR_COORDENACAO}
  - [x] Implementar repository Supabase e leitura sanitizada por tenant {weight:10} {sector:SETOR_BACKEND}
  - [x] Reforçar autenticação, sessão, CSRF, HTTPS, headers e platform_admin {weight:9} {sector:SETOR_SEGURANCA}
  - [x] Preparar Docker e configuração genérica de hospedagem {weight:7} {sector:SETOR_BACKEND}
- [x] Integrar suporte, observabilidade, Admin Recovery e Nexa {sector:SETOR_COORDENACAO}
  - [x] Preservar sanitização, correlação, riscos, incidentes e retenção {weight:9} {sector:SETOR_BACKEND}
  - [x] Implementar tickets e Admin Recovery atômico de uso único {weight:9} {sector:SETOR_SEGURANCA}
  - [x] Validar bridge Nexa somente leitura e falha isolada {weight:8} {sector:SETOR_QA_TESTES}
- [x] Preparar a operação Windows PROD {sector:SETOR_COORDENACAO}
  - [x] Fixar versão, commit, ambiente, canal e installation_id no diagnóstico {weight:6} {sector:SETOR_BACKEND}
  - [x] Criar build reproduzível sem secrets, seed, QA ou debug {weight:9} {sector:SETOR_BACKEND}
  - [x] Migrar o banco operacional até 0015 com backup prévio validado {weight:8} {sector:SETOR_BANCO_DADOS}
  - [x] Validar backup, integridade e restore somente em cópia isolada {weight:8} {sector:SETOR_QA_TESTES}
- [x] Documentar deploy, provisionamento, backup, rollback, incidente e runbook {weight:8} {sector:SETOR_COORDENACAO}
- [x] Registrar e enviar o checkpoint oficial da Nexa após testes e secret scan {weight:5} {sector:SETOR_COORDENACAO}
