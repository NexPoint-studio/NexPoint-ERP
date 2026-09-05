# Módulo Clientes

O módulo Clientes é local, genérico e independente dos módulos Caixa e Serviços.
Ele usa exclusivamente SQLAlchemy e o SQLite da própria carcaça.

## Funcionalidades

- cadastro e edição de pessoa ou empresa;
- CPF e CNPJ opcionais, normalizados e validados localmente;
- bloqueio de documento duplicado;
- aviso confirmável para telefone ou WhatsApp possivelmente duplicado;
- endereço estruturado com preenchimento manual;
- pesquisa, filtros, ordenação e paginação;
- perfil com dados, endereço, situação e linha do tempo;
- registro manual de visita;
- inativação e reativação sem exclusão do histórico;
- histórico geral filtrável;
- classificação configurável do tempo de relacionamento.

## Persistência

As migrações aditivas criam:

- `customers`: cadastro principal, status e autoria;
- `customer_addresses`: endereço individual estruturado;
- `customer_activities`: linha do tempo imutável de eventos;
- `schema_migrations`: controle idempotente das versões locais.

As atividades aceitas são `CUSTOMER_CREATED`, `CUSTOMER_UPDATED`,
`CUSTOMER_DEACTIVATED`, `CUSTOMER_REACTIVATED`, `VISIT`, `NOTE`,
`SERVICE_CREATED` e `SERVICE_COMPLETED`. As duas últimas já definem o contrato
para a integração futura com Serviços, mas ainda não são produzidas nesta fase.

## Relacionamento e inatividade

A última atividade relevante é uma visita ou serviço concluído. Os limites
iniciais são 30, 60 e 90 dias e ficam nas configurações locais:

- `customers.inactivity.recent_days`;
- `customers.inactivity.attention_days`;
- `customers.inactivity.distant_days`.

Sem atividade relevante, o cliente aparece como `Nunca atendido`. Depois disso,
a situação evolui para `Recente`, `Atenção`, `Afastado` e `Há muito tempo`.

O status cadastral (`Ativo` ou `Inativo`) é independente dessa classificação.
Inativar um cadastro não apaga o cliente nem altera artificialmente a data da
última visita.

## Busca, filtros e ordenação

A lista pesquisa parcialmente por nome, nome fantasia, documento, telefone,
WhatsApp e e-mail. Os filtros cobrem Pessoa/Empresa, Ativo/Inativo, Nunca
atendido, recente, 30/60/90+ dias e um número customizado de dias. A ordenação
aceita nome, cadastro e última atividade, e a paginação consulta 25, 50 ou 100
clientes por vez sem carregar toda a tabela.

## Permissões

- `customers.view`: lista, perfil e histórico;
- `customers.create`: novo cadastro;
- `customers.edit`: edição;
- `customers.deactivate`: inativar ou reativar;
- `customers.activity.create`: registrar visita.

Administrador e usuário comum recebem essas permissões. O papel Entrega não
recebe acesso ao módulo.

## Testes

Execute offline:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Os testes usam bancos SQLite temporários e dados fictícios. Eles não modificam
o banco utilizado pelo aplicativo desktop.
