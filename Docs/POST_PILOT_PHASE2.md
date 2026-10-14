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

### Proteção da fronteira do cliente

Achado confirmado por teste local, severidade MEDIUM: os métodos de consulta
por tenant do repositório Supabase retornavam o objeto administrativo completo,
incluindo notas internas e autores/detalhes do histórico. O backend SQLite já
removia esse conteúdo. Isso representa uma falha do contrato de exposição;
não foi demonstrado vazamento em PROD e nenhuma consulta remota foi necessária.

Correção: detalhe, listagem e histórico por tenant usam projeção explícita.
Somente eventos `created` e `status_changed` são publicados, sem autor ou detalhe
interno. Eventos desconhecidos são omitidos. A consulta administrativa mantém
as notas para uso interno. Testes com objetos e transporte JSON simulado cobrem
as três leituras e a rejeição de outro tenant.

### Consentimento para diagnóstico

O formulário de chamado passou a oferecer checkbox opcional, desmarcado por
padrão. Sem consentimento, não chama o coletor técnico nem resolve diagnóstico
da Nexa; mantém somente a identificação necessária do atendimento. O serviço
também remove payload, fingerprint, correlação e diagnóstico na ausência de
consentimento, registrando a decisão no contexto do chamado. O caminho com
consentimento preserva a sanitização existente.

Não foram adicionados endpoints remotos, migrations ou transporte de mensagens
neste incremento. Conversa bidirecional e anexos continuam pendentes.

Validação deste incremento: 69 testes passaram em suporte, fronteira pública,
sync offline, Control Center com backend simulado e Nexa. Evidência local:
`artifacts/post-pilot-20261014/support-boundary.xml`. Secret scan passou em 508
arquivos candidatos e `git diff --check` passou. Tela conferida no navegador
em 1366 e 390 pixels, sem overflow horizontal, checkbox desmarcado; captura
mobile inspecionada. Bancos e imagens são descartáveis e ficam fora do Git.

### Sequência restante

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
