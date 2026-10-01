## A Fazer

- [ ] Receber serviços, unidades e preços reais da lavanderia; catálogo não pode ser inventado e precisa estar preparado antes da primeira Nota real {weight:2} {sector:SETOR_COORDENACAO}
- [ ] Executar aceite no notebook após entrega: primeiro login, DPAPI própria, segundo start, heartbeat e ACK sob nil-lav-pc-01; testes neste PC não comprovam essa etapa {weight:3} {sector:SETOR_QA_TESTES}

## Em andamento

- [ ] Finalizar entrega oficial NilLavLavanderia {sector:SETOR_COORDENACAO}
  - [ ] Gerar instalador 1.1.0 de commit limpo, validar instalação/reinstalação/desinstalação e preservação dos dados {weight:6} {sector:SETOR_QA_TESTES}
  - [ ] Atualizar seis PDFs e screenshots reais sanitizados; arquivar entrega anterior fora da pasta final {weight:8} {sector:SETOR_INTERFACE_UX}
  - [ ] Verificar conteúdo expandido, secrets, hashes, relatório de entrega e push autorizado para main {weight:5} {sector:SETOR_SEGURANCA}

## Concluídas

- [x] Implementar primeiro acesso automático, proteção DPAPI, banco limpo, conta operacional de menor privilégio e retomada idempotente sem secrets no pacote {weight:18} {sector:SETOR_COORDENACAO}
- [x] Validar 1041 testes Python, incluindo 23 do novo fluxo; 21 testes Edge e 35 checks PostgreSQL isolados, com ACK/sync da credencial ativada {weight:7} {sector:SETOR_QA_TESTES}
- [x] Conferir Git candidates: release secret scan limpo; Gitleaks 12 e TruffleHog 1 alertas triados como hashes Git, placeholders e fixtures sintéticas preexistentes; git diff --check limpo {weight:3} {sector:SETOR_SEGURANCA}
- [x] Publicar migration aditiva e erp-activate com backup cloud protegido; autorização nil-lav-pc-01 preparada sem consumo, expira em 08/10/2026 00:52 UTC; leitura final confirmou tenant, instalação e credencial principal preservados; Render não alterado {weight:7} {sector:SETOR_SEGURANCA}
