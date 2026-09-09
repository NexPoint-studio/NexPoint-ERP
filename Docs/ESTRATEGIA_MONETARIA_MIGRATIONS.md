# Estratégia monetária e migrations — Fase 1/4

Este documento complementa o [contrato oficial da Nota de Serviço](CONTRATO_NOTA_SERVICO.md).
Vale para o projeto `D:\NexStudio\sistema ERP`, repositório `SmuriNex/sistema-ERP`.
As exigências abaixo orientam as Fases 2 e 3. A Fase 1 não cria tabelas de
Nota/Pagamento, não migra o Caixa e não modifica valores operacionais.

> Estado atual: a Fase 2 aplicou esta estratégia às Notas, com dinheiro em
> centavos inteiros e quantidade inteira escalonada de 0 a 6 casas. As migrations
> `0007_billing_units` e `0008_service_notes` não convertem o Caixa legado.

## 1. Decisão monetária

Novos campos monetários de Nota, itens, descontos, entrega e Pagamento serão
persistidos em **centavos inteiros**, em colunas SQLite `INTEGER`, com nomes
explícitos como `total_cents`. Nas regras de negócio, usar `Decimal`, criado
de texto ou inteiro. Nunca converter entrada financeira para `float`, fazer
cálculos com `float` ou construir `Decimal` a partir de `float`.

| Valor em reais | Centavos persistidos |
|---|---:|
| `Decimal("0.00")` | `0` |
| `Decimal("10.00")` | `1000` |
| `Decimal("97.35")` | `9735` |

A unidade persistida deve fazer parte do contrato de cada campo e resposta.
Não misturar reais e centavos em uma coluna ou converter duas vezes o mesmo
valor. Um inteiro vindo do banco representa centavos; um `Decimal` usado nas
regras monetárias representa reais.

Os limites técnicos são `-9223372036854775808` a `9223372036854775807` centavos,
o intervalo de inteiro assinado de 64 bits do SQLite. Eles **não são limites
de produto**: cada domínio definirá seus tetos e suas constraints antes da
implementação, inclusive para totais e agregações. O utilitário suporta o
intervalo inteiro; não se deve permitir que a soma de parcelas estoure o
intervalo da coluna ou de uma agregação SQL.

Valores de item, preço, entrega, desconto e total da Nota não podem ser
negativos. Quantidades são positivas; uma Nota de total zero é válida.
O conversor aceita valores negativos para operações matemáticas e saldos;
a validação e as constraints de cada domínio restringem o sinal adequado.
O sinal nunca substitui o tipo de movimento do Caixa.

## 2. Utilitário central implementado nesta fase

Arquivo: [`app/core/money.py`](../app/core/money.py).

| API | Entrada | Saída e regra |
|---|---|---|
| `decimal_to_cents(amount)` | `Decimal` finito | `int`; arredondamento explícito `ROUND_HALF_UP` para duas casas; valida resultado int64 |
| `cents_to_decimal(cents)` | `int`, exceto `bool` | `Decimal` exato com duas casas; valida entrada int64 |

O utilitário não interpreta moeda formatada, vírgulas ou formulários. Essas
entradas pertencem aos validadores, que deverão produzir um `Decimal` finito.
`float`, `bool`, texto e coerções implícitas não são aceitos nas APIs.
Tipos incorretos produzem `TypeError`; `NaN` e infinitos produzem `ValueError`;
valores fora de int64 produzem `OverflowError`.

Na conversão para centavos, o limite se aplica ao **resultado arredondado**:
`1.005` vira `101`; `-1.005` vira `-101`. Empates afastam-se de zero.
`92233720368547758.074` ainda cabe; `92233720368547758.075` ultrapassa o
limite depois do arredondamento e é rejeitado. O limite negativo é assimétrico,
conforme int64. Zero com sinal negativo resulta em zero inteiro.

A conversão usa contexto decimal próprio, com precisão e arredondamento
explícitos. A conversão inversa constrói o Decimal exatamente, sem divisão
sujeita ao contexto global. Mudanças externas de precisão, arredondamento,
traps e limites de expoente não alteram o resultado. Expoentes muito grandes
são rejeitados antes de expandir um inteiro; valores muito pequenos podem
arredondar legitimamente para zero.

Os testes em [`tests/test_money.py`](../tests/test_money.py) cobrem ida/volta,
centavos, valores negativos, arredondamento, limites de int64, limites dos
módulos legados, entradas inválidas, contexto global alterado e expoentes
extremos. São testes puros, sem acesso a banco.

## 3. Cálculos e valores que não são dinheiro

As futuras regras devem seguir o contrato da Nota:

1. Calcular cada item com preço utilizado e quantidade em `Decimal`.
2. Arredondar o subtotal de cada item para centavos com `ROUND_HALF_UP` e
   preservá-lo no snapshot; somar esses subtotais para o subtotal dos serviços.
3. Aplicar o desconto somente sobre serviços. No percentual, conservar o
   percentual original, a base e o desconto efetivo arredondado em centavos.
4. Impedir desconto superior ao subtotal e então adicionar a entrega.
5. Persistir o resultado e os componentes; não recalcular Notas antigas
   por alterações do catálogo ou de regras futuras.

O contexto explícito do conversor protege a **conversão**. Ele não recupera
precisão perdida em uma multiplicação anterior. Os serviços de cálculo
deverão definir contexto local adequado aos limites de preços, quantidades,
percentuais e número de itens, com testes das fronteiras e sem depender da
precisão global. Evitar arredondamentos intermediários adicionais.

Quantidade e percentual não representam dinheiro e não devem passar pelo
conversor de centavos. `UNIT` e `PAIR` exigem inteiro positivo; `FIXED` exige
`1`; `KG` e `METER` aceitam decimal positivo. Desconto percentual deverá
ficar entre zero e cem, sem tornar os serviços negativos.

A precisão máxima de quantidades decimais e percentuais ainda deverá ser
definida antes das tabelas e dos validadores da Fase 2. Não impor agora uma
escala arbitrária. Depois dessa decisão, escolher armazenamento exato como
inteiro escalonado com escala documentada ou texto decimal canônico validado.
Não usar `REAL` nem ampliar a dependência de `Numeric`/SQLite nesses campos.

Nota com total zero fica financeiramente `PAGO`, sem exigir forma de pagamento
e sem gerar entrada financeira. Essa quitação também bloqueia edição dos
campos financeiros, conforme o contrato. Não criar pagamento fictício de
valor zero para produzir um movimento no Caixa.

## 4. Compatibilidade com o legado na Fase 3

O estado atual permanece intacto na Fase 1:

- `CashMovement.gross_amount`, `fee_amount` e `net_amount` usam `Numeric(14, 2)`.
- O validador do Caixa aceita até `Decimal("999999999999.99")` por valor.
- `ServicePrice.amount` usa `Numeric(12, 2)` e seu validador aceita até
  `Decimal("9999999999.99")`.
- Os serviços trabalham com `Decimal`, mas o adaptador atual pode passar por
  conversão binária na persistência SQLite. O utilitário novo não remove essa
  característica do legado e não é integrado a essas tabelas nesta fase.

Antes da integração financeira, a Fase 3 deverá implementar e testar uma
fronteira explícita entre Pagamento em centavos e Caixa legado:

1. Nota/Pagamento manterão seus snapshots monetários em inteiros, como fonte
   dos valores da operação nova.
2. Ao preparar o lançamento legado, converter centavos para `Decimal` pela API
   central; não usar `float`, divisão binária ou SQL `REAL` para conversão.
3. Validar os limites do Caixa antes de confirmar a operação. Valores novos
   integrados devem respeitar o teto legado enquanto essa fronteira existir.
   Não reduzir a capacidade dos lançamentos manuais já suportados, truncar
   valores ou aceitar uma Nota pagável sem uma estratégia válida para o Caixa.
4. Após o `flush`, reler os valores persistidos e comparar seus centavos com
   bruto, taxa e líquido esperados. Diferença implica rollback integral,
   sem confirmação parcial ou correção silenciosa. Testar especificamente
   os maiores valores aceitos pelo legado.
5. Pagamento, origem única no Caixa, situação financeira, eventual entrega e
   auditoria deverão compartilhar uma única transação. Os commits internos
   atuais dos serviços precisarão de adaptação delimitada antes da integração:
   chamar serviços que já confirmaram operações separadas não garante atomicidade.
6. Usar ID interno do Pagamento para vínculo e idempotência; impedir duplicação
   por reenvio/concorrência e edição avulsa que rompa a correspondência financeira.

Essa ponte verifica a correspondência **em centavos**; ela não torna a
representação interna de `Numeric`/SQLite uma persistência inteira exata.
Não habilitar a integração enquanto reconciliação e transação não estiverem
validadas. Se a ponte não atender aos testes, tratar a migração do Caixa como
pré-requisito explícito daquela etapa.

Uma eventual migração do legado para centavos será uma tarefa identificada,
com backup validado e ensaio isolado. Deverá preservar IDs, referências,
categorias, formas, datas, origens, status/cancelamentos e auditoria, comparar
bruto/taxa/líquido por registro e reconciliar saldos e relatórios antes/depois.
Não presumir que os bytes históricos permitem reconstruir precisão já perdida;
qualquer divergência deve impedir a migração até ser analisada.

**Lançamentos manuais antigos continuam manuais.** Não inferir pagamentos,
Notas, clientes ou vínculos retroativos a partir de descrição, valor ou data.

## 5. Padrão obrigatório para migrations futuras

O mecanismo atual em [`app/migrations.py`](../app/migrations.py) registra
versões em `schema_migrations`. A Fase 2 acrescenta `0007_billing_units` e
`0008_service_notes`, com DDL/defaults versionados em
[`app/migration_definitions.py`](../app/migration_definitions.py). `0007`
reconstrói `services` de forma explícita para trocar o código textual por FK;
`0008` cria Notas, itens e eventos. As definições já versionadas não dependem do
model ou de defaults mutáveis.

Cada mudança futura deverá atender a estes requisitos:

| Requisito | Padrão de implementação e evidência |
|---|---|
| Identificação | ID único e ordenado, descrição, versão de origem/destino e operações explícitas; versões aplicadas não são reescritas |
| Uma execução | Consultar `schema_migrations`; aplicar apenas versões pendentes; registrar conclusão somente junto ao sucesso da mudança |
| Concorrência | Coordenar exclusividade de atualização; duas inicializações não podem disputar alteração de schema ou confirmar metade de uma versão |
| Transação | Alteração e registro da versão na mesma conexão/transação quando suportado; funções internas não fazem commits independentes |
| Backup | Antes de alteração sensível, backup consistente e verificável, fora do Git; testar restauração em local isolado |
| Transformação | SQL/etapas explícitos e mapeamento de dados; se reconstruir tabela, preservar IDs, FKs, índices, checks e demais constraints |
| Integridade | Exigir `PRAGMA integrity_check` com resultado `ok` e `PRAGMA foreign_key_check` sem linhas antes/depois |
| Preservação | Comparar registros, referências, valores, estados e históricos; contagem de linhas sozinha não basta |
| Recuperação | Documentar falhas esperadas, rollback, retomada ou restauração, versão compatível do aplicativo e critério para reabrir operação |

No SQLite, a evidência de atomicidade deve vir também do teste de falha do
driver/configuração realmente utilizados. Não presumir que o bloco
`engine.begin()` cobre automaticamente todo DDL: quando necessário, garantir
o início explícito da transação e comprovar o comportamento antes do uso
operacional. Etapas que não puderem compartilhar transação precisam de plano
de recuperação testado e não podem deixar uma versão marcada como concluída
com schema/dados incompletos.

Backup de SQLite em uso deve utilizar mecanismo consistente (por exemplo,
API de backup do SQLite); copiar somente o arquivo principal enquanto há WAL
ou gravações ativas não é uma estratégia suficiente. Registrar resultado e
local protegido do backup sem expor dados comerciais ou credenciais em logs.
Uma restauração não deve sobrescrever automaticamente um banco em uso.

Ao criar modelos de domínio na Fase 2/3, incluí-los em `domain_tables` no
[`bootstrap`](../app/services/bootstrap.py). Caso contrário, o `create_all`
de infraestrutura poderá criar tabelas fora da migration planejada. Registrar
os modelos antes da resolução das tabelas e conferir ordem de dependências/FKs.
Migrations não devem depender de regras de negócio mutáveis para converter
dados históricos nem refazer seed por conveniência.

## 6. Matriz mínima de validação de cada migration

Todos os ensaios destrutivos usam bancos temporários. Um legado representativo
deve ser sintético ou uma cópia isolada protegida, nunca o arquivo operacional.

| Cenário | Verificação obrigatória |
|---|---|
| Banco novo | Aplicar a cadeia completa; estrutura, versões, índices, constraints e bootstrap corretos |
| Banco legado | Migrar fixtures de versões anteriores com dados relacionados, inativos, cancelamentos e valores nas fronteiras |
| Reexecução | Rodar novamente sem duplicar versões, dados, eventos ou configuração e sem alterar valores |
| Falha intermediária | Injetar falha após criação/cópia/atualização e antes do registro da versão; comprovar rollback ou recuperação documentada |
| Integridade/FKs | Executar verificações antes/depois; tentar vínculos e duplicidades inválidos em banco de teste |
| Preservação | Conferir conteúdo e relações, permissões customizadas, históricos, preços e valores monetários reconciliados |
| Backup/recovery | Restaurar isoladamente e comprovar integridade, versão e dados; validar retomada após falha sem duplicação |

Para uma migration monetária, acrescentar reconciliação por registro e por
período, arredondamento documentado, valores próximos aos limites e comparação
dos resultados dos relatórios. Falha de reconciliação bloqueia conclusão.

O hash do arquivo ajuda a provar ausência de gravação em tarefas sem migration,
como esta Fase 1. Em migrations legítimas, o arquivo muda: a evidência de
preservação deve ser lógica e financeira, além da integridade estrutural.

## 7. Limite da Fase 2

As novas Notas persistem dinheiro em centavos inteiros e snapshots da unidade e
do preço do catálogo. O `Numeric(12,2)` já existente em `service_prices` e toda a
persistência monetária do Caixa permanecem sem conversão. Esta fase não cria
Payment, não vincula Nota ao Caixa, não infere recebimentos antigos e não produz
`CustomerActivity`; essas integrações continuam reservadas às fases seguintes.
