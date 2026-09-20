# Arquitetura do ERP local

## Fluxo das camadas

```text
HTML/CSS/JS + Jinja2
        ↓
Rotas FastAPI e autorização
        ↓
Serviços e transações de negócio
        ↓
Repositórios
        ↓
SQLAlchemy
        ↓
SQLite local
```

As telas não acessam o banco diretamente. As rotas cuidam de HTTP, sessão e
permissões; os serviços concentram regras e limites transacionais; os
repositórios isolam consultas e persistência. Adaptadores locais conectam a
Outbox ao sidecar isolado do Control Center e a interface same-origin à ponte
assinada da Nexa. Nenhum deles concede acesso direto ao SQLite operacional.

## Execução local

- FastAPI e pywebview usam exclusivamente `127.0.0.1`;
- o banco operacional fica em `data/erp.sqlite3` e não é versionado;
- configuração sensível fica em `.env.local`, também não versionado;
- HTML, CSS, JavaScript, fontes e imagens são locais;
- Outbox, Control Center e observabilidade persistem em armazenamento local
  isolado; esta versão não depende de SDK, storage ou sincronização de nuvem;
- a ponte opcional da Nexa usa HMAC, payload sanitizado e política read-only.

## Núcleo reutilizável

- registro declarativo de módulos e abas;
- autenticação local com hash de senha;
- papéis `admin`, `user`, `delivery` e `support` e permissões por ação;
- exibição do papel técnico `admin` como Proprietário da empresa;
- feature flags locais;
- eventos de auditoria;
- design tokens e componentes reutilizáveis;
- tratamento uniforme de acesso negado e sessão;
- migrations aditivas, idempotentes e verificadas.

## Módulos funcionais

- **Clientes**: cadastro, endereços, atividades, último retorno, último serviço e
  alertas de inatividade;
- **Serviços**: catálogo operacional e Notas de Serviço;
- **Administração > Serviços**: categorias, unidades universais, preços e gestão
  do catálogo;
- **Pagamentos**: recebimentos integrais ou parciais, formas, terminais, regras
  e snapshots de taxa;
- **Caixa operacional**: entradas e saídas manuais e histórico próprio recente;
- **Financeiro administrativo**: saldo, histórico completo, relatórios e
  agregações;
- **Administração**: indicadores, catálogo, financeiro, pagamentos, suporte,
  auditoria e sistema. Os backends legados de usuários/permissões e dados da
  empresa permanecem preservados, porém não aparecem na navegação normal.
- **Manutenção administrativa**: suporte temporário, auditoria, informações do
  sistema, backup consistente e restauração aplicada somente no startup.

O fluxo integrado é:

```text
Cliente
   ↓
Nota de Serviço ──────> Histórico do Cliente
   ↓
Pagamento integral ou parcial
   ↓
Entrada SYSTEM no Caixa
```

Criar a Nota registra atividade no Cliente, mas não movimenta dinheiro. Confirmar
cada Pagamento de Nota positiva atualiza o ledger e cria o Caixa na mesma
transação. Fechar a Nota não cria Caixa; eventual saldo devedor permanece ligado
à origem e pode ser quitado depois. Uma Nota de total zero fica paga sem
`Payment` e sem `CashMovement`.

## Limite transacional financeiro

O recebimento inicia `BEGIN IMMEDIATE` e coordena:

- criação de `Payment`;
- atualização financeira da Nota;
- entrega opcional;
- criação da entrada `SYSTEM` no Caixa;
- eventos de Nota;
- auditoria.

Uma falha desfaz o conjunto inteiro. UUID de requisição, índices únicos e revisão
da Nota protegem retries e concorrência. O domínio novo usa centavos inteiros; a
ponte para os campos `Numeric(14,2)` do Caixa relê e reconcilia os valores antes
do commit.

## Configuração e precedência

`.env.local` fornece parâmetros técnicos e valores iniciais. O bootstrap insere
configurações funcionais somente quando a chave ainda não existe. Nas páginas,
os valores persistidos em `settings` prevalecem para nome da empresa, nome do
aplicativo, versão, logo e fuso; o ambiente funciona como fallback.

Segredo de sessão, host, porta e caminho do SQLite continuam sob configuração
local de inicialização. Logos aceitos precisam estar em `/static/`, e um fuso
persistido inválido não substitui o fallback seguro.

Sessões assinadas também dependem de `users.auth_version` e da configuração
`security.session_generation`. O middleware relê esses valores e as permissões
efetivas a cada requisição. Assim, logout e alterações de acesso invalidam
cookies copiados; a revogação ou expiração de suporte remove o alcance sem
depender do conteúdo anterior do cookie.

Quando o usuário opta por **Manter conectado neste dispositivo**, um cookie
`HttpOnly` separado carrega uma credencial opaca e revogável. A tabela
`remember_sessions` guarda somente o hash do segredo aleatório, com vínculo ao
usuário, instalação, `auth_version`, geração local, expiração e rotação. O
restauro é inteiramente local e funciona offline. O Admin Lock mantém sessão e
timeout próprios e nunca é restaurado por essa credencial.

Backups usam a API de snapshot do SQLite, manifesto com SHA-256 e validações de
integridade. A requisição de restauração apenas prepara um candidato isolado. A
troca atômica e o rollback ocorrem antes da criação do engine normal no próximo
início. O contrato completo está em
[Backup, restauração e atualização](BACKUP_RESTAURACAO_ATUALIZACAO.md).

## Contratos e migrations

A Nota segue o [contrato oficial](CONTRATO_NOTA_SERVICO.md) e sua
[documentação operacional](NOTAS_SERVICO.md). Dinheiro novo segue a
[estratégia monetária e de migrations](ESTRATEGIA_MONETARIA_MIGRATIONS.md).

As migrations `0009_payment_configuration`, `0010_payments`,
`0011_customer_activity_sources`, `0012_administration_security`,
`0013_offline_finance_admin`, `0014_functional_ux_recovery` e
`0015_remember_sessions` adicionam a integração, o financeiro offline, a
recuperação administrativa e as sessões persistentes sem converter o histórico
manual do Caixa ou recriar atividades antigas. A `0015` também revoga, sem
apagar, códigos de recuperação legados que ainda estivessem ativos. As
definições já versionadas permanecem congeladas, e novas mudanças devem usar
outra versão aditiva.
