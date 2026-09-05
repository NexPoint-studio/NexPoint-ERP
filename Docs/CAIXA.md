# Módulo Caixa

O módulo Caixa é um livro-caixa operacional genérico e totalmente local. Ele registra dinheiro efetivamente realizado, informa quanto entrou, quanto saiu e qual é o saldo, sem assumir funções de contabilidade completa, contas a pagar, contas a receber ou conciliação bancária.

O banco normal da carcaça começa com saldo de `R$ 0,00` e nenhuma movimentação. Não existe importação automática de histórico financeiro, criação mensal de saldo inicial ou seed de movimentações empresariais.

## Movimentações

A entidade `cash_movements` mantém, no mínimo:

- tipo, descrição e data/hora da ocorrência;
- categoria e forma de pagamento opcionais;
- valor bruto, taxa e valor líquido;
- observações e referência de origem opcionais;
- status e origem;
- datas e usuários de criação e alteração;
- data, usuário e motivo de cancelamento.

Os tipos aceitos são:

- `ENTRY`: entrada que aumenta o saldo pelo seu valor líquido;
- `EXIT`: saída que reduz o saldo pelo seu valor líquido.

Os status aceitos são:

- `ACTIVE`: participa dos saldos, totais e relatórios;
- `CANCELED`: permanece no histórico, mas não participa de nenhum cálculo financeiro.

As origens previstas são:

- `MANUAL`: lançamento realizado por um usuário no Caixa;
- `SYSTEM`: reservado para integrações internas futuras.

Nesta fase, os lançamentos normais são manuais. Cliente e serviço não são obrigatórios e nenhum cadastro desses módulos gera movimentação financeira automaticamente.

## Valores, taxa e precisão

Valores financeiros são tratados com `Decimal` na aplicação e `Numeric(14,2)` no SQLAlchemy. `float` não deve ser usado em regras ou persistência monetária.

Sem taxa:

```text
valor bruto = R$ 100,00
taxa        = R$   0,00
valor líquido = R$ 100,00
```

Em uma entrada com taxa manual opcional:

```text
valor líquido = valor bruto - taxa
```

A taxa não pode ser negativa, maior que o valor bruto ou produzir valor líquido negativo. Para saídas, a taxa é `R$ 0,00` nesta versão e o valor líquido é igual ao valor bruto. Custos adicionais de saídas não são inferidos automaticamente.

## Saldo atual, saldo anterior e resultado

Todos os cálculos consideram somente movimentações `ACTIVE`.

O saldo atual é acumulado sobre todas as movimentações realizadas até o momento:

```text
saldo atual = soma das entradas líquidas - soma das saídas líquidas
```

Para um período, o saldo anterior é o saldo acumulado antes do início do período. Ele é calculado a partir das movimentações existentes e não é salvo como um lançamento artificial.

```text
resultado do período = entradas líquidas do período - saídas do período

saldo final = saldo anterior
            + entradas líquidas do período
            - saídas do período
```

Resumo, Histórico e Relatórios devem usar a mesma regra centralizada para impedir divergência de centavos ou de status.

## Cancelamento e edição

Movimentações não são apagadas fisicamente pela operação normal. Um lançamento incorreto deve ser cancelado, com motivo obrigatório.

Ao cancelar, são registrados `canceled_at`, `canceled_by` e `cancellation_reason`. O registro continua consultável com status `CANCELED`, mas seu valor deixa imediatamente de participar do saldo, das entradas, das saídas e dos relatórios. Não há reativação de lançamento cancelado nesta versão; se necessário, deve-se criar um novo lançamento correto.

A edição depende de permissão específica. Mudanças em descrição, categoria, pagamento, data, observações, valores ou taxa atualizam a autoria e geram auditoria legível, especialmente para alterações financeiras.

Os eventos relevantes incluem:

- `CASH_MOVEMENT_CREATED`;
- `CASH_MOVEMENT_UPDATED`;
- `CASH_MOVEMENT_CANCELED`;
- `CASH_CATEGORY_CREATED`;
- `CASH_CATEGORY_UPDATED`;
- `CASH_CATEGORY_DEACTIVATED`.

## Categorias

`cash_categories` organiza movimentações sem impor categorias de um ramo empresarial. Uma categoria possui nome, descrição opcional, ordem, status e um tipo de aplicação:

- `ENTRY`: somente entradas;
- `EXIT`: somente saídas;
- `BOTH`: entradas e saídas.

O vínculo é opcional. Quando não houver categoria, a interface apresenta `Sem categoria`. Categorias podem ser criadas de forma discreta dentro do Caixa e inativadas sem apagar o histórico já vinculado.

## Formas de pagamento

`cash_payment_methods` mantém nome, ordem de exibição, status e datas de criação e alteração. A configuração inicial genérica contém:

- Dinheiro;
- Pix;
- Cartão;
- Boleto;
- Outro.

Selecionar `Boleto` significa que o boleto já foi recebido e que o dinheiro está sendo realizado no Caixa. Um boleto ainda pendente não aumenta o saldo e pertence a um futuro módulo de contas a receber.

Formas de pagamento podem ser inativadas para novos lançamentos sem perder as referências históricas.

## Resumo

A tela Resumo apresenta:

- saldo atual acumulado;
- saldo anterior ao mês selecionado;
- entradas líquidas, saídas e resultado do mês;
- movimentações recentes;
- entradas do mês por forma de pagamento;
- principais entradas e saídas por categoria quando existirem dados.

Na ausência de movimentações, todos os valores permanecem zerados e a interface apresenta um estado vazio com acesso ao novo lançamento.

## Histórico

O Histórico abre no mês atual e oferece atalhos para Hoje, Esta semana, Este mês, Este ano e período personalizado. A data inicial não pode ser posterior à data final.

A consulta permite buscar por descrição, observação, categoria e forma de pagamento, além de filtrar por tipo, status, categoria e pagamento. A ordenação contempla mais recentes, mais antigos, maior valor e menor valor. A paginação real usa 25 itens por padrão e permite 25, 50 ou 100, preservando filtros, busca, período e ordenação.

O detalhe de uma movimentação mostra valores, classificação, pagamento, data, observações, autoria, datas de criação e alteração, origem e status. Cancelados permanecem identificados visualmente no histórico.

## Relatórios

Relatórios começam no mês atual, aceitam os mesmos períodos rápidos e um intervalo personalizado. Os indicadores são:

- saldo anterior ao período;
- entradas brutas;
- taxas;
- entradas líquidas;
- saídas;
- resultado operacional;
- saldo final;
- quantidade de movimentações ativas.

As visões agregadas incluem:

- entradas e saídas por categoria, com quantidade e total;
- formas de pagamento, com quantidade, valor bruto, taxas e valor líquido;
- evolução temporal de entradas, saídas e resultado.

Períodos curtos podem ser agrupados por dia e períodos maiores por mês, mantendo a escolha de agrupamento centralizada. Gráficos são apenas uma representação dos mesmos dados calculados pelos serviços; o relatório continua funcional sem eles.

Movimentações canceladas nunca aparecem nos totais financeiros, embora possam ser consultadas no Histórico.

## Permissões

O módulo utiliza permissões granulares:

- `cash.view`;
- `cash.create`;
- `cash.edit`;
- `cash.cancel`;
- `cash.reports.view`;
- `cash.categories.manage`.

Admin recebe todas as permissões do Caixa. User recebe `cash.view` e `cash.create` por padrão e pode receber outras permissões explicitamente. Delivery não recebe acesso automático ao Caixa. As rotas e serviços validam a permissão necessária; a interface não depende apenas do nome do perfil.

## Contrato futuro com Atendimento, OS e Pagamentos

O fluxo conceitual futuro é:

```text
Atendimento/OS
    ↓
Pagamento aprovado
    ↓
Movimentação no Caixa
```

Quando esse fluxo for implementado, o módulo interno responsável poderá criar uma entrada com `origin = SYSTEM` e uma referência de origem. O contrato deve conter identificadores equivalentes a:

- `source_type`, por exemplo `PAYMENT`;
- `source_id`, identificador imutável do pagamento;
- `source_reference`, referência legível, por exemplo o número da OS.

A combinação `source_type + source_id` deve ser única para impedir que o mesmo pagamento gere duas movimentações. O cadastro de Cliente, Serviço ou OS isoladamente não representa recebimento e não cria lançamento. Esta integração fica somente preparada por contrato; ela não é implementada nesta fase.

## Saldo inicial de implantação futura

Uma implantação específica poderá oferecer a ação administrativa controlada `Definir saldo inicial de implantação`, executada uma única vez na data de corte. Esse valor representa a posição financeira conferida no cutover, não uma importação automática do histórico antigo.

Antes do cutover, os dados antigos permanecem preservados em seus arquivos e backups. A nova operação começa com novos lançamentos a partir da data oficial. Não se cria saldo inicial mensal e nenhum histórico anterior é apagado ou transportado automaticamente.
