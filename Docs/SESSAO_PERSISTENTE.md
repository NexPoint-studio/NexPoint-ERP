# Sessão persistente do login

## Comportamento

A opção **Manter conectado neste dispositivo** pertence somente ao login normal
do ERP. Sem a opção, o cookie assinado é de sessão do navegador e o payload
expira em 12 horas por padrão. Com a opção, o ERP cria uma credencial opaca que
permite restaurar a sessão ao reabrir o perfil persistente do WebView2.

A opção não salva senha. O formulário nunca preenche a senha e o backend não a
grava em SQLite, configuração, cookie, `localStorage`, `sessionStorage`, log ou
frontend.

## Mecanismo escolhido

O cookie persistente contém uma versão, um identificador aleatório e um segredo
aleatório de alta entropia. Ele usa `HttpOnly`, `SameSite=Strict`, `Path=/` e
`Max-Age`; JavaScript não pode lê-lo. `Secure` fica desativado porque o desktop
serve exclusivamente em HTTP no loopback `127.0.0.1`. Ativá-lo nesse transporte
impediria o WebView2 de enviar o cookie.

O SQLite guarda somente SHA-256 aplicado ao segredo aleatório com separação de
contexto. A segurança não depende de senha humana. Cada registro também fica
vinculado a:

- um único usuário;
- a identidade persistente da instalação;
- `users.auth_version`;
- o hash de `security.session_generation`;
- expiração absoluta, último uso e instante de rotação.

A duração padrão é 30 dias e a rotação ocorre após 7 dias de uso, configuráveis
por `ERP_REMEMBER_SESSION_DAYS` e `ERP_REMEMBER_SESSION_ROTATION_DAYS`. A sessão
normal restaurada continua tendo a validade curta configurada por
`ERP_SESSION_HOURS`.

## Revogação e falha fechada

Logout revoga a credencial apresentada, invalida as sessões daquele usuário,
remove os cookies e volta ao login. Troca de senha da conta, desativação,
mudança relevante de papel/permissão e incremento de `auth_version` revogam as
sessões persistentes. Token corrompido, vínculo divergente, usuário inativo ou
expiração removem o cookie e mostram o login sem loop.

Uma tentativa com identificador conhecido e segredo errado não revoga a sessão
legítima, evitando negação de serviço por adivinhação. Ao entrar novamente neste
dispositivo, a credencial apresentada é revogada e uma nova é criada somente se
a opção estiver marcada. Assim, o cookie ativo identifica inequivocamente o
usuário atual, inclusive em computadores com mais de uma conta ERP.

## Offline

Validação e restauração usam somente o banco local, portanto funcionam sem
internet enquanto a credencial estiver válida. Um dispositivo totalmente
offline não descobre instantaneamente uma revogação feita em outro sistema; a
proteção local usa expiração absoluta, versão de autenticação e sincronização
quando a conexão retorna. Essa limitação não transforma a internet em requisito
para abrir o ERP.

O Admin Lock continua separado. Restaurar o login normal não restaura nem amplia
o tempo de desbloqueio da Administração e nunca reutiliza a senha de login.
