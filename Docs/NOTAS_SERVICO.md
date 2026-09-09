# Notas de Serviço — Fase 2/4

Esta é a implementação operacional do
[contrato oficial da Nota de Serviço](CONTRATO_NOTA_SERVICO.md). Ela cobre
registro, consulta, edição autorizada, produção, entrega e cancelamento. Não há
Pagamento real, movimento de Caixa, pagamento parcial ou `CustomerActivity`.

## Tabelas e exatidão

A migration `0008_service_notes` cria:

- `service_notes`, com identificação manual, cliente, datas, estados, desconto,
  entrega, totais em centavos, prazo congelado, autoria e cancelamento;
- `service_note_items`, com referências técnicas e snapshots completos de
  Serviço e Unidade, quantidade escalonada inteira e valores em centavos;
- `service_note_events`, com UID único, tipo, instante, autor e detalhes mínimos.

`number_normalized + series_normalized` é único no SQLite. O número continua
texto e preserva zeros e caixa; somente espaços externos são retirados. Série
ausente vira `""` e a comparação usa `casefold`. Cancelar conserva a chave.

Quantidades não usam `REAL` ou `float`. `quantity_scaled` guarda um inteiro na
escala do snapshot `decimal_places_snapshot`, de 0 a 6. Cálculos monetários usam
`Decimal`, `ROUND_HALF_UP` e `app/core/money.py`; cada subtotal é arredondado
para centavos antes da soma. Os `CHECKs` exigem também `typeof(...)=integer`
para centavos, escala, posição e revisão, pois a afinidade `INTEGER` isolada do
SQLite ainda aceitaria alguns valores reais.

## Cálculo autoritativo

Ao adicionar um item, o servidor lê o Serviço, a unidade ativa e o preço vigente.
O navegador pode mostrar uma prévia exata com `BigInt`, mas não envia nem decide
preço, subtotal ou total.

```text
subtotal dos serviços
- desconto sobre os serviços
+ entrega
= total
```

Os descontos aceitos são `VALOR` e `PERCENTUAL` (até quatro casas no percentual).
Entrega marcada com valor vazio é gratuita. Total zero é válido e grava
`financial_status=PAGO` com motivo `ZERO_TOTAL`, sem Payment ou Caixa. Total
positivo permanece `PENDENTE` nesta fase.

## Estados, prazo e histórico

O único fluxo normal é:

```text
RECEBIDO -> EM_ANDAMENTO -> PRONTO -> ENTREGUE
```

`CANCELADO` é uma ação separada que exige `notes.cancel` e motivo. Toda mutação
envia a `revision` lida no formulário e usa compare-and-swap no banco; uma edição,
mudança de estado ou cancelamento obsoleto recebe conflito `409`. `PRONTO` grava
`ready_at` e `ready_delay_days`; a espera até `ENTREGUE` não aumenta o atraso de
produção.

Eventos `NOTE_CREATED`, `NOTE_UPDATED`, `STATUS_CHANGED` e `NOTE_CANCELLED`
preservam a linha do tempo da Nota. A auditoria geral registra as mesmas
operações sensíveis sem salvar cookies, SQL ou dados de autenticação.

## Edição e segurança

Uma Nota pendente pode ser recalculada no servidor. Itens existentes conservam
seus snapshots; somente quantidade e subtotal mudam. Remover e adicionar outro
item captura o catálogo vigente. Depois de `PAGO`, a rota aceita somente
observações; qualquer campo financeiro ou cadastral adicional é rejeitado.
Nota cancelada é imutável.

As permissões são:

- `notes.view`
- `notes.create`
- `notes.edit`
- `notes.change_status`
- `notes.cancel`

Admin recebe todas. O papel User recebe as quatro primeiras por padrão; cancelar
fica restrito ao Admin. Em instalações existentes, o bootstrap adiciona apenas
os novos códigos previstos para o papel e preserva concessões customizadas.

## Rotas e telas

- `/servicos/nova-nota`: criação;
- `/servicos/notas`: filtros e paginação;
- `/servicos/notas/{id}`: detalhe, snapshots, totais e eventos;
- `/servicos/notas/{id}/editar`: edição permitida;
- POST de `/status` e `/cancelar`: ações explícitas protegidas.

Os filtros incluem busca, cliente, estado operacional, situação financeira,
atrasadas, vence hoje e período de recebimento.

## Migration e recuperação

As migrations são serializadas com `BEGIN IMMEDIATE`, registram a versão apenas
na mesma transação do schema e executam `integrity_check` e `foreign_key_check`
antes e depois. Os DDLs/defaults de `0003`, `0007` e `0008` ficam congelados em
`app/migration_definitions.py`; fases futuras devem adicionar outra versão. A
validação final confere colunas/tipos, PKs, nullability, FKs, `CHECKs`,
unicidades, collations e índices. O rebuild recusa estruturas legadas
desconhecidas em vez de descartá-las silenciosamente.
Antes de migrar uma instalação legada, criar backup consistente pela API do
SQLite e verificar sua restauração. Uma falha deixa a versão sem registro e deve
ser investigada; se o banco não passar nas verificações, manter a operação
fechada e restaurar o backup em local isolado antes de qualquer substituição.
