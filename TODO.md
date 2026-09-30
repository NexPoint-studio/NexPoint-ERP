## A Fazer

## Em andamento

- [ ] Encerrar entrega Git da RC: revisão final, secret scan, commits/push e conferir working tree/remote {weight:2} {sector:SETOR_COORDENACAO}

## Concluídas

- [x] Executar Security Retest + Coverage Expansion em QA/local {sector:SETOR_COORDENACAO}
  - [x] Conferir baseline remoto 9e6bcf18, main, origin e fontes oficiais; revisar 39 categorias + 8 superfícies {weight:3} {sector:SETOR_COORDENACAO}
  - [x] Retestar SD-001/002/003/007/008 e preservar os resíduos SD-003/004/005 e N/A SD-006 {weight:5} {sector:SETOR_SEGURANCA}
  - [x] Reexecutar scanners, triagem e secret scanning sem expor candidatos históricos {weight:5} {sector:SETOR_SEGURANCA}
  - [x] Validar 121 checks Supabase QA integrado, RLS/RPC/Edge/sessões/migrations/TLS e 8 testes Deno com typecheck {weight:7} {sector:SETOR_BANCO_DADOS}
  - [x] Validar quatro ciclos Desktop, WebView2 8/8, restore cruzado/DPAPI e 15 checks de transporte TLS {weight:7} {sector:SETOR_QA_TESTES}
  - [x] Documentar limites: contas Windows/ACL/memória, provider real, stack gerenciada e capacidade sem aprovação presumida {weight:6} {sector:SETOR_SEGURANCA}
  - [x] Validar suíte 1.018/1.018, 145 focados, 14 após versão RC e 101/101 hashes históricos preservados {weight:4} {sector:SETOR_QA_TESTES}
- [x] Aprovar gate QA PASS_WITH_KNOWN_LIMITATIONS e consolidar RC/changelog/rollback {weight:5} {sector:SETOR_COORDENACAO}
- [x] Preparar Supabase PROD após backup DPAPI/ACL, aplicar somente migration de sessões e verificar 21 tabelas/dados preexistentes preservados; Edge inalterada {weight:6} {sector:SETOR_BANCO_DADOS}
- [x] Gerar RC 1.0.1rc1, validar executável Windows em fixture offline (28 checks) e Docker isolado; reconexão/ACK em integração QA separada {weight:6} {sector:SETOR_QA_TESTES}
