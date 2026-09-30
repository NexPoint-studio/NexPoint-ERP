## A Fazer

- [ ] Consolidar Security Gate, RC, changelog e rollback com riscos residuais explícitos {weight:5} {sector:SETOR_COORDENACAO}
- [ ] Após gate QA, executar somente preparação Supabase PROD necessária, com backup e schema diff; Edge apenas se houver mudança {weight:6} {sector:SETOR_BANCO_DADOS}
- [ ] Validar builds RC Windows/Docker, secret scan final, commits/push e entregar SHA para deploy manual Render {weight:8} {sector:SETOR_COORDENACAO}

## Em andamento

- [ ] Executar Security Retest + Coverage Expansion em QA/local {sector:SETOR_COORDENACAO}
  - [x] Ler fontes oficiais e registrar baseline remoto e matriz das 39 categorias + 8 superfícies {weight:3} {sector:SETOR_COORDENACAO}
  - [x] Retestar SD-001/002/003/007/008 e regressões auth/session/CSRF/integrações {weight:5} {sector:SETOR_SEGURANCA}
  - [x] Reavaliar SD-004/005/006 e scanners sem expor secrets ou ampliar campanha ofensiva {weight:5} {sector:SETOR_SEGURANCA}
  - [x] Ampliar Supabase QA integrado, RLS/RPC/Edge, migrations e rollback compatível {weight:7} {sector:SETOR_BANCO_DADOS}
  - [x] Ampliar WebView2/DPAPI/filesystem/restore entre instalações com fixtures descartáveis {weight:7} {sector:SETOR_QA_TESTES}
  - [x] Ampliar browser/origin/proxy/TLS/rede e documentar limitações de todas as categorias {weight:6} {sector:SETOR_SEGURANCA}
  - [ ] Executar suíte completa e preservar evidências sanitizadas e hashes {weight:4} {sector:SETOR_QA_TESTES}

## Concluídas

- [x] Confirmar main, origin NexPoint-studio/NexPoint-ERP, árvore limpa e remoto 9e6bcf18fed4a7b5350016fc8d80eab59ec0cf42; diff --check aprovado {weight:1} {sector:SETOR_COORDENACAO}
