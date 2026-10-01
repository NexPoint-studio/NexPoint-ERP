# Instalação e ativação Windows

O instalador instala por usuário, cria os atalhos e registra a desinstalação.
O programa fica em `%LOCALAPPDATA%\Programs\NexPoint ERP`; os dados permanecem
em `%LOCALAPPDATA%\NexPoint\ERP` mesmo após desinstalar. Inno Setup fica somente
no computador de build. O notebook precisa de Windows x64. O Setup detecta
WebView2 e, se necessário, executa o bootstrapper Evergreen assinado pela
Microsoft, incluído no instalador; essa preparação precisa de internet.
O mecanismo segue a [documentação oficial da Microsoft](https://learn.microsoft.com/en-us/microsoft-edge/webview2/concepts/distribution).

## Contrato de primeiro acesso

1. O pacote contém somente o perfil público `activation-profile.json`:
   `schema_version`, `tenant_key`, `installation_key`, `company_name`, `username`.
   Esse arquivo identifica a entrega e não autoriza nenhuma operação.
2. O usuário entra pela tela local de primeira configuração. A senha segue a
   política operacional existente, de 8 a 256 caracteres. Nada é gravado em HTML,
   JavaScript, URL, log ou pacote. A tela exige origem loopback exata e token CSRF.
3. O desktop gera uma chave aleatória e um request ID, grava e relê o estado
   protegido por DPAPI CurrentUser **antes** de contatar a nuvem.
4. `erp-activate` recebe login, senha, escopo e somente SHA-256 da chave. HTTPS
   verifica certificado/host, sem redirects. A função limita corpo, trabalho e
   tentativas duráveis antes de verificar scrypt N16384/r8/p1, igual ao ERP.
5. A autorização administrativa é ativa, revogável, expirável e limitada a uma
   instalação. Não é uma conta Control Center. Nenhum token administrativo vai
   ao cliente. A Edge usa sua própria service role exclusivamente no servidor.
6. Uma transação cria instalação, uma credencial e consumo da autorização. Retry
   autenticado exige o mesmo request ID, digest e key ID, no máximo sete dias
   após o consumo. Outra máquina/chave é rejeitada. Revogação sempre impede retry
   remoto. Alterações relevantes mudam a versão do grant e fecham corridas.
7. A resposta autorizada contém identidade e hash scrypt de um proprietário local
   técnico **novo**, independente do operador e da conta administrativa da nuvem.
   Sua senha forte fica no cofre DPAPI do responsável, fora da entrega. Esse hash
   é necessário para a manutenção posterior do SQLite e nunca aparece em logs.
8. O desktop guarda a confirmação protegida, cria banco limpo em staging, aplica
   migrations, cria proprietário técnico e usuário operacional pelo AdminService,
   verifica integridade/FKs/schema, publica sem sobrescrita e grava a identidade
   final no Windows local. Nenhum catálogo, cliente, Nota ou pagamento fictício é
   criado. O usuário operacional recebe somente o papel `user`.
9. O login oficial do ERP cria a sessão, incluindo Manter conectado se escolhido.
   O segundo start usa banco e identidade locais, sem consultar a autorização.

O endpoint não emite a chave em plaintext: autoriza o segredo já gerado no
Windows final, persistindo somente seu digest no contrato existente. Roubar o
instalador não fornece senha, chave operacional ou autorização válida.

## Interrupções e preservação

`credentials/activation.dpapi` conserva o request e a confirmação para retomada.
Um lock de arquivo do sistema operacional evita dois provisionamentos locais.
Depois de uma resposta autorizada, a retomada local exige a mesma senha e pode
ocorrer sem internet. Se o ACK se perder antes disso, o retry remoto é idempotente.

Banco existente precisa ter o marcador daquele request, empresa, usuário e
proprietário esperados. Arquivos corrompidos, links, identidade diferente ou
credencial de outro Windows causam recusa; nenhum reset automático ocorre.
Instalações anteriores completas continuam usando seu fluxo normal offline.

## Preparação pelo responsável

Depois dos testes SQL/Edge e da migration aditiva, preparar uma autorização com
`scripts/prepare_activation_grant.py`. Ele lê explicitamente o verificador de uma
conta operacional aprovada em modo read-only, cria a senha técnica independente
em DPAPI no PC do responsável e registra o grant por sete dias. Não replica banco
ou credencial da instalação antiga e não cria a instalação antes do primeiro uso.
Tentativas de reconciliar grants divergentes exigem revisão, sem rotação silenciosa.

Nenhuma senha vai em argumentos CLI: o comando usa cofres e verificadores locais.
O arquivo protegido `activation-owner-<installation_key>.dpapi` é privado do
responsável e não pode acompanhar o instalador. A senha operacional é entregue
separadamente, como antes. O perfil público precisa corresponder exatamente ao
grant; a função valida o escopo independentemente desse arquivo.

## Build e validação

Gerar o bundle com `scripts/build_windows_prod.ps1` a partir de commit limpo,
passando `-ActivationProfilePath` para o perfil público da entrega. O builder
valida o contrato, copia esse arquivo e o inclui no inventário SHA256SUMS. Gerar o Setup com
`scripts/build_windows_installer.ps1`, informando versão e commit esperados.
O builder verifica o compilador assinado, manifesto, hashes e secrets antes e
depois de compilar. O instalador NexPoint ainda precisa de certificado próprio
para assinatura Authenticode; assinatura do compilador não assina o Setup.

Testes: `tests/test_installation_activation.py`,
`supabase/functions/erp-activate/handler_test.ts` e
`scripts/security/test_activation_database.py`. O último usa PostgreSQL novo,
sem rede externa, e não acessa nenhum container de projeto ou banco PROD.

Além dos scans do repositório, é necessário examinar o payload extraído do Setup,
o bundle PyInstaller, textos dos PDFs e toda a entrega. Um scan superficial do
EXE comprimido não demonstra ausência de secrets no conteúdo.

## Aceite no notebook

Testes locais não comprovam a execução no Windows da usuária. Após instalar,
confirmar login, primeiro e segundo start, ausência de nova ativação, health,
heartbeat e ACK/synced sob a instalação nova. Somente usar operações reais em
PROD. Preparar serviços/preços reais com o responsável: conta operacional não
pode inventar catálogo nem administrar preços. A sincronização é de eventos,
não uma réplica do SQLite ou substituto do backup.

O tenant existente permanece com seu nome global. O Control Center distingue os
computadores pelo label e pela installation key; a empresa configurada no ERP
nunca renomeia o tenant nem o computador principal.
