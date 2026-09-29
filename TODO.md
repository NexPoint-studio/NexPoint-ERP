## A Fazer

- [ ] Executar em futura tarefa as validações residuais de SECURITY_COVERAGE_GAPS.md, com escopo QA próprio; não iniciar remediação nesta rodada {weight:8} {sector:SETOR_SEGURANCA}

## Em andamento

## Concluídas

- [x] SECURITY COVERAGE REVIEW + FINAL TRIAGE, continuação de 277a5b2 sem remediação {sector:SETOR_COORDENACAO}
  - [x] Preservar e reconferir os 31 artefatos anteriores por SHA-256 {weight:2} {sector:SETOR_COORDENACAO}
  - [x] Consolidar matriz por fronteira e todos os gaps ASVS/WSTG selecionados {weight:3} {sector:SETOR_COORDENACAO}
  - [x] Executar 149 checks locais de autenticação/sessão/autorização e registrar SD-008 {weight:6} {sector:SETOR_SEGURANCA}
  - [x] Classificar secrets/advisories por ocorrência; SD-004/005 UNRESOLVED, SD-006 NOT_APPLICABLE no fluxo atual {weight:6} {sector:SETOR_SEGURANCA}
  - [x] Validar 90 casos Data API/RPC/RLS em infraestrutura QA exclusiva e encerrada {weight:6} {sector:SETOR_BANCO_DADOS}
  - [x] Revisar limites desktop/restore/observabilidade e executar 5 checks TLS loopback; pendências documentadas sem alegar validação {weight:6} {sector:SETOR_QA_TESTES}
  - [x] Consolidar relatórios, inventário sanitizado e revisão de escopo sem alterar runtime {weight:5} {sector:SETOR_COORDENACAO}
  - [x] Revisar candidatos Git com secret scanners; somente fixtures/hashes conhecidos nos alertas da árvore {weight:3} {sector:SETOR_SEGURANCA}
