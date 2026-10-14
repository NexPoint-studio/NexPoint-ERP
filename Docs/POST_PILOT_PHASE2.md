# Pós-piloto — Fase 2 em andamento

## Checkpoint de entrada

Fase 1 registrada localmente em `e69fabc9fbd0133cacd2505b30359e296a30b119`.
Sem push ou deploy. PROD e entrega da Nil lav não foram alterados.

## Nexa: primeiro incremento

Base local de ajuda versão 3 acompanha catálogo M:N, preço por categoria,
recebimentos parciais, taxa da operadora, acréscimo e saldos anteriores.
Busca normaliza acentos e prioriza correspondências no título/assunto, mantendo
o limite de cinco resultados e o filtro de permissões efetivas. Não consulta
dados financeiros ou dados pessoais para montar esses textos.

Validação: 36 testes passaram (`test_postpilot_nexa_help`, `test_nexa_bridge`,
`test_nexa_ui`). Incluem permissões reais, busca com acentos, ausência de ajuda
inventada para assunto desconhecido e contratos existentes da ponte/UI.
Isso não comprova a qualidade de uma resposta de um provedor real nem encerra
as exigências de contexto confiável da Fase 2.

## Suporte: contratos identificados

- ERP cria chamado via Outbox transacional (`ClientSupportService`).
- Transporte de sync aceita `support_ticket`; ainda não há eventos de conversa.
- Listagem atual combina chamados locais pendentes e o repositório sidecar.
- `SupportTicket.internal_notes` é conteúdo interno; não reutilizar como conversa
  pública nem enviar esse campo na resposta de um endpoint da instalação.
- Supabase registra notas internas em `np_support_ticket_events` com
  `event_type=internal_note`. Uma resposta pública precisa de contrato distinto,
  escopo derivado da credencial técnica e idempotência.

## Próxima implementação

Definir e implementar mensagens públicas com identificação estável, direção,
data e estado; transporte de ida/volta autenticado por instalação; persistência
local para consulta offline; status/reabertura autorizados; projeção pública
que exclua notas internas. Depois integrar as telas ERP/Control Center e anexos
opcionais com limites, validação e acesso por tenant. Diagnóstico deve exigir
consentimento e permanecer sanitizado.

Antes de fechar a fase: testar isolamento entre tenants/instalações, replay,
falha de rede, acesso negado, anexos inválidos e visibilidade das respostas.
Nenhum endpoint ou migration desta conversa foi publicado. A integração
bidirecional, anexos e o restante da revisão contextual ainda estão pendentes.
