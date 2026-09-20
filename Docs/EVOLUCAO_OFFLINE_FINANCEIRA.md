# Operação local, sincronização e cobrança

O SQLite do ERP permanece a fonte de verdade para clientes, serviços, Notas,
pagamentos, saldos e Caixa. Essas operações não consultam a Nexa ou um serviço
na internet. O Control Center local recebe somente eventos técnicos sanitizados
e chamados; não recebe o cadastro operacional completo nem controla o Caixa.

## Outbox e Control Center

Os eventos transitórios possuem identificação e chave de idempotência estáveis,
payload versionado e estados `pending`, `sending`, `failed`, `synced` e
`dead_letter`. O worker roda em segundo plano, retoma leases interrompidos,
envia lotes pelo contrato `SyncRemote` e aguarda o ACK com chave e versão
correspondentes antes de marcar um item como sincronizado. Respostas ambíguas,
timeout e indisponibilidade deixam o item recuperável. Reenvios ao Control
Center local usam comprovantes persistentes para não reaplicar o mesmo evento.

Falhas recuperáveis usam espera progressiva com variação aleatória e eventual
indicação `Retry-After`; tentativas esgotadas passam a `dead_letter` para
inspeção. Somente itens transitórios **sincronizados e com ACK** podem passar
pela retenção. A limpeza nunca remove Notas, pagamentos, saldos ou Caixa, nem
itens `pending`, `sending`, `failed` ou `dead_letter`.

O indicador de sincronização usa `/sync/status` e mostra estado de conexão e
quantidade pendente sem exibir dados privados. Heartbeats incluem identificador
pseudônimo da instalação, versão, ambiente e resumo técnico da saúde. Eventos
diagnósticos persistentes armazenam informações sanitizadas. A proteção de
nonce também usa tabela persistente. O Doctor (`scripts/erp_doctor.py`) examina
o banco em modo somente leitura e separa inconsistências (`FAIL`) de alertas
operacionais, como lease vencido ou dead letter (`WARN`).

## Pagamento, dívida e fechamento

Cada pagamento confirmado é um registro imutável com chave de requisição. O
valor efetivamente recebido cria uma entrada única no Caixa na mesma
transação; a Nota pode continuar parcialmente paga. O fechamento da Nota é
separado do recebimento: encerra a operação, fixa o total e os valores pagos,
e cria um saldo devedor da própria Nota se houver parcela em aberto. Repetir a
requisição de fechamento não cria novo saldo ou Caixa. A Nota fechada não pode
ter serviços e valores editados sem um fluxo futuro de retificação auditável.

Um saldo anterior pode ser apresentado e vinculado a uma nova Nota apenas por
escolha do operador. O vínculo guarda a origem e o valor apresentado, sem
recriar a dívida. Pagamentos da nova cobrança são alocados em ordem
determinística aos saldos anteriores vinculados e depois ao serviço atual;
cada alocação mantém sua referência contábil. A alocação usa o saldo **atual**
da dívida mais antiga, independentemente do valor que ela tinha quando foi
vinculada. Cada recebimento registra um `Payment`, suas alocações por destino e
uma única movimentação de Caixa. Uma dívida também pode ser recebida
diretamente, sem serviço fictício, por `ReceivablePayment` e uma entrada de
Caixa com origem `RECEIVABLE_PAYMENT`. Nesse fluxo direto, cartão não é
oferecido até existir entrada dos detalhes de terminal e parcelas.

A interface usa esse mesmo ledger como fonte. Na Nova Nota, o pagamento inicial
é opcional e, quando selecionado, Nota, Payment, alocação, Caixa, auditoria e
observabilidade formam um único efeito transacional. No detalhe, cada pagamento
posterior permanece separado no histórico. A tela calcula `PENDENTE`, `PARCIAL`
ou `PAGO` conforme o valor realmente recebido; depois do fechamento, saldo em
aberto é apresentado como `SALDO_DEVEDOR`. Esses estados não são campos de
escolha do operador.

O limite de cada novo pagamento é o saldo cobrável. O backend rejeita valores
inválidos, precisão monetária incorreta e recebimento acima do saldo, além de
impedir que uma edição reduza o total abaixo do que já foi pago. Retries e duplo
clique reutilizam a chave da requisição, garantindo um Payment e uma entrada de
Caixa por recebimento efetivo.

O fechamento guarda um retrato imutável do valor pago naquele instante; uma
quitação posterior da dívida altera o estado financeiro da Nota de origem com
histórico e auditoria, preservando esse retrato. A coluna de valor cobrável
soma o serviço da Nota ao saldo anterior ainda em aberto, sem alterar o total
fiscal/original da Nota antiga.

O ERP Doctor valida em leitura o vínculo entre pagamentos confirmados e Caixa,
movimentações órfãs, quitações diretas e Caixa e a igualdade entre o saldo
persistido e o ledger de alocações/quitações. Uma divergência é `FAIL`; o Doctor
não corrige registros e não substitui a suíte de testes.

As migrations ampliam o esquema sem apagar registros anteriores. Em instalações
existentes, a aplicação da migration ocorre na inicialização normal do ERP;
para validar uma instalação antes de adotá-la, execute testes e Doctor em uma
cópia isolada do SQLite. O gerador demo utiliza somente destinos explicitamente
marcados como demo e nunca deve ser executado no banco operacional.
