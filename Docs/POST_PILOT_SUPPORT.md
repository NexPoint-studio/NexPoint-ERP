# Suporte pós-piloto

Implementação local em validação; promoção PROD ainda não realizada.

O ERP identifica empresa e instalação automaticamente. O chamado recebe assunto,
categoria, área, prioridade e descrição. Contato é opcional e compartilhado apenas
para atendimento. Diagnóstico técnico exige consentimento separado. A Nexa prepara
um rascunho editável; enviar o chamado continua sendo uma ação explícita.

Respostas e mudanças de estado usam a outbox operacional, com identificadores
idempotentes. O endpoint `erp-support` autentica a instalação e assina respostas.
A projeção pública exclui notas internas; o cache local permite consulta offline.
Chamados e mensagens possuem paginação. Estados resolvido/fechado permitem
reabertura explícita antes de continuar a conversa.

Screenshots são opcionais, até três por chamado e 1 MiB por imagem, nos formatos
PNG/JPEG/WebP. O endpoint separado `erp-support-attachments` valida identidade,
nome, formato, tamanho e SHA-256. Bytes locais ficam no sidecar; a outbox contém
somente referência. Uma reconciliação limitada recupera a janela de interrupção
entre esses bancos. Downloads são privados, como attachment e nosniff; não há
execução nem visualização inline de conteúdo enviado.

O Control Center distingue respostas públicas de notas internas e oferece filtros
por empresa, instalação, data, estado, categoria e prioridade. Contato autorizado
fica fora do contexto enviado à Nexa. Nenhum canal implementa comandos Windows
ou shell remoto.

Migrations cloud: `20261014010000_support_conversation.sql` e
`20261014020000_support_attachments.sql`. Sidecar local: schema 12 aditivo.
Aplicação em PROD depende dos gates de backup/restore e QA do ciclo completo.

Limitações: anexos têm validação estrutural de cabeçalhos, sem decoder/antivírus;
quota por chamado não substitui quota acumulada por empresa. Falhas permanentes
de envio permanecem visíveis na fila; não são descartadas silenciosamente.

Evidência visual: desktop 1366 e mobile 390, sem overflow ou erro JavaScript,
rascunho Nexa revisado antes do envio e consentimento desmarcado. Capturas e
relatório ficam em `artifacts/post-pilot-20261014/` (não distribuídos pelo Git).
