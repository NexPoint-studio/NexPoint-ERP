# ERP — Tarefa Atual

> Ajuste final de UX e segurança antes da auditoria ASTRA. Escopo exclusivo: simplificar a recuperação do Admin Lock e adicionar “Manter conectado neste dispositivo”.

## A Fazer

## Em andamento

## Concluídas

- [x] Simplificar a recuperação da Administração {sector:SETOR_COORDENACAO}
  - [x] Remover recovery codes e opções técnicas da experiência do cliente {weight:7} {sector:SETOR_INTERFACE_UX}
  - [x] Manter somente solicitação NexPoint com fila offline {weight:8} {sector:SETOR_BACKEND}
  - [x] Automatizar entrega e consumo da autorização de redefinição {weight:10} {sector:SETOR_SEGURANCA}
  - [x] Ajustar Control Center, auditoria, observabilidade e sanitizer {weight:8} {sector:SETOR_SEGURANCA}
- [x] Implementar “Manter conectado neste dispositivo” {sector:SETOR_COORDENACAO}
  - [x] Criar sessão persistente hasheada, expirável, revogável e rotacionável {weight:10} {sector:SETOR_BACKEND}
  - [x] Adicionar checkbox simples à tela de login {weight:3} {sector:SETOR_INTERFACE_UX}
  - [x] Revogar no logout, senha alterada e usuário inativo {weight:7} {sector:SETOR_SEGURANCA}
  - [x] Cobrir reabertura, expiração, adulteração, offline e separação do Admin Lock {weight:8} {sector:SETOR_QA_TESTES}
- [x] Validar integração e encerrar a tarefa {sector:SETOR_COORDENACAO}
  - [x] Atualizar documentação e decisões de segurança/offline {weight:4} {sector:SETOR_COORDENACAO}
  - [x] Executar suítes completas, Doctor, migrations e testes de segurança {weight:10} {sector:SETOR_QA_TESTES}
  - [x] Validar pywebview/WebView2, reabertura, reset autorizado e logout {weight:7} {sector:SETOR_QA_TESTES}
  - [x] Corrigir e testar a idempotência dos eventos de risco da Outbox {weight:3} {sector:SETOR_BACKEND}
  - [x] Revisar Git e confirmar ausência de secrets/artefatos {weight:4} {sector:SETOR_COORDENACAO}
