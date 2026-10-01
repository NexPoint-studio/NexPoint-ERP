## A Fazer

- [ ] Responsável cadastrará manualmente serviços, unidades e preços reais em Administração > Serviços após instalar no notebook, antes da primeira Nota; não é necessário enviar o catálogo para preparar o pacote {weight:2} {sector:SETOR_COORDENACAO}
- [ ] Executar aceite no notebook após entrega: primeiro login, DPAPI própria, segundo start, heartbeat e ACK sob nil-lav-pc-01; testes neste PC não comprovam essa etapa {weight:3} {sector:SETOR_QA_TESTES}

## Em andamento

## Concluídas

- [x] Preparar entrega oficial em Entrega/NilLavLavanderia: Setup 1.1.0 da build eef7592, seis PDFs/36 páginas e 24 screenshots atuais, relatório ENTREGA.txt com 26 pontos e limites do aceite {weight:8} {sector:SETOR_INTERFACE_UX}
- [x] Validar 12 checks do Setup, 914 arquivos extraídos idênticos, instalação/reinstalação/desinstalação e preservação por hash; Windows atual com WebView2, sem afirmar teste em VM limpa/notebook {weight:6} {sector:SETOR_QA_TESTES}
- [x] Arquivar entrega antiga fora da pasta final, preservando os 952 arquivos por hash, incluindo RAR preexistente {weight:2} {sector:SETOR_COORDENACAO}
- [x] Conferir entrega e conteúdo expandido PKG/PYZ/ZIP, seis PDFs e valores privados em memória: zero achados; revisar Git para publicação autorizada em main {weight:5} {sector:SETOR_SEGURANCA}
- [x] Implementar primeiro acesso automático, proteção DPAPI, banco limpo, conta operacional de menor privilégio e retomada idempotente sem secrets no pacote {weight:18} {sector:SETOR_COORDENACAO}
- [x] Validar 1041 testes Python, incluindo 23 do novo fluxo; 21 testes Edge e 35 checks PostgreSQL isolados, com ACK/sync da credencial ativada {weight:7} {sector:SETOR_QA_TESTES}
- [x] Conferir Git candidates: release secret scan limpo; Gitleaks 12 e TruffleHog 1 alertas triados como hashes Git, placeholders e fixtures sintéticas preexistentes; git diff --check limpo {weight:3} {sector:SETOR_SEGURANCA}
- [x] Publicar migration aditiva e erp-activate com backup cloud protegido; autorização nil-lav-pc-01 preparada sem consumo, expira em 08/10/2026 00:52 UTC; leitura final confirmou tenant, instalação e credencial principal preservados; Render não alterado {weight:7} {sector:SETOR_SEGURANCA}
