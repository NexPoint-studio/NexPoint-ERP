# Notas de Serviço

A Nota de Serviço integra Cliente, catálogo, produção, Pagamento e Caixa. Ela
mantém estado operacional e financeiro separados e preserva snapshots completos
dos itens, conforme o [contrato oficial](CONTRATO_NOTA_SERVICO.md).

## Persistência e exatidão

A migration `0008_service_notes` criou:

- `service_notes`: identificação manual, cliente, datas, estados, desconto,
  entrega, totais em centavos, prazo, autoria e cancelamento;
- `service_note_items`: referências técnicas e snapshots de Serviço e Unidade,
  quantidade escalonada inteira e valores em centavos;
- `service_note_events`: UID único, tipo, instante, autor e detalhes mínimos.

A migration `0010_payments` adiciona a entidade de Pagamento vinculada à Nota. A
migration `0011_customer_activity_sources` permite que eventos da Nota alimentem
Clientes com origem idempotente.

A migration `0013_offline_finance_admin` amplia o ledger para pagamentos
parciais, alocações, fechamento e saldos devedores. A migration
`0014_functional_ux_recovery` acrescenta observação ao Pagamento e reforça as
constraints necessárias ao fluxo exposto na interface. Ambas preservam os
registros existentes.

`number_normalized + series_normalized` é único no SQLite. O número continua
texto e preserva zeros e caixa; somente espaços externos são retirados. Série
ausente vira `""` e a comparação usa `casefold`. Cancelar conserva a chave.

Quantidades não usam `REAL` ou `float`. `quantity_scaled` guarda um inteiro na
escala do snapshot `decimal_places_snapshot`, de 0 a 6. Cálculos monetários usam
`Decimal`, `ROUND_HALF_UP` e centavos inteiros; cada subtotal é arredondado antes
da soma. Constraints do banco também verificam os tipos inteiros.

## Cálculo autoritativo e snapshots

Ao adicionar um item, o servidor lê o Serviço, a unidade ativa e o preço vigente.
O navegador apenas apresenta uma prévia; ele não decide preço ou total.

```text
subtotal dos serviços
- desconto sobre os serviços
+ entrega
= total
```

Os descontos aceitos são `VALOR` e `PERCENTUAL`, com até quatro casas no
percentual. Entrega marcada com valor vazio é gratuita.

Cada item congela Serviço, categoria, Unidade de cobrança, comportamento de
quantidade, precisão, quantidade, preço e subtotal. Alterações posteriores no
catálogo, no preço ou na unidade não modificam Notas antigas.

## Estados e prazo

O fluxo operacional é:

```text
RECEBIDO -> EM_ANDAMENTO -> PRONTO -> ENTREGUE
qualquer estado aberto -> FECHADO
```

`CANCELADO` é uma ação separada, exige motivo e `notes.cancel`. Toda mutação envia
a revisão lida no formulário e usa compare-and-swap no banco; uma edição,
mudança de estado ou pagamento com revisão obsoleta recebe conflito.

`PRONTO` grava `ready_at` e congela `ready_delay_days`. A espera pela entrega não
aumenta o atraso de produção. Alertas de vencimento consideram somente Notas em
produção: `PRONTO`, `ENTREGUE` e `CANCELADO` ficam fora das contagens ativas.

O estado financeiro persistido é derivado do ledger: `PENDENTE` quando nada foi
recebido, `PARCIAL` quando o total recebido está entre zero e o total da Nota, e
`PAGO` quando o saldo chega a zero. Na apresentação, uma Nota `FECHADO` que ainda
possui valor em aberto aparece como `SALDO_DEVEDOR`. O usuário não escolhe esses
estados manualmente.

O estado operacional ganhou a ação explícita `FECHADO`. Fechar congela a
operação da Nota e é independente de receber dinheiro; `ENTREGUE` continua
representando o andamento operacional anterior ao fechamento.

## Total zero

Uma Nota de total zero é válida e recebe:

```text
financial_status = PAGO
financial_settlement_reason = ZERO_TOTAL
```

Ela não cria `Payment`, não cria `CashMovement` e não solicita forma de
pagamento. Uma Nota positiva começa `PENDENTE`.

## Pagamentos parciais e múltiplos

Uma Nota positiva aceita vários `Payment` confirmados. Cada recebimento é um
registro imutável próprio; valores como `100 + 50 + 80` não sobrescrevem um
campo acumulado. O servidor usa a forma, o terminal quando necessário, a
modalidade, as parcelas e a data para resolver a taxa vigente. O Pagamento
congela esses dados, os valores bruto, taxa e líquido e a observação opcional.

O valor solicitado precisa ser positivo, monetariamente válido e menor ou igual
ao saldo cobrável. Não são aceitos valor negativo, `NaN`, infinito, precisão
indevida ou recebimento acima do saldo. O ERP não cria crédito ou troco
implícito.

Os detalhes da Nota exibem Total, Total recebido, Saldo restante e situação
financeira derivados, além do histórico individual com data, valor, forma,
origem, status, ator e observação. O evento `PAYMENT_RECEIVED` registra cada
recebimento na linha do tempo.

### Fluxo A — Pagamento inicial

Na criação, a seção **Pagamento** mostra Total, Recebido, Saldo e Situação. A
opção **Registrar pagamento agora** abre valor, forma, data/hora, observação e,
quando aplicável, terminal, modalidade e parcelas. Nota, Pagamento, alocações,
Caixa, auditoria e observabilidade são concluídos na mesma transação; se o
recebimento falhar, a Nota também não permanece salva.

### Fluxo B — Registrar pagamentos depois

Enquanto a Nota está aberta, **Registrar pagamento** apresenta o resumo atual e
aceita recebimentos integrais ou parciais. Cada confirmação atualiza o estado
derivado e cria exatamente um Pagamento e uma movimentação no Caixa. O Caixa
representa dinheiro efetivamente recebido no instante do pagamento, sem esperar
o fechamento.

### Fluxo C — Entregar e receber

Em uma Nota `PRONTO` e `PENDENTE`, a ação entrega e recebe de uma vez. Ao final:

```text
operacional = ENTREGUE
financeiro  = PAGO
1 Payment confirmado para esse recebimento
1 entrada SYSTEM correspondente no Caixa
```

### Fluxo D — Entregar sem receber

O operador pode entregar a Nota mantendo `financial_status=PENDENTE`. Essa ação
não cria Pagamento nem Caixa. O pagamento pode ser registrado depois; somente
então a Nota fica `PAGO` e a entrada financeira é criada.

### Fluxo E — Fechar e quitar depois

**Fechar Nota** apresenta Total, Pago, Saldo e situação antes da confirmação. Se
o saldo for zero, o fechamento apenas muda o estado operacional. Se houver saldo,
ele cria um `CustomerReceivable` vinculado à Nota de origem e o detalhe passa a
mostrar `SALDO_DEVEDOR`. Nenhum dos dois casos cria dinheiro novo no Caixa.

Uma Nota fechada com dívida oferece **Registrar pagamento do saldo**. Cada
quitação parcial cria um `ReceivablePayment` e uma entrada de Caixa; a dívida
permanece `PARTIALLY_PAID` até o restante chegar a zero e então muda para
`SETTLED`. A Nota continua fechada e preserva o retrato do fechamento.

## Transação, retry e concorrência

Criação com pagamento inicial, pagamento posterior, estado financeiro,
entrega opcional, Caixa, eventos e auditoria usam transações SQLite atômicas.
Falha em qualquer etapa desfaz todo o efeito correspondente.

Cada tentativa possui um UUID. Um retry com o mesmo UUID e os mesmos dados
retorna o efeito existente sem duplicar o recebimento; o mesmo UUID com dados
diferentes gera conflito. As chaves únicas de Pagamento e origem no Caixa, a
revisão da Nota e a transação `BEGIN IMMEDIATE` também protegem duplo clique e
requisições concorrentes. O fechamento usa outra chave idempotente: repeti-lo
não cria novo fechamento, saldo ou Caixa.

A entrada financeira usa `origin=SYSTEM`, `source_type=PAYMENT` e o ID imutável
do Pagamento. Ela não pode ser editada ou cancelada isoladamente pelo Caixa. Veja
[Administração e Pagamentos](PAGAMENTOS_ADMINISTRACAO.md) para o contrato
financeiro completo.

## Integração com Clientes

Criar uma Nota registra `SERVICE_CREATED` no histórico do Cliente, na mesma
transação e com a data do recebimento. Marcar a Nota como `PRONTO` registra
`SERVICE_COMPLETED`.

Somente `VISIT` e `SERVICE_CREATED` contam como último retorno. Concluir a
produção não faz o Cliente parecer ter voltado dias depois. O perfil obtém o
último serviço dos snapshots da Nota e os eventos automáticos usam origem única
para impedir duplicidade.

## Edição e segurança

Uma Nota aberta e ainda não quitada pode ser recalculada no servidor. Itens
mantidos conservam os snapshots; somente quantidade e subtotal mudam. Remover e
adicionar um item captura o catálogo vigente. Pagamentos existentes permanecem
no ledger: aumentar o total apenas aumenta o saldo; reduzir o total abaixo do
valor já recebido é rejeitado com uma mensagem explícita. Depois de `PAGO`, a
rota aceita apenas observações e rejeita mudanças financeiras ou cadastrais.
Notas fechadas e canceladas são imutáveis no backend, mesmo que alguém tente um
POST direto.

As permissões são:

- `notes.view`;
- `notes.create`;
- `notes.edit`;
- `notes.change_status`;
- `notes.cancel`;
- `payments.receive`.

O Proprietário recebe todas. O papel `user` recebe consulta, criação, edição,
mudança operacional e recebimento por padrão; cancelar permanece restrito ao
Proprietário. As rotas validam as permissões no backend.

## Rotas e telas

- `/servicos/nova-nota`: criação;
- `/servicos/notas`: busca, filtros, paginação e linhas inteiras clicáveis com
  badges de Não pago, Parcialmente pago, Pago ou Saldo devedor;
- `/servicos/notas/{id}`: detalhe completo, snapshots, resumo financeiro,
  histórico de pagamentos, fechamento e quitação de dívida;
- `/servicos/notas/{id}/editar`: edição autorizada;
- `/servicos/notas/{id}/pagamento`: registrar cada pagamento ou entregar e
  receber;
- POST `/servicos/notas/{id}/fechar`: fechamento idempotente;
- POST `/clientes/{cliente_id}/debitos/{debito_id}/pagar`: quitação parcial ou
  total do saldo, com retorno ao detalhe da Nota quando iniciado por ele;
- POST de `/status` e `/cancelar`: ações explícitas protegidas.

Os filtros incluem busca, cliente, estado operacional, situação financeira,
atrasadas, vence hoje e período de recebimento. Os cartões da Administração
abrem essa lista já filtrada.

## Migrations e recuperação

As migrations são serializadas com `BEGIN IMMEDIATE`, registram a versão na
mesma transação do schema e executam `integrity_check` e `foreign_key_check`. A
validação confere colunas, tipos, chaves, constraints, unicidades e índices.

Antes de migrar uma instalação legada, deve-se criar backup consistente pela API
do SQLite e verificar sua restauração. Uma falha não pode registrar a versão nem
recriar o banco para facilitar o desenvolvimento. Nenhum histórico anterior é
convertido artificialmente em Pagamento.
