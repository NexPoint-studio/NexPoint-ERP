# Acesso direto e identidade da empresa

Especificação canônica de 04/10/2026: instalação ativada abre diretamente,
sem contas ou papéis operacionais. Substitui as decisões de acesso por usuário
do relatório histórico ADMIN_TENANT_UPDATER.md. Trabalho anterior preservado;
baseline pré-alteração: 1182 testes Python, 63 verificações PostgreSQL e 29 Edge.
Esses resultados não validam automaticamente a nova implementação.

## Auditoria e classificação antes da alteração

| Componente | Classificação | Decisão |
| --- | --- | --- |
| Login, logout e sessões lembradas ERP | DESATIVAR / REMOVER DA EXPERIÊNCIA | Sem login operacional ou restauração de identidade por cookie antigo. |
| Usuários, roles, matriz e concessões a contas locais | LEGACY | Preservar tabelas e FKs históricos, remover rotas e interface de gestão. |
| Autoria de operações | INFRAESTRUTURA TÉCNICA / MIGRAR | Ator único da instalação, sem senha utilizável e sem roles; vínculo explícito, sem reutilizar conta humana. |
| Identidade em cookie | MIGRAR | Sessão local do navegador para CSRF e desbloqueio temporário; não identifica funcionário. |
| Admin Lock | MANTER / ADAPTAR | Inicialmente livre; hash, expiração, versão, reinício, tentativas limitadas e recovery. Não restringe operação financeira normal. |
| Admin Recovery | MIGRAR | Escopo empresa/instalação/solicitação; manter autorização expirada/consumida negada. |
| Company Profile | MANTER / ADAPTAR | Settings company.*, validação existente; nome fantasia como nome exibido, fallback nome. |
| Identidade técnica e DPAPI | MANTER | Rename não troca tenant, installation, slug ou segredo. |
| Outbox/heartbeat | MIGRAR | Perfil e evento na mesma transação; somente metadados necessários. Heartbeat anterior não garante rename PROD. |
| Ativação usuário/senha | LEGACY / SUBSTITUIR | Código aleatório one-time; contratos e grants antigos preservados para histórico. |
| Control Center platform users/scopes | MANTER | Autenticação da plataforma não é login operacional do ERP. |
| RLS e HMAC por instalação | MANTER | Acesso direto local não concede acesso entre empresas. |
| Updater | MANTER / ADAPTAR | Health check de abertura direta, sem requisito de conta proprietária. |
| Nil lav e entrega | MIGRAR | Código genérico; transferência depende de inventário/backup real e gates QA. |

## Fronteiras de segurança

O servidor ERP continua restrito a loopback, hosts permitidos e origem local.
POST não pode ganhar autorização por receber um cookie de usuário legado.
A sessão local usa cookie HttpOnly/SameSite Strict; a proteção administrativa
tem validade finita e não sobrevive ao reinício. O ator técnico não atribui
ações a uma pessoa e não pode autenticar por senha.

Não antecipar PROD aos gates da especificação. Acesso real ao notebook e Windows
QA limpo ainda não foram comprovados. O instalador antigo não contém esta
simplificação e não é a entrega final desta tarefa.

## Contratos implementados localmente

O ator técnico é uma linha sem roles e com verificador deliberadamente inválido
em `users`, vinculada por `system.installation_actor_id`. Isso conserva FKs de
Caixa, Notas, pagamentos e auditoria. Não é conta de funcionário; nenhuma rota
de login, logout ou gestão de usuários é registrada. Dados antigos não foram
apagados nem tiveram a autoria substituída. A migration 0016 provoca backup
pré-migration em PROD pelo mecanismo existente.

Admin Lock continua opcional. Protege configurações administrativas, backups,
restauração, catálogo e suporte; Caixa/Clientes/Notas/Financeiro permanecem
operacionais. Backup usa o desbloqueio administrativo existente. Restauração
mantém a confirmação RESTAURAR, validação de schema e cópia prévia. Recuperação
usa referência aleatória persistida por solicitação, vinculada a tenant e
instalação, sem depender de e-mail ou papel. A autorização remota mantém prazo,
assinatura, nonce e consumo único. Uma interrupção entre o consumo remoto e o
commit local exige nova autorização; não há reutilização silenciosa do reset.

Perfil usa `company.*`. Nome fantasia prevalece na interface, com fallback ao
nome. Nome, fantasia, revisão e associação são os únicos dados do novo evento
`company_profile`; CPF/CNPJ, endereço e contatos permanecem locais. Estado
comercial é administrado no servidor, nunca alterado pelo cadastro local.
Salvar perfil e Outbox ocorre na mesma transação. Renomear não muda identidade,
slug, instalação ou DPAPI. Revisões antigas são ignoradas; mesma revisão com
conteúdo diferente é rejeitada. O Control Center não envia cadastro de volta.

Em múltiplas instalações, uma fonte canônica é escolhida e persistida. Na nuvem,
a migração escolhe a instalação mais antiga, com desempate pela chave; empresas
novas recebem a primeira instalação provisionada. As demais publicam snapshots
sem substituir o nome canônico. Reassociação deve incluir essas tabelas novas.

Ativação v2 usa código aleatório de 256 bits, digest no servidor, expiração de
até sete dias, revogação e consumo transacional. Retry aceita somente o mesmo
request/key/digest, por até um dia. RPCs antigos de ativação por senha perdem
EXECUTE para service_role; histórico permanece. `/ativacoes` no Control Center
exige platform_admin e CSRF, mostra o código somente na emissão e permite
revogar pelos metadados. Código não fica em cookie, SQLite cliente ou journal;
journal e credencial da instalação continuam protegidos por DPAPI.

## Validação parcial da nova etapa

Testes focados de abertura, proteção, recovery, backup, cadastro, ativação,
catálogo e updater passaram localmente. Nova validação PostgreSQL descartável:
29 verificações de código/empresa e 63 regressões de releases aprovadas. Edge:
49 testes aprovados, incluindo 20 de ativação v2. Scans de candidatos Git:
463 arquivos aprovados nesse passe. A regressão final posterior está registrada
abaixo; esses resultados não significam aprovação do instalador ou da entrega.

Pendente: validar Windows limpo e ciclo real de atualização, concluir handoff e backup do
notebook, migração PROD após gates, build oficial e manuais finais. Não houve
commit, push, deploy, emissão de código real nem alteração de dados PROD.

## Reteste e revisão de 04/10

A primeira regressão do novo contrato executou 1.202 testes: 1.107 aprovados e
95 falhas. Foram aposentadas 30 funções exclusivamente ligadas a login,
remember-login, administração de contas e negações por papel. Não foram
adicionados skips para esconder falhas. Contratos de preservação de autoria,
migrations, isolamento de ambiente, transações e proteção de dados continuam.

Substituições verificam abertura direta, identidade técnica sem senha e sem
roles, POST direto sem sessão, Origin/Host, cookies antigos ignorados, expiração,
Admin Lock opcional, navegação financeira encerrando desbloqueio, endpoints
retirados sem mutação e recuperação vinculada à solicitação correta.

Rodadas focadas: 43 verificações iniciais; 166 testes de compatibilidade; 47
testes de acesso/empresa/ativação/restore; e 16 testes finais de backup/restore,
todos aprovados nas respectivas execuções. Contagens são de rodadas distintas,
com sobreposição, e não devem ser somadas como cobertura única.

A revisão reproduziu restore cruzado aceito em dois bancos sintéticos. A
correção compara a identidade persistente e o escopo antes de preparar/aplicar o
backup. Backups anteriores ao perfil sincronizado precisam comprovar a mesma
identidade local. Durante a finalização, a revisão do cadastro sobe além da
maior revisão pré-restore e cria um snapshot na mesma transação, com marcador
idempotente. Nenhum DPAPI é alterado. Rejeição mantém o banco original; a
auditoria pode acrescentar o registro sanitizado da recusa.

A emissão/revogação local de códigos ganhou auditoria transacional sem código
ou digest nos eventos. A precedência local de perfil considera data de criação
da instalação e desempate por ID, sem depender da ordem dos snapshots.

O guia `GUIA_USO_SIMPLIFICADO.md`, README e contratos de ativação e cadeado foram
atualizados. PDFs e o instalador antigo em Entrega ainda precisam ser substituídos
após a validação do pacote novo. O uso do TODO foi dispensado pelo usuário; os
resultados e limitações ficam nesta documentação.

## Regressão completa consolidada

Execução de 04/10/2026: **1.184 testes aprovados, zero falhas, zero erros e zero
skips**, em 695,156 segundos. Evidência JUnit local ignorada pelo Git:
`artifacts/simplification-20261004/regression-final.xml`. Os 28 testes focados
anteriores incluíam as duas últimas expectativas de QA/demo corrigidas; a
regressão completa confirma o conjunto final do código de produto.

O navegador Edge automatizado também validou abertura direta, edição do nome,
Financeiro operacional, criação do cadeado e desbloqueio em banco sintético.
Não houve erro de página nem requisição externa; as capturas foram inspecionadas.
Esse teste usa a aplicação Python local, não o instalador em Windows limpo.

O inventário posterior de reassociação passou em nove verificações PostgreSQL:
19 tabelas de escopo, 21 FKs compostas e duas tabelas de ativação. O relatório
detalhado do catálogo fica em `artifacts/release-qa/np-release-qa-9e0bb5f246/`.
O container descartável foi removido. Naquele momento, a descoberta de schema
ainda não equivalia a um handoff implementado; a execução transacional e a
recuperação de interrupções foram implementadas posteriormente. A validação
com os arquivos do notebook permanece pendente.

Depois da regressão completa, foram acrescentados somente ferramentas de
inventário e seus testes/documentação. O inventário de uma cópia operacional
passou em 15 testes adicionais (`tests/test_handoff_copy_inventory.py`). A suíte
completa de 1.184 não inclui esses 15 novos testes: são execuções distintas.
Nenhum dado de cliente ou credencial é exportado no relatório; o resultado nunca
declara uma transferência autorizada/pronta. O código de produto não havia sido
alterado após aquela regressão completa; o handoff posterior exige nova suíte.

Verificação final desta rodada: secret scan aprovado para 466 arquivos candidatos
ao Git; `git diff --check` sem erros. Não foi gerado um novo instalador, portanto
esse scan não substitui o scan futuro do build, updater, Setup e pacote de entrega.
Não houve commit, push, deploy ou alteração PROD nesta rodada.

## Transferência controlada: implementação local posterior

O checkpoint pré-handoff, contrato cloud/Edge, helper Windows com journal DPAPI e
QA sintético estão descritos em `TENANT_REASSOCIATION_PLAN.md`. A verificação
PostgreSQL descartável de transferência passou em 83/83 checks e o conjunto
focado Python passou em 43/43. A nova suíte completa posterior passou em
**1.227/1.227**, zero falhas, zero erros e zero skips; evidência local em
`artifacts/release-qa/handoff-regression-final.xml`. Ainda são necessários
Windows isolado, backup real/restaurável, inventário do notebook, e só depois
mudança PROD, build oficial, manuais finais e entrega. Nenhum desses gates
pendentes foi inferido como aprovado pelos testes sintéticos.
