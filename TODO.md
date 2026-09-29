# ERP — Tarefa Atual

> Corrigir o POST de login do Control Center no Render, preservando origem canônica, CSRF e configurações PROD existentes.

## A Fazer

- [ ] Confirmar login publicado após deploy do novo commit pelo proprietário; validação local concluída, aceite em produção pendente {weight:3} {sector:SETOR_COORDENACAO}

## Em andamento

## Concluídas

- [x] Validar secret scan e diff antes do commit/push autorizado {weight:2} {sector:SETOR_QA_TESTES}
- [x] Corrigir a recusa de origem no login em ambiente isolado {sector:SETOR_COORDENACAO}
  - [x] Reproduzir no navegador o Origin:null causado pela política no-referrer {weight:3} {sector:SETOR_SEGURANCA}
  - [x] Corrigir política de navegador e normalização estrita da origem sem relaxar CSRF {weight:5} {sector:SETOR_BACKEND}
  - [x] Exibir login com mensagem segura nas recusas de segurança {weight:2} {sector:SETOR_INTERFACE_UX}
  - [x] Cobrir proxy Render, credenciais, sessão, origens maliciosas e ausência de secrets em logs {weight:5} {sector:SETOR_QA_TESTES}
- [x] Validar 152 testes Control Center/PROD, sessão real no Edge com backend isolado e schema oficial do Blueprint {weight:5} {sector:SETOR_QA_TESTES}
