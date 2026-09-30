## A Fazer

- [ ] Consolidar evidências/status, secret scan, commits e push sem deploy {weight:4} {sector:SETOR_COORDENACAO}

## Em andamento

- [ ] Executar regressões completas, retestes de ferramentas e builds aplicáveis {weight:8} {sector:SETOR_QA_TESTES}

## Concluídas

- [x] Reavaliar SD-004/005: permanecem UNRESOLVED com limites documentados; SD-006 preservado {weight:4} {sector:SETOR_SEGURANCA}
- [x] Ajustar fronteira offline do teste de restore sem mudar produto ou retirar asserções; 12/12 {weight:2} {sector:SETOR_QA_TESTES}

- [x] Corrigir SD-008: trabalho criptográfico equivalente no login SQLite {weight:3} {sector:SETOR_BACKEND}

- [x] Tratar SD-003: prevenção futura validada; histórico residual exige ação humana separada {weight:4} {sector:SETOR_SEGURANCA}

- [x] Corrigir SD-007: CSRF malformado com rejeição controlada {weight:3} {sector:SETOR_SEGURANCA}

- [x] Corrigir SD-002: sanitização com custo limitado, regressões e reteste original {weight:6} {sector:SETOR_SEGURANCA}

- [x] Corrigir SD-001: revogação persistente por sessão, migration QA e regressões {weight:8} {sector:SETOR_SEGURANCA}

- [x] Confirmar main limpa, diff --check e publicar marco PRE-SECURITY-REMEDIATION f91edf983b151f494c113cfabe8344252c626c73 no origin NexPoint-studio/NexPoint-ERP; SHA remoto conferido antes de alterar arquivos {weight:2} {sector:SETOR_COORDENACAO}
- [x] Ler fontes de verdade e delimitar 5 CONFIRMED + 2 UNRESOLVED; sem coverage expansion, PROD ou deploy {weight:2} {sector:SETOR_COORDENACAO}
