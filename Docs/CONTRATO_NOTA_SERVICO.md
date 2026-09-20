# Contrato oficial da Nota de Serviço — versão 1

Consolidado na Fase 1/4 — Fundação, correções e contratos.

> Estado atual: implementado na Fase 2/4 conforme
> [Notas de Serviço](NOTAS_SERVICO.md). As menções abaixo a “futuro” registram o
> momento em que o contrato foi congelado; as decisões continuam normativas.

Este documento registra as decisões aprovadas para a primeira versão da Nota de
Serviço. É a referência para as fases seguintes e prevalece sobre propostas da
análise anterior e referências genéricas a Atendimento/OS na documentação antiga.
Não constitui implementação de Nota, Pagamento, telas, tabelas ou integração.

A arquitetura comercial permanece local: FastAPI, SQLAlchemy, SQLite, Jinja2 e
pywebview. A estratégia de valores e de evolução do banco está em
[Estratégia monetária e migrations](ESTRATEGIA_MONETARIA_MIGRATIONS.md).

## 1. Identificação e unicidade

A Nota possui um ID técnico interno e um número manual obrigatório, armazenado
como texto. O operador informa o número do papel; não há geração automática do
número comercial. Relacionamentos utilizam exclusivamente o ID técnico.

São válidos, por exemplo, `187`, `0187` e `A187`. Zeros iniciais e a representação
textual digitada devem ser preservados. `187` e `0187` são números diferentes.
Bloco/série é opcional e também textual.

Separar a representação de apresentação das chaves usadas na comparação. O
contrato de normalização é:

```text
numero_normalizado = numero_digitado.strip()
serie_normalizada = (serie_digitada ou "").strip().casefold()
```

- Número ausente ou formado somente por espaços é inválido.
- Remover somente espaços externos para comparação; não remover zeros nem
  espaços internos e não converter o número para inteiro.
- Apenas a série tem comparação sem distinção de maiúsculas/minúsculas.
  Não aplicar conversão de caixa ao número: `A187` e `a187` permanecem distintos.
- Série ausente, vazia ou formada somente por espaços tem a mesma chave `""`.
- Não introduzir outra normalização que torne números distintos equivalentes.
- A combinação de número normalizado e série normalizada é única por instalação.
- Não há reinício anual de numeração nesta versão.
- Cancelamento não libera o número.

| Número / série já cadastrados | Nova tentativa | Resultado esperado |
|---|---|---|
| `187` / ausente | `0187` / ausente | Permitido: números diferentes |
| `187` / `A` | `187` / `B` | Permitido: séries diferentes |
| `187` / `A` | ` 187 ` / ` a ` | Duplicidade |
| `187` / ausente | `187` / vazia ou espaços | Duplicidade |
| `A187` / ausente | `a187` / ausente | Permitido: número preserva caixa |
| `187` / ausente, Nota cancelada | `187` / ausente | Duplicidade |
| `187` / ausente, ano anterior | `187` / ausente | Duplicidade |

Na tabela implementada, a chave normalizada da série é não nula, com vazio
representado por string vazia. Uma constraint composta com série nullable não
cumpre sozinha este contrato. A unicidade deve existir no banco e tratar duas
criações concorrentes com erro de domínio compreensível.

## 2. Cliente

Toda Nota referencia um cliente existente por ID interno. Nome ou razão social
continua essencial no cadastro de Clientes. CPF/CNPJ, e-mail, endereço, telefone
e WhatsApp permanecem opcionais. A Nota não pode exigir esses campos como condição
indireta para cadastrar ou selecionar o cliente.

Preservar os clientes e atividades existentes. A futura relação deve impedir
exclusão que deixe uma Nota órfã; inativar cadastro não apaga documentos antigos.

## 3. Itens e snapshots

Uma Nota suporta vários serviços. Cada item deverá guardar:

- `service_id` original, como referência técnica;
- código e nome do serviço em snapshot;
- descrição relevante em snapshot, quando aplicável;
- unidade de cobrança em snapshot;
- quantidade;
- preço unitário efetivamente utilizado;
- subtotal calculado daquele item.

Snapshots são os dados históricos do atendimento. Alterar nome, categoria,
unidade, preço ou status no catálogo não modifica uma Nota já registrada.
O histórico de preços do catálogo pode indicar a origem do preço, mas não
substitui esses snapshots. Consultas de Notas antigas usam os dados congelados;
não recalculam itens a partir do preço ou da unidade atual do serviço.

Uma correção explicitamente autorizada de Nota ainda pendente é uma operação
sobre a própria Nota, sujeita às regras de edição. Não é sincronização automática
com o catálogo. Após quitação, aplicam-se os bloqueios da seção 9.

## 4. Quantidades

| Unidade | Regra obrigatória de servidor |
|---|---|
| `UNIT` | Inteiro positivo |
| `PAIR` | Inteiro positivo |
| `KG` | Decimal positivo |
| `METER` | Decimal positivo |
| `FIXED` | Sempre igual a 1 |

Zero, negativos e números não finitos não são quantidades válidas. Quantidades
decimais não devem passar por `float`. A interface auxilia o preenchimento,
mas o backend é a autoridade final, inclusive para POST feito manualmente.

Precisão máxima de KG/METER e limites operacionais de quantidade deverão ser
explicitados antes da persistência da Fase 2. Essa definição técnica não permite
aceitar frações em UNIT/PAIR nem quantidade diferente de 1 em FIXED.

## 5. Entrega, desconto e total

A indicação de entrega é independente do preço:

- Sem entrega: valor de entrega igual a zero.
- Com entrega: valor opcional; vazio significa `R$ 0,00`.
- Entrega gratuita é válida. Valor negativo não é válido.

Tipos de desconto: `VALOR` e `PERCENTUAL`. O desconto incide somente sobre o
subtotal dos serviços; a entrega é adicionada depois.

```text
subtotal_item = arredondar_centavos(quantidade × preco_unitario_snapshot)
subtotal_servicos = soma dos subtotais dos itens
desconto_efetivo = desconto em valor ou percentual sobre subtotal_servicos
total_final = subtotal_servicos - desconto_efetivo + valor_entrega
```

Adotar `Decimal` nos cálculos e `ROUND_HALF_UP` na conversão para centavos.
Arredondar cada subtotal de item para centavos antes da soma. No percentual,
calcular sobre o subtotal dos serviços já consolidado e arredondar o desconto
uma única vez para centavos. Somar os componentes finais em centavos exatos.

O percentual deve estar entre 0 e 100; o desconto em valor deve estar entre zero
e o subtotal dos serviços. Validar os limites do desconto informado antes do
arredondamento, sem aceitar excesso que apenas arredondaria para dentro do limite.
O desconto não pode tornar o subtotal dos serviços negativo.

Guardar o tipo do desconto, o percentual original quando utilizado,
a base aplicada e o valor efetivamente descontado. Congelar também subtotal dos
serviços, valor de entrega e total final. A forma de representação exata do
percentual será definida com a precisão dos campos; não usar ponto flutuante.

| Serviços | Desconto | Entrega | Total |
|---:|---:|---:|---:|
| R$ 100,00 | R$ 10,00 | R$ 15,00 | R$ 105,00 |
| R$ 100,00 | 10% dos serviços | R$ 15,00 | R$ 105,00 |
| R$ 50,00 | R$ 50,00 | R$ 0,00 | R$ 0,00 |
| R$ 50,00 | R$ 50,00 | R$ 15,00 | R$ 15,00 |

### Total zero

Nota com total final zero é permitida e fica financeiramente quitada:
`status_financeiro = PAGO`. Não exigir forma de pagamento, não criar pagamento
fictício e não gerar movimento de Caixa. Registrar a razão de quitação sem
recebimento para distingui-la de pagamento efetivo e preservar a auditoria.

O estado operacional continua independente: valor zero não torna o serviço
automaticamente PRONTO ou ENTREGUE. Os bloqueios financeiros de Nota paga
também se aplicam à Nota quitada por total zero.

## 6. Estados operacionais

Estes são os únicos códigos operacionais da primeira versão:

| Código | Semântica |
|---|---|
| `RECEBIDO` | Nota criada e serviço recebido |
| `EM_ANDAMENTO` | Produção iniciada |
| `PRONTO` | Produção concluída, aguardando entrega ou retirada |
| `ENTREGUE` | Atendimento operacional encerrado |
| `CANCELADO` | Nota anulada operacionalmente |

Não criar `CONCLUIDO` como código adicional. Na linguagem da interface,
concluir/entregar o atendimento corresponde a ENTREGUE; terminar a produção
corresponde a PRONTO. As ações e mensagens devem manter essa distinção.

As transições deverão registrar autor e instante. Não inferir pagamento de uma
transição operacional, nem produzir transição operacional apenas por pagamento.
Reabertura, reversão de produção e outros fluxos não especificados não estão
autorizados por este contrato; não devem surgir implicitamente na implementação.

## 7. Recebimento e prazo de produção

A Nota guarda data/hora de recebimento e previsão de conclusão. A previsão é a
data em que o serviço deve estar PRONTO, não a data de entrega ao cliente.

Usar a data local da empresa para comparação. Guardar o instante em que a Nota
entrou em PRONTO para congelar o atraso de produção, com conversão de UTC para
o fuso da empresa antes de obter a data local.

Para uma Nota em produção, comparar a previsão com a data local atual. Depois
de PRONTO, comparar com a data local do evento PRONTO. ENTREGUE conserva essa
mesma referência de produção; a espera por retirada não aumenta o atraso.

```text
data_referencia = data local de PRONTO, se produção encerrada;
                 data local atual, enquanto em produção

data_referencia < previsao  => DENTRO_DO_PRAZO
data_referencia = previsao  => VENCE_HOJE
data_referencia > previsao  => ATRASADO
dias_atraso = max(0, data_referencia - previsao), em dias de calendário
```

Depois de PRONTO/ENTREGUE, essa comparação é histórica. A interface deverá
explicar que o serviço ficou pronto na data prevista ou com atraso, evitando
mostrar um alerta atual de "vence hoje" para produção já encerrada.

Exemplo: previsão dia 10, PRONTO dia 12, ENTREGUE dia 15. O atraso de produção
permanece 2 dias, inclusive depois da entrega. Nota CANCELADA permanece no
histórico e não deve ser apresentada como produção pendente em alertas ativos;
cancelamento não fabrica um evento PRONTO nem apaga o atraso já registrado.

## 8. Financeiro e os fluxos de recebimento

Os estados financeiros persistidos são `PENDENTE`, `PARCIAL` e `PAGO`.
Não usar `PAGA` como código alternativo. `SALDO_DEVEDOR` é o estado derivado de
apresentação para Nota fechada com valor em aberto. Todos são calculados a
partir dos pagamentos reais; o operador não escolhe a situação financeira.

São combinações válidas, entre outras:

- `EM_ANDAMENTO + PAGO`;
- `EM_ANDAMENTO + PARCIAL`;
- `PRONTO + PENDENTE`;
- `ENTREGUE + PENDENTE`;
- `ENTREGUE + PAGO`;
- `FECHADO + SALDO_DEVEDOR`.

| Fluxo | Operacional | Financeiro | Caixa |
|---|---|---|---|
| Já pago / receber antecipadamente | Mantém estado operacional | Pagamento confirmado torna PAGO | Uma entrada ligada ao pagamento |
| Receber parcialmente | Mantém estado operacional | PENDENTE passa a PARCIAL | Uma entrada por recebimento |
| Concluir/entregar e receber | Passa a ENTREGUE | Confirma pagamento e torna PAGO | Entrada na mesma transação |
| Concluir/entregar sem receber | Passa a ENTREGUE | Permanece PENDENTE | Nenhuma entrada |
| Receber depois | Mantém atendimento encerrado | Estado deriva do novo total pago | Uma entrada ligada ao pagamento |
| Fechar com saldo | Passa a FECHADO | Apresenta SALDO_DEVEDOR | Nenhuma entrada adicional |
| Quitar dívida fechada | Permanece FECHADO | Saldo diminui até PAGO | Uma entrada por valor recebido |

A exceção de total zero segue a seção 5, sem pagamento nem entrada.

Na integração implementada, confirmar pagamento, gerar movimento, registrar
auditoria e atualizar situação financeira constitui uma única transação. Em
entregar e receber, a transição operacional participa da mesma unidade de trabalho.
Falha intermediária não pode deixar pagamento confirmado sem Caixa ou entrega
confirmada parcialmente nessa ação composta.

Idempotência impede duplicidade por repetição de requisição ou concorrência.
A origem do movimento usa o ID técnico do pagamento, com referência à Nota;
o número manual é somente apresentação. Usar unicidade persistida, não apenas
bloqueio de duplo clique na interface. Esta versão admite um pagamento integral
ou vários pagamentos parciais por Nota; cada UUID representa um recebimento
imutável e possui exatamente uma origem de Caixa. O histórico não é apagado.

Taxa de recebimento não é dívida do cliente: Nota de R$ 100,00 com pagamento
bruto de R$ 100,00 e taxa de R$ 3,00 fica PAGO; o Caixa recebe R$ 97,00 líquidos.
Pagamentos e taxas antigos também conservam os valores utilizados no momento.

## 9. Edição depois de receber

Enquanto a Nota estiver aberta e não totalmente paga, uma edição pode aumentar
ou reduzir o total, mas nunca abaixo da soma já recebida. Os Payments existentes
permanecem no ledger e o saldo é recalculado; não existe estorno automático.

Após ficar PAGO, bloquear alteração de qualquer campo que modifique valores:
serviços, quantidade, preço, entrega, desconto, subtotal e total. O servidor
deverá bloquear a alteração mesmo com campos forjados no POST.

Somente campos não financeiros seguros, como observações e informações
administrativas sem impacto financeiro, poderão ser editados conforme permissão
e auditoria. Correções financeiras precisarão de fluxo próprio futuro; não
reabrir edição de valores nem recalcular documentos pagos como atalho.

## 10. Cancelamento e preservação

Cancelar a Nota e devolver dinheiro são operações distintas. A Nota cancelada:

- permanece no banco, nas consultas históricas e na auditoria;
- continua reservando número e série;
- conserva itens, snapshots, eventos e referências financeiras;
- não apaga pagamento nem produz devolução financeira implícita.

Para uma Nota já paga, a correção financeira depende de fluxo específico futuro.
Não improvisar estorno usando o cancelamento atual do Caixa, nem cancelar uma
entrada e lançar saída como se fossem uma única correção sem regra definida. A
Fase 2 implementa o cancelamento operacional com histórico; estorno financeiro
continua fora do escopo.

## 11. Integração com Clientes e responsabilidades

Os eventos `SERVICE_CREATED` e `SERVICE_COMPLETED` da Nota alimentam as
atividades do cliente. Eles possuem referência técnica à Nota, proteção de
duplicidade, participam da mesma transação da operação e preservam visitas e
atividades anteriores. Somente visita e criação do serviço contam como retorno;
a conclusão posterior da produção não altera artificialmente essa data.
Cadastro ou edição de item do catálogo não representa atendimento ao cliente.

Permissões devem ser verificadas no servidor. A existência de botão oculto,
senha adicional ou status na interface não substitui autorização de ação.

No módulo Clientes corrigido nesta fase, edição cadastral não altera status.
Inativação/reativação de cliente existente são ações separadas, protegidas por
`customers.deactivate`, e conservam histórico.

## 12. Verificação obrigatória nas fases de implementação

- Numeração manual, preservação textual, série vazia, duplicidade concorrente,
  cancelados e ausência de reinício anual.
- Seleção de cliente sem tornar contatos/documentos obrigatórios.
- Snapshot após alterações de nome, preço, categoria, unidade e status.
- Quantidades válidas e inválidas para cada unidade, inclusive POST forjado.
- Entrega gratuita/vazia, desconto em valor/percentual, base sem entrega,
  arredondamento, desconto acima da base e total zero sem Caixa.
- Prazo em data local, fronteira da meia-noite e atraso congelado em PRONTO.
- Independência dos estados, três fluxos, recebimento posterior e total zero.
- Transação única, falhas intermediárias, reenvio e concorrência de pagamento.
- Bloqueio financeiro após PAGO, permissões e histórico preservado no cancelamento.
- Banco novo/legado, migrations repetidas, falha e recovery conforme estratégia.

As fases seguintes deverão explicitar limites de entrada, precisão de
quantidade/percentual e matriz detalhada de ações antes de implementá-los.
Esses detalhes não podem contrariar as decisões congeladas neste documento.
