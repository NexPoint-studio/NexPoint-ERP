# Cadeado da Administração

O Admin Lock é uma segunda barreira local para a área **Administração**. Ele não
substitui o login normal, papéis ou permissões. Mesmo com uma sessão normal
lembrada, o usuário precisa informar a senha administrativa adicional enquanto o
ciclo temporário de desbloqueio não estiver válido.

## Configuração, desbloqueio e troca

Somente um Proprietário ativo com `admin.overview.view` pode configurar o
cadeado. A senha tem de 8 a 256 caracteres e o tempo de desbloqueio pode variar
de 1 a 480 minutos. O SQLite guarda somente o hash `scrypt` com salt; a senha em
texto não entra em cookie, sessão, template, log, auditoria ou chamado.

O servidor registra na sessão apenas a versão do cadeado, o instante de
expiração e uma geração do processo. Timeout, logout, reinício do ERP ou mudança
da credencial invalidam o desbloqueio anterior. A troca normal exige a senha
administrativa atual e incrementa a versão do cadeado.

## Recuperação única pela NexPoint

A interface oferece somente **Solicitar recuperação à NexPoint**. Não existem
campo de código, chave offline, ID de chamado ou token para copiar. A Nexa não
aparece como ação de recuperação e não pode iniciar, autorizar ou concluir um
reset. Se perguntada, ela apenas orienta o Proprietário a usar o botão na tela do
Admin Lock.

O endpoint confirma novamente que a conta autenticada é um Proprietário legítimo
e cria um chamado `admin_access_recovery` com:

- tenant e instalação derivados no servidor;
- pseudônimo do solicitante;
- `correlation_id`;
- versão e status limitado do Admin Lock;
- tentativas recentes e diagnóstico sanitizado.

O chamado segue pela Outbox idempotente. Se a conexão estiver indisponível, ele
permanece localmente e a tela informa que será enviado quando a conexão voltar.
Caixa, Clientes, Serviços e outras áreas autorizadas continuam operando; somente
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
O ERP consulta o sidecar confiável usando seus vínculos locais; o cliente não
digita token ou identificador técnico. Quando existe autorização válida, a tela
muda automaticamente para **Recuperação autorizada** e mostra somente “Nova
senha”, “Confirmar nova senha” e “Definir nova senha”.

O consumo e a mudança de estado da autorização são atômicos no Control Center.
Depois do consumo, o ERP grava um novo hash para o Admin Lock, incrementa sua
versão e registra auditoria e observabilidade. A operação modifica somente a
credencial do Admin Lock: login normal, usuário, papel, permissões, tenant e
sessão normal permanecem iguais.

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
texto; o fluxo permite ao Proprietário criar uma nova credencial após a
autorização da NexPoint.
