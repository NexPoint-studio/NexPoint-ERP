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
```

`CANCELADO` é uma ação separada, exige motivo e `notes.cancel`. Toda mutação envia
a revisão lida no formulário e usa compare-and-swap no banco; uma edição,
mudança de estado ou pagamento com revisão obsoleta recebe conflito.

`PRONTO` grava `ready_at` e congela `ready_delay_days`. A espera pela entrega não
aumenta o atraso de produção. Alertas de vencimento consideram somente Notas em
produção: `PRONTO`, `ENTREGUE` e `CANCELADO` ficam fora das contagens ativas.

O estado financeiro é `PENDENTE` ou `PAGO` e não muda automaticamente com o
estado operacional, exceto pelas ações explícitas de recebimento e pela regra de
total zero.

## Total zero

Uma Nota de total zero é válida e recebe:

```text
financial_status = PAGO
financial_settlement_reason = ZERO_TOTAL
```

Ela não cria `Payment`, não cria `CashMovement` e não solicita forma de
pagamento. Uma Nota positiva começa `PENDENTE`.

## Pagamento integral

Uma Nota positiva possui no máximo um `Payment` confirmado. Não há pagamento
parcial. O servidor usa a forma, o terminal quando necessário, a modalidade, as
parcelas e a data do recebimento para resolver a taxa vigente. O Pagamento
congela todos esses dados e os valores bruto, taxa e líquido.

Os detalhes do Pagamento aparecem no detalhe da Nota. O evento
`PAYMENT_RECEIVED` registra o recebimento na linha do tempo.

### Fluxo A — Registrar pagamento

O operador pode receber antes de concluir a produção. A Nota fica financeiramente
`PAGO`, o `Payment` e a entrada automática no Caixa são criados e o estado
operacional permanece onde estava.

### Fluxo B — Entregar e receber

Em uma Nota `PRONTO` e `PENDENTE`, a ação entrega e recebe de uma vez. Ao final:

```text
operacional = ENTREGUE
financeiro  = PAGO
1 Payment confirmado
1 entrada SYSTEM no Caixa
```

### Fluxo C — Entregar sem receber

O operador pode entregar a Nota mantendo `financial_status=PENDENTE`. Essa ação
não cria Pagamento nem Caixa. O pagamento pode ser registrado depois; somente
então a Nota fica `PAGO` e a entrada financeira é criada.

## Transação, retry e concorrência

Pagamento, estado financeiro, entrega opcional, Caixa, eventos e auditoria usam
uma única transação SQLite. Falha em qualquer etapa desfaz toda a operação.

Cada tentativa possui um UUID. Um retry com o mesmo UUID e os mesmos dados não
duplica o recebimento; o mesmo UUID com dados diferentes gera conflito. O índice
único parcial de Pagamentos confirmados, a origem única no Caixa e a revisão da
Nota também protegem duas requisições concorrentes.

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

Uma Nota pendente pode ser recalculada no servidor. Itens mantidos conservam os
snapshots; somente quantidade e subtotal mudam. Remover e adicionar um item
captura o catálogo vigente. Depois de `PAGO`, a rota aceita apenas observações e
rejeita mudanças financeiras ou cadastrais. Nota cancelada é imutável.

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
- `/servicos/notas`: busca, filtros e paginação;
- `/servicos/notas/{id}`: detalhe, snapshots, Pagamento, totais e eventos;
- `/servicos/notas/{id}/editar`: edição autorizada;
- `/servicos/notas/{id}/pagamento`: registrar pagamento ou entregar e receber;
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
