## A Fazer

## Em andamento

## Concluídas

- [x] Revisar 355 candidatos, secret scans e diff; preparar registro local exclusivo da auditoria, sem push {weight:3} {sector:SETOR_COORDENACAO}

- [x] SECURITY DISCOVERY — rodada QA/local documentada, sem remediação; limitações em Docs/Security/SECURITY_ASVS_MATRIX.md {sector:SETOR_COORDENACAO}
  - [x] Preservar baseline b09c48e e preparar snapshot sem dados operacionais {weight:3} {sector:SETOR_COORDENACAO}
  - [x] Executar SAST/SCA e revisão Docker/Render estática {weight:5} {sector:SETOR_SEGURANCA}
  - [x] Examinar árvore/histórico Git sem verificar credenciais em provedores {weight:3} {sector:SETOR_SEGURANCA}
  - [x] Validar RLS/grants/Sync/Recovery em banco descartável, inclusive consumo concorrente limitado {weight:6} {sector:SETOR_BANCO_DADOS}
  - [x] Executar DAST/API local e reproduzir findings de sessão, sanitização e CSRF {weight:6} {sector:SETOR_SEGURANCA}
  - [x] Validar desktop, DPAPI fictício, backup offline e Nexa sem rede; preservar divergência do teste de restore {weight:6} {sector:SETOR_QA_TESTES}
  - [x] Consolidar 7 findings OPEN, triagem contextual, inventários e cobertura parcial ASVS/WSTG {weight:5} {sector:SETOR_SEGURANCA}
  - [x] Encerrar servidores exclusivos QA e preservar evidências ignoradas; PROD intacto {weight:2} {sector:SETOR_COORDENACAO}
