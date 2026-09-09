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
repositórios isolam consultas e persistência. Não existe adaptador remoto nesta
versão.

## Execução local

- FastAPI e pywebview usam exclusivamente `127.0.0.1`;
- o banco operacional fica em `data/erp.sqlite3` e não é versionado;
- configuração sensível fica em `.env.local`, também não versionado;
- HTML, CSS, JavaScript, fontes e imagens são locais;
- não há SDK, token, autenticação, storage ou sincronização de nuvem.

## Núcleo reutilizável

- registro declarativo de módulos e abas;
- autenticação local com hash de senha;
- papéis `admin`, `user` e `delivery` e permissões por ação;
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
- **Pagamentos**: quitação integral, formas, terminais, regras e snapshots de
  taxa;
- **Caixa operacional**: entradas e saídas manuais e histórico próprio recente;
- **Financeiro administrativo**: saldo, histórico completo, relatórios e
  agregações;
- **Administração**: indicadores, usuários, permissões e dados da empresa.

O fluxo integrado é:

```text
Cliente
   ↓
Nota de Serviço ──────> Histórico do Cliente
   ↓
Pagamento integral
   ↓
Entrada SYSTEM no Caixa
```

Criar a Nota registra atividade no Cliente, mas não movimenta dinheiro. Confirmar
um Pagamento de Nota positiva atualiza a Nota e cria o Caixa na mesma transação.
Uma Nota de total zero fica paga sem `Payment` e sem `CashMovement`.

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

## Contratos e migrations

A Nota segue o [contrato oficial](CONTRATO_NOTA_SERVICO.md) e sua
[documentação operacional](NOTAS_SERVICO.md). Dinheiro novo segue a
[estratégia monetária e de migrations](ESTRATEGIA_MONETARIA_MIGRATIONS.md).

As migrations `0009_payment_configuration`, `0010_payments` e
`0011_customer_activity_sources` adicionam a integração sem converter o histórico
manual do Caixa ou recriar atividades antigas. As definições já versionadas
permanecem congeladas, e novas mudanças devem usar outra versão aditiva.
