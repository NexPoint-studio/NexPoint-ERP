## A Fazer

- [ ] Windows QA: acesso válido à VM e instalação/update A→B com preservação de dados e DPAPI {weight:14} {sector:SETOR_QA_TESTES}
- [ ] Fase 4: segurança, integridade, restore e promoção {weight:18} {sector:SETOR_SEGURANCA}
- [ ] Conferir vínculo real Nil lav antes de promoção: consulta de 14/10 ainda indica nexpoint/internal; preservar identidade e dados, cumprir gates de backup/restore antes de PROD {weight:3} {sector:SETOR_COORDENACAO}

## Em andamento

- [ ] Validar instaladores QA A/B de be07424 na VM; acesso ao Windows convidado pendente {weight:8} {sector:SETOR_QA_TESTES}

## Concluídas

- [x] Builds e instaladores exclusivos QA A 1.1.0 / B 1.1.2 gerados; hashes, secret scan e boundary QA passaram {sector:SETOR_COORDENACAO}

- [x] Suporte/conversa/anexos/Nexa e gestão de rollout implementados localmente; 1389 casos validados (1384 na suíte + 5 corrigidos reexecutados), 21 Edge e SQL isolado {sector:SETOR_COORDENACAO}

- [x] Fase 1 pós-piloto: catálogo e recebimentos {sector:SETOR_COORDENACAO}
  - [x] Auditar arquitetura, Git e identidade remota somente leitura {weight:4} {sector:SETOR_COORDENACAO}
  - [x] Catálogo M:N e preços específicos preservando snapshots {weight:8} {sector:SETOR_BANCO_DADOS}
  - [x] Taxas, acréscimo cliente e ajustes auditados {weight:8} {sector:SETOR_BACKEND}
  - [x] Visual, fluxo e responsividade das telas e navegação {weight:5} {sector:SETOR_INTERFACE_UX}
  - [x] Migrations, regressões, integridade, backup/restore e scans; 1290 casos validados com reexecução dos 7 corrigidos {weight:7} {sector:SETOR_QA_TESTES}

- [x] Baseline 8a38978 preservada em bundle verificado em artifacts/post-pilot-20261014 {weight:1} {sector:SETOR_COORDENACAO}
