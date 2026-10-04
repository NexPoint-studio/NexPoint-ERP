# Cadeado da Administração

O Admin Lock é uma barreira **opcional** para a área **Administração**. Todo
acesso à instalação ativada permite abrir essa área. Sem senha configurada,
a área abre diretamente e Segurança mostra
**DESATIVADA**. Com senha, o servidor exige o desbloqueio temporário também nas
rotas administrativas e nos serviços chamados por elas. Operações de Caixa,
Clientes, Notas e Financeiro permanecem disponíveis.

## Configuração, desbloqueio e troca

O cadeado pode ser configurado em Administração →
Segurança. A senha tem de 8 a 256
caracteres e o tempo de desbloqueio pode variar
de 1 a 480 minutos. O SQLite guarda somente o hash `scrypt` com salt; a senha em
texto não entra em cookie, sessão, template, log, auditoria ou chamado.

O servidor registra na sessão a versão do cadeado, o instante de expiração,
uma geração do processo e a identidade técnica da instalação.
Sair da Administração, timeout, reinício do ERP, restauração do banco ou
mudança da credencial invalidam o desbloqueio anterior. A troca exige a senha
administrativa atual. A remoção exige a senha atual e confirmação explícita;
a versão permanece monotônica, inclusive depois de remover e recriar a senha,
para impedir reutilização de cookies de desbloqueios antigos.

O desbloqueio não concede acesso ao Control Center. Restauração continua
exigindo confirmação explícita, validação de integridade e cópia anterior.
Com o cadeado ativo, tarefas internas que exijam Administração precisam de
contexto administrativo válido; a identidade técnica sozinha não o substitui.

## Recuperação única pela NexPoint

A interface oferece somente **Solicitar recuperação à NexPoint**. Não existem
campo de código, chave offline, ID de chamado ou token para copiar. A Nexa não
aparece como ação de recuperação e não pode iniciar, autorizar ou concluir um
reset. Se perguntada, ela apenas orienta o usuário a usar o botão na tela do
Admin Lock.

O endpoint confirma a identidade técnica da instalação e cria um chamado
`admin_access_recovery` com:

- tenant e instalação derivados no servidor;
- referência opaca exclusiva daquela solicitação, independente de conta humana;
- `correlation_id`;
- versão e status limitado do Admin Lock;
- tentativas recentes e diagnóstico sanitizado.

O chamado segue pela Outbox idempotente. Se a conexão estiver indisponível, ele
permanece localmente e a tela informa que será enviado quando a conexão voltar.
Caixa, Clientes, Serviços e Financeiro continuam operando; somente
a Administração permanece bloqueada. Por decisão de produto e segurança, a
senha administrativa esquecida não pode ser redefinida totalmente offline.

## Autorização no Control Center

Somente um `platform_admin` ativo pode usar **Autorizar redefinição** em um
chamado ativo da categoria correta. A autorização:

- expira em 15 minutos;
- vale uma única vez;
- fica vinculada ao chamado, tenant, instalação e pseudônimo solicitante;
- é revogada quando o chamado deixa de estar ativo;
- não contém senha antiga nem nova.

Um verificador aleatório interno é guardado apenas como hash e nunca é exibido.
Em LOCAL/DEV, o ERP consulta o sidecar confiável usando seus vínculos locais.
Em PROD, `SupabaseAdminRecoveryRemote` consulta e consome a autorização pelo
endpoint `erp-admin-recovery`, com a credencial de instalação e os vínculos
remotos previstos no contrato. O cliente não digita token ou identificador
técnico. Quando existe autorização válida, a tela
muda automaticamente para **Recuperação autorizada** e mostra somente “Nova
senha”, “Confirmar nova senha” e “Definir nova senha”.

O consumo e a mudança de estado da autorização são atômicos no Control Center.
Depois do consumo, o ERP grava um novo hash para o Admin Lock, incrementa sua
versão e registra auditoria e observabilidade. A operação modifica somente a
credencial do Admin Lock. Empresa, instalação, dados operacionais e credencial
protegida pelo Windows são preservados. O desbloqueio anterior é invalidado.
Uma nova solicitação não pode consumir autorização emitida para a anterior.
Se o processo parar depois do consumo remoto e antes do commit local, exige-se
nova autorização; o consumo não é desfeito nem reutilizado.

Esse fluxo de Admin Lock é distinto da redefinição da senha de um administrador
interno do Control Center. A arquitetura publicada está registrada em
[PROD_BASELINE.md](PROD_BASELINE.md).

## Compatibilidade do schema

A tabela histórica `admin_recovery_codes` e os campos antigos do cadeado foram
mantidos para evitar uma migration destrutiva. A migration
`0015_remember_sessions` revoga qualquer código legado ainda ativo sem apagar
linhas. Nenhum endpoint, serviço ou template usa essa tabela, e novos códigos
não são gerados.

## Auditoria e observabilidade

Os eventos operacionais incluem `admin.lock.failed_attempt`,
`admin.lock.lockout`, `admin.recovery.requested`,
`admin.recovery.queued_offline`, `admin.recovery.synced`,
`admin.recovery.authorized` e `admin.password.reset_completed`.

Sanitizadores continuam reconhecendo formatos legados para impedir vazamento de
registros antigos. Senha, hash, cookie, cabeçalho `Authorization` e qualquer
verificador interno não podem aparecer em logs, exports, Nexa, chamado ou
Control Center. Não existe senha mestra e nenhuma senha pode ser recuperada em
texto; o fluxo permite ao solicitante criar uma nova credencial após a
autorização da NexPoint.
