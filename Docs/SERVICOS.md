# Módulo Serviços

O módulo Serviços é um catálogo genérico, local e independente de Clientes. Ele não representa atendimento, venda ou execução de serviço.

## Persistência

As migrations aditivas `0003_services_catalog` e `0004_enable_services` criam e habilitam o módulo sem apagar usuários, clientes ou configurações existentes.

- `service_categories`: nome, descrição, ordem, status e autoria.
- `services`: código opcional e único, nome, descrição, categoria opcional, forma de cobrança, status e autoria.
- `service_prices`: valor `Numeric(12,2)`, início/fim da vigência, motivo e autor.

Não há categorias ou serviços empresariais inseridos automaticamente. Uma categoria em uso não é excluída; ela pode ser inativada e seus vínculos permanecem preservados.

## Formas de cobrança

Os identificadores ficam centralizados em `app/core/service_config.py`:

- `UNIT`: Por unidade — preço multiplicado pela quantidade.
- `KG`: Por kg.
- `PAIR`: Por par.
- `METER`: Por metro.
- `FIXED`: Preço fixo para uma execução.

O módulo ainda não calcula quantidade, total, venda ou OS. A distinção entre unidade e fixo já está preservada para essa fase futura.

## Preços e vigências

O preço atual nunca é sobrescrito. O registro vigente possui `valid_to IS NULL`. Ao alterar um preço, a vigência anterior é encerrada e um novo registro é criado com valor, momento, motivo opcional e usuário responsável.

Valores monetários são tratados com `Decimal` na aplicação e `Numeric(12,2)` no SQLAlchemy. Não se usa `float`.

## Inativação e auditoria

Serviços e categorias não são apagados pela interface. Inativação e reativação preservam histórico. Criação, edição, mudança de unidade, status e preço são registradas em `audit_events` sem expor SQL ao usuário.

## Permissões

- `services.view`
- `services.create`
- `services.edit`
- `services.deactivate`
- `services.prices.manage`
- `services.categories.manage`

Admin recebe acesso completo. User visualiza o catálogo por padrão e precisa de permissão específica para mudanças estruturais. Delivery não recebe acesso ao módulo.

## Contrato futuro com Atendimento/OS

Uma futura linha de OS deverá referenciar `service_id` e congelar o snapshot usado no atendimento:

- `service_name`
- `billing_unit`
- `unit_price`
- `quantity`
- `line_total`

Assim, alterações posteriores no nome, unidade ou preço do catálogo não modificam uma OS antiga.

Somente a futura conclusão de um Atendimento/OS poderá criar em Clientes a atividade `SERVICE_COMPLETED`, contendo ao menos `customer_id`, `service_id`, data e referência da OS. Cadastrar ou editar um item do catálogo não cria atividade de cliente.
