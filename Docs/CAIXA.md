# Módulo Caixa

O Caixa é um livro-caixa local de valores efetivamente realizados. Ele registra
quanto entrou, quanto saiu e o saldo operacional, sem assumir funções de
contabilidade completa, contas a pagar, contas a receber ou conciliação bancária.

O banco começa com saldo de `R$ 0,00` e nenhuma movimentação. Não existe
importação automática de histórico financeiro, criação mensal de saldo inicial
ou seed de movimentações empresariais.

## Caixa operacional e Financeiro

O módulo possui dois alcances autorizados no backend.

O **Caixa operacional** permite ao operador registrar entradas e saídas manuais
e consultar somente os lançamentos que ele próprio criou nos últimos sete dias.
A consulta é paginada e não calcula nem retorna saldo, total, resultado, gráfico
ou agregação global. O detalhe, a edição e o cancelamento também respeitam esse
mesmo autor e intervalo.

O **Financeiro administrativo**, acessado pela Administração, oferece:

- saldo atual e saldo anterior;
- entradas brutas e líquidas, saídas, taxas e resultado operacional;
- histórico completo com busca, filtros, ordenação e paginação;
- relatórios e gráficos;
- agrupamentos por categoria e forma de pagamento.

O resultado do período é operacional e não é apresentado como lucro contábil.

## Movimentações

`cash_movements` mantém tipo, descrição, data e hora, categoria e forma de
pagamento opcionais, valores bruto/taxa/líquido, observações, status, origem,
referência de origem, autoria e metadados de cancelamento.

Os tipos são:

- `ENTRY`: aumenta o saldo pelo valor líquido;
- `EXIT`: reduz o saldo pelo valor líquido.

Os status são:

- `ACTIVE`: participa dos cálculos;
- `CANCELED`: permanece no histórico, sem participar dos totais.

As origens são:

- `MANUAL`: entrada ou saída criada no Caixa por um usuário;
- `SYSTEM`: entrada criada por uma integração interna autorizada.

O cadastro isolado de Cliente, Serviço ou Nota não movimenta o Caixa. Uma Nota
positiva gera entrada `SYSTEM` somente quando o seu Pagamento é confirmado.

## Lançamentos manuais

Entradas e saídas manuais permanecem independentes de Cliente ou Nota. O vínculo
com categoria e forma de pagamento é opcional. Referências inativas já usadas
podem ser preservadas em uma edição, mas não são selecionáveis para um novo
lançamento.

Movimentações não são apagadas pela operação normal. Um lançamento manual
incorreto pode ser cancelado com motivo obrigatório. O registro continua
consultável e deixa de participar imediatamente dos cálculos. Uma edição ou um
cancelamento autorizado registra auditoria.

## Entradas automáticas de Pagamento

Ao confirmar qualquer Pagamento integral ou parcial de uma Nota, a mesma
transação cria uma entrada com:

```text
origin = SYSTEM
source_type = PAYMENT
source_id = ID técnico do Payment
source_reference = identificação legível da Nota
```

A combinação de origem é única, impedindo duas movimentações para o mesmo
Pagamento. Uma entrada `SYSTEM` não pode ser editada nem cancelada diretamente
pelas rotas ou pelo serviço do Caixa, pois isso deixaria a quitação e o livro-caixa
divergentes.

O fechamento da Nota não cria movimentação. Se ele gerar saldo devedor, cada
quitação posterior cria um `ReceivablePayment` e uma entrada `SYSTEM` com
`source_type=RECEIVABLE_PAYMENT`. Assim, o Caixa contém somente valores
efetivamente recebidos e não antecipa a dívida.

O vínculo e a atomicidade estão detalhados em
[Administração e Pagamentos](PAGAMENTOS_ADMINISTRACAO.md).

## Valores, taxa e precisão

Os lançamentos históricos do Caixa continuam em `Numeric(14,2)` e são tratados
com `Decimal`; regras monetárias nunca usam `float`.

Sem taxa:

```text
valor bruto   = R$ 100,00
taxa          = R$   0,00
valor líquido = R$ 100,00
```

Em uma entrada com taxa:

```text
valor líquido = valor bruto - taxa
```

A taxa não pode ser negativa, maior que o bruto ou produzir líquido negativo.
Saídas possuem taxa zero e líquido igual ao bruto nesta versão.

Pagamentos calculam seus valores em centavos inteiros. Ao criar a movimentação
legada, o servidor grava os valores decimais, relê o banco e compara bruto, taxa
e líquido convertidos novamente para centavos. Qualquer diferença provoca
rollback integral do Pagamento e do Caixa.

## Saldo e resultado

Todos os cálculos consideram somente movimentações `ACTIVE`:

```text
saldo atual = soma das entradas líquidas - soma das saídas líquidas

resultado do período = entradas líquidas do período - saídas do período

saldo final = saldo anterior
            + entradas líquidas do período
            - saídas do período
```

O saldo anterior é calculado a partir das movimentações anteriores ao intervalo;
ele não cria um lançamento artificial. Resumo, Histórico e Relatórios usam a
mesma regra centralizada.

## Categorias

`cash_categories` organiza movimentos sem impor um ramo empresarial. A categoria
possui nome, descrição opcional, ordem, status e aplicação:

- `ENTRY`: somente entradas;
- `EXIT`: somente saídas;
- `BOTH`: entradas e saídas.

Uma categoria pode ser inativada sem apagar lançamentos históricos. A gestão de
categorias exige permissão administrativa financeira.

## Formas de pagamento

`cash_payment_methods` é o catálogo compartilhado com Pagamentos. Além de nome,
ordem e status, cada forma possui `method_kind`: `CASH`, `PIX`, `CARD`, `BOLETO`
ou `OTHER`. A configuração inicial genérica contém Dinheiro, Pix, Cartão, Boleto
e Outro.

Selecionar Boleto significa que o valor já foi recebido. Um boleto ainda
pendente não aumenta o saldo. Formas podem ser inativadas sem perder referências
históricas. A gestão de formas, terminais e taxas fica em Administração >
Pagamentos e taxas.

## Histórico e relatórios administrativos

O histórico global abre no mês atual e oferece Hoje, Esta semana, Este mês, Este
ano e período personalizado. A busca cobre descrição, observação, categoria e
forma de pagamento; os filtros incluem tipo, status, categoria e pagamento. A
ordenação contempla data e valor. A paginação oferece 25, 50 ou 100 itens e
preserva os filtros.

Os relatórios apresentam saldo anterior, entradas brutas, taxas, entradas
líquidas, saídas, resultado operacional, saldo final e quantidade de movimentos
ativos. As agregações incluem categorias, formas de pagamento e evolução por dia
ou mês. Movimentações canceladas continuam consultáveis no histórico e nunca
participam dos totais.

## Permissões

- `cash.operations.view`: histórico operacional próprio dos últimos sete dias;
- `cash.create`: criar Entrada ou Saída manual;
- `cash.edit`: editar movimento manual dentro do escopo autorizado;
- `cash.cancel`: cancelar movimento manual dentro do escopo autorizado;
- `finance.overview.view`: saldo, indicadores e histórico globais;
- `finance.reports.view`: relatórios financeiros;
- `finance.config.manage`: categorias, formas, terminais e taxas.

O papel `user` recebe por padrão o escopo operacional e criação manual. O
Proprietário recebe as permissões financeiras. As rotas, serviços e consultas
validam o alcance; esconder elementos na interface não é a barreira de segurança.
