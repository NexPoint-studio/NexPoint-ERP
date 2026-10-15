# Promoção pós-piloto — 15/10/2026

Windows QA encerrado pelo proprietário; não reexecutado. Baseline: 1402/1402
PASS e ciclo A→B com preservação de SQLite, dados, Empresa, Admin Lock e DPAPI.

## Segurança e regressões

Nenhum CRITICAL/HIGH confirmado aberto. Defesa de escopo tenant adicionada aos
três RPCs de suporte, com 24 combinações de acesso, revogação e grants privados.
SQL conversa/anexos PASS; 37 regressões Python PASS. Recurso Windows alinhado
com versão 1.1.2; regressão específica PASS. Sem alteração no runtime desktop
aprovado no Windows QA. Limitações MEDIUM/LOW em POST_PILOT_SECURITY.md e
RELEASE_1.1.2.md continuam explícitas.

## Backup e migrations

Backup do schema/dados public de PROD, cópias cifradas DPAPI com roundtrip.
Restauração real em PostgreSQL 17 sem rede: PASS. Comparação de todas as colunas
preexistentes das 29 tabelas: dados preservados após as quatro migrations.
Constraints e índices válidos. Não é backup integral de objetos externos do
Storage, Auth gerenciado ou infraestrutura; nenhuma alteração nesses objetos
preexistentes foi necessária nesta promoção.

Aplicadas em PROD, em transações separadas e com verificação entre etapas:

1. 20261014010000_support_conversation.sql
2. 20261014020000_support_attachments.sql
3. 20261014180000_release_rollout.sql
4. 20261015010000_support_admin_tenant_scope.sql

Edge publicados: erp-support e erp-support-attachments. Smoke: ambos rejeitam
requisição anônima com 401; painel /health e /login retornam 200. Zero constraints
ou índices inválidos e nenhuma tabela np_* sem RLS. Esse smoke confirma a
disponibilidade atual, não o deploy da nova revisão do Control Center.

Rollback: preservar backup e novas mensagens; retirar/pausar a release antes de
nova oferta. As mudanças são aditivas e compatíveis com o cliente anterior.
Não executar DROP nem restaurar dados antigos sobre escritas posteriores.
Se necessária recuperação do backend, ensaiar em cópia e reconciliar as escritas
da janela antes de restauração autorizada. Artefatos privados em
`artifacts/release-stable-20261015/` não pertencem ao Git ou à entrega.

## Nil lav e liberação

Consulta atual mantém nil-lav-pc-01 em nexpoint/internal. Identidade preservada;
não houve reassociação, troca de credencial ou acesso ao notebook.
Antes de tornar a instalação elegível: obter backup/integridade atual no notebook
e decidir o canal pelo fluxo oficial, sem simular handoff local inexistente.

Render está com autoDeploy=false. Não há navegador conectado ou credencial API
Render disponível. Resta Manual Deploy → Deploy latest commit no serviço
nexpoint-erp-control-center, seguido de smoke da revisão publicada.
O pacote notebook-preflight prepara backup e relatório sanitizado sem instalar.

## Artefato stable

Instalador 1.1.2 gerado do commit `34e0febbddcfc251066d45cc337236966dbbf184`,
com updater incluído e metadados Windows/manifest concordantes. O empacotador
recusou a tentativa anterior por recurso Windows ainda em 1.1.0; ela não foi
publicada. Corrigidos recurso e regressão; não houve rebuild A/B QA.

SHA-256 do instalador:
`b4b01840d3c6a4c29b3988397166133cf5b54dfda0926199f77e3a7243792527`.
Arquivo: `artifacts/windows-installer/1.1.2-34e0febbddcf/NexPointERP-Setup.exe`.
Secret scan de distribuição: 919 arquivos do bundle e instalador PASS.
Assinatura Windows: NotSigned, declarada como CODE_SIGNING_PENDING.

Publicação verificada em Storage privado (download e conferência independente):
release `5233c45a-a9a7-4f87-bfa2-1fb3adcde8ac`, canal stable, status published,
rollout paused/0%. Não é oferecida automaticamente aos clientes enquanto
os gates externos não terminarem. Nil lav permanece internal, portanto não
receberá esta release sem revisão explícita de elegibilidade.

Pacote manual sanitizado: `C:\Users\ruben\Desktop\NilLav-Preflight-1.1.2.zip`.
Enviar de volta somente o JSON de auditoria gerado no notebook, não o backup.
