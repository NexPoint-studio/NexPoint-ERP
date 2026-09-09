# Módulo Serviços

O módulo Serviços reúne o catálogo configurável e a operação de
[Notas de Serviço](NOTAS_SERVICO.md). Tudo permanece local e genérico: cadastrar
ou alterar um item do catálogo não gera recebimento, movimento de Caixa ou
atividade de Cliente.

## Persistência do catálogo

- `service_categories`: nome, descrição, ordem, status e autoria.
- `billing_units`: código, nome, símbolo, comportamento da quantidade, precisão,
  status e ordem de exibição.
- `services`: código opcional e único, nome, descrição, categoria opcional,
  referência à unidade, status e autoria.
- `service_prices`: valor legado `Numeric(12,2)`, início/fim da vigência, motivo
  e autor.

A migration `0007_billing_units` cria as unidades e substitui o código textual
legado de cada Serviço por `billing_unit_id`. O rebuild preserva IDs, categorias,
status, autoria, datas e todo o histórico em `service_prices`. A FK final usa
`ON DELETE RESTRICT`, portanto uma unidade em uso pode ser inativada, mas não
removida deixando referências órfãs.

## Unidades configuráveis

Cada unidade define uma das regras abaixo. O backend consulta essa configuração;
não há regras de quantidade condicionadas ao código `KG`, `PAIR` ou a qualquer
segmento comercial.

| Comportamento | Quantidade |
|---|---|
| `INTEGER` | inteiro positivo |
| `DECIMAL` | decimal positivo, limitado por `decimal_places` |
| `FIXED_ONE` | exatamente 1 |

Os defaults da carcaça são `UNIT`, `FIXED`, `KG`, `METER`, `SQUARE_METER`,
`HOUR`, `DAY`, `SESSION`, `PAIR`, `PERSON`, `KM`, `LITER` e `PACKAGE`. O seed
insere somente códigos ausentes e nunca restaura campos de uma unidade já
customizada. A Administração completa dessas unidades pertence à Fase 3.

## Preços e vigências

O preço atual nunca é sobrescrito. O registro vigente possui
`valid_to IS NULL`; uma alteração encerra a vigência anterior e cria outra.
O catálogo legado continua usando `Decimal` e `Numeric(12,2)`. As novas Notas
convertem o preço vigente para centavos inteiros ao congelar o item, sem alterar
a persistência existente do catálogo.

## Interface e permissões

O catálogo mostra serviço, categoria, preço e nome/símbolo da unidade. Os
formulários oferecem somente unidades ativas, exceto quando a edição precisa
preservar uma unidade inativada já vinculada.

- `services.view`
- `services.create`
- `services.edit`
- `services.deactivate`
- `services.prices.manage`
- `services.categories.manage`

Admin recebe acesso completo. User visualiza o catálogo por padrão e precisa de
permissão específica para mudanças estruturais. Inativação preserva todos os
vínculos e históricos.
