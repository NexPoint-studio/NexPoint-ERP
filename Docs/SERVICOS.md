# Módulo Serviços

Serviços possui uma área operacional e uma área administrativa. Ambas reutilizam
o mesmo catálogo e as mesmas regras de negócio.

Na operação, o módulo apresenta somente:

- Catálogo para consulta;
- Nova Nota;
- Notas de Serviço.

A manutenção estrutural fica em Administração > Serviços:

- criar, editar, ativar e inativar serviços;
- manter categorias;
- criar e manter unidades de cobrança;
- alterar preços e consultar seu histórico.

Cadastrar ou alterar um item do catálogo não cria Pagamento, movimentação de
Caixa nem atividade de Cliente. A integração com Cliente e Caixa acontece pelas
[Notas de Serviço](NOTAS_SERVICO.md).

## Persistência do catálogo

- `service_categories`: nome, descrição, ordem, status e autoria;
- `billing_units`: código, nome, símbolo, comportamento da quantidade, precisão,
  status e ordem de exibição;
- `services`: código opcional e único, nome, descrição, categoria opcional,
  referência à unidade, status e autoria;
- `service_prices`: valor legado `Numeric(12,2)`, início e fim da vigência,
  motivo e autor.

A migration `0007_billing_units` criou as unidades e substituiu o código textual
legado de cada Serviço por `billing_unit_id`. O rebuild preservou IDs,
categorias, status, autoria, datas e todo o histórico em `service_prices`. A FK
usa `ON DELETE RESTRICT`.

## Unidades de cobrança

A Administração permite criar uma unidade com código estável e editar nome,
símbolo, comportamento de quantidade, precisão, ordem e status. O código não é
alterado depois da criação. Uma unidade em uso pode ser inativada, sem apagar o
histórico nem quebrar o vínculo dos Serviços.

Cada unidade define uma das regras abaixo. O backend consulta a configuração;
não há regra de quantidade condicionada ao código da unidade ou a um segmento
comercial.

| Comportamento | Quantidade |
|---|---|
| `INTEGER` | inteiro positivo |
| `DECIMAL` | decimal positivo com uma a seis casas configuradas |
| `FIXED_ONE` | exatamente 1 |

Os defaults genéricos são `UNIT`, `FIXED`, `KG`, `METER`, `SQUARE_METER`,
`HOUR`, `DAY`, `SESSION`, `PAIR`, `PERSON`, `KM`, `LITER` e `PACKAGE`. O seed
insere somente códigos ausentes e nunca restaura campos já customizados.

Uma Nota congela código, nome, símbolo, comportamento e precisão da unidade no
item. Alterar ou inativar a unidade posteriormente não modifica esses snapshots.

## Preços e vigências

O preço atual nunca é sobrescrito. Uma alteração encerra a vigência anterior e
cria outra. O catálogo legado continua usando `Decimal` e `Numeric(12,2)`; a
Nota converte o preço vigente para centavos inteiros ao congelar o item.

Itens já registrados conservam nome, descrição, categoria, unidade, quantidade,
preço unitário e subtotal do momento da Nota. Nenhuma alteração do catálogo
reescreve o histórico operacional ou financeiro.

## Interface e rotas protegidas

O catálogo operacional mostra serviço, categoria, preço e unidade e não oferece
ações administrativas. A Administração usa rotas sob `/admin/servicos` para a
gestão.

As URLs legadas de criação, edição, preço, categoria e status continuam
compatíveis, porém exigem as mesmas permissões administrativas no backend. Um
operador não ganha acesso à gestão enviando um endereço ou POST diretamente.

Permissões de consulta e gestão:

- `services.view`: consultar o catálogo operacional;
- `admin.services.view`: abrir a gestão do catálogo;
- `admin.services.create`: criar Serviço;
- `admin.services.edit`: editar Serviço;
- `admin.services.deactivate`: ativar ou inativar Serviço;
- `admin.services.prices.manage`: alterar e consultar preços;
- `admin.services.categories.manage`: manter categorias;
- `admin.services.units.manage`: manter unidades de cobrança.

O Proprietário recebe acesso completo. O papel `user` consulta o catálogo por
padrão e precisa de concessão administrativa explícita para qualquer mudança
estrutural. Permissões legadas existentes são migradas para sucessoras de forma
aditiva, preservando concessões personalizadas.
