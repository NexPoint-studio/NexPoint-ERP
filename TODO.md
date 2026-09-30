## A Fazer

## Em andamento

## Concluídas

- [x] Consolidar release acceptance PASS_WITH_KNOWN_LIMITATIONS, evidências sanitizadas, revisão Git/secret scan e preservação de 134 hashes anteriores, sem novo deploy {weight:3} {sector:SETOR_COORDENACAO}
- [x] Receber confirmação do proprietário de Deploy succeeded/Live no Render para da653f9be3101f67cc50de2df2497295bfd774b2 {weight:1} {sector:SETOR_COORDENACAO}
- [x] Executar FINAL PROD SECURITY SMOKE após Live confirmado do SHA da653f9 {sector:SETOR_COORDENACAO}
  - [x] Conferir contratos, alvo, baseline e credenciais locais sem exposição {weight:2} {sector:SETOR_SEGURANCA}
  - [x] Validar HTTPS, liveness/readiness, headers, cookies, Origin e CSRF com poucas requisições {weight:3} {sector:SETOR_SEGURANCA}
  - [x] Validar login, sessões persistentes, logout/replay e páginas principais: 74/74 checks, sessões do smoke encerradas {weight:4} {sector:SETOR_QA_TESTES}
  - [x] Conferir migration/RLS/identidades, integridade SQLite, backups e retry/ACK real 10/10 sem dados fictícios em PROD {weight:3} {sector:SETOR_BANCO_DADOS}
