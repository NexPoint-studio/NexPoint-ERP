# Administração e Pagamentos

## Administração 2.0

A Administração é a área de gestão do Proprietário da empresa e possui nove
áreas funcionais:

1. **Visão geral**: indicadores de produção, clientes sem retorno e, somente com
   permissão financeira, resultados do Caixa.
2. **Serviços**: catálogo administrativo, categorias, unidades de cobrança,
   preços, histórico de preços e status dos cadastros.
3. **Financeiro**: saldo, entradas, saídas, taxas, resultado operacional,
   histórico completo, filtros, relatórios e gráficos.
4. **Pagamentos e taxas**: formas de pagamento, terminais e regras de taxa.
5. **Usuários e permissões**: usuários locais, papéis, status, senhas e matriz de
   permissões.
6. **Empresa**: dados institucionais, logo local e fuso horário.
7. **Suporte**: concessões explícitas, temporárias e revogáveis para um usuário
   visível com papel exclusivo de Suporte.
8. **Auditoria**: consulta paginada e filtrável dos eventos, com ocultação de
   campos sensíveis estruturados.
9. **Sistema**: versão, build, ambiente, schema, backup, restauração preparada e
   estado local de atualização.

O papel técnico legado `admin` é preservado para evitar uma migração destrutiva,
mas aparece na interface como **Proprietário**. Ele representa o gestor da
empresa. O papel `support` não recebe permissões permanentes nem cria uma conta
automaticamente. Não existe acesso permanente do desenvolvedor, backdoor ou
senha secreta.

O dashboard consulta os indicadores financeiros apenas quando o usuário possui
`finance.overview.view`. Os cartões de serviços atrasados e clientes inativos
abrem as listas operacionais já filtradas; o dashboard apresenta contagens, sem
carregar listas ilimitadas.

## Usuários, papéis e segurança

O Proprietário pode listar e criar usuários, editar seus dados e papéis,
ativá-los ou inativá-los, redefinir senhas com hash seguro e editar a matriz de
permissões. Usuários não são apagados fisicamente porque podem ser referenciados
por histórico e auditoria.

As regras de proteção impedem:

- inativar a própria conta de Proprietário;
- inativar o último Proprietário ativo;
- retirar de si mesmo o papel de Proprietário;
- retirar do papel Proprietário as permissões administrativas essenciais;
- usar uma permissão delegada de usuários para assumir uma conta ou papel com
  privilégios superiores;
- redefinir senha, status ou dados de um Proprietário por um usuário que não seja
  Proprietário.

As operações administrativas são autorizadas no backend e geram auditoria. A
interface apenas reflete essas decisões; acessar uma URL ou enviar um POST
diretamente não contorna as permissões.

Cookies de sessão carregam a versão de autenticação do usuário e uma geração
global persistida. Logout, redefinição de senha, mudança de login, status, papel
ou permissões invalidam as sessões afetadas. Uma restauração concluída gira a
geração global antes de o ERP voltar a aceitar requisições.

## Suporte temporário e auditoria

Somente um Proprietário ativo com `admin.support.manage`, após informar a senha
atual, pode autorizar ou revogar suporte. A janela dura no máximo 24 horas e
registra responsável, finalidade, início, término e eventual revogação. O alvo
precisa ser um usuário ativo com exclusivamente o papel `support`.

Durante uma concessão ativa, o escopo é recalculado no banco em cada requisição
e contém apenas `admin.overview.view`, `admin.audit.view` e
`admin.system.view`. Ele não inclui Empresa, usuários, permissões, financeiro,
Caixa, pagamentos, backup, restauração ou gestão do próprio suporte.

A página de Auditoria permite filtrar por ação, responsável, recurso e período.
Senhas, tokens, secrets, cookies e credenciais presentes em detalhes JSON são
substituídos por um marcador antes da renderização.

## Empresa e precedência de configuração

Administração > Empresa mantém os seguintes valores no SQLite:

- nome da empresa e nome fantasia;
- CPF ou CNPJ opcional;
- telefone e e-mail;
- endereço;
- caminho de logo local sob `/static/`;
- fuso horário IANA.

O documento institucional é opcional. Caminhos externos, URLs e travessia de
diretórios não são aceitos para o logo.

Na inicialização, `.env.local` fornece os valores iniciais das chaves ainda
ausentes. Nas requisições seguintes, os valores persistidos no banco prevalecem
para nome da empresa, nome do aplicativo, versão, logo e fuso. O ambiente é o
fallback quando a chave não existe ou, no caso do fuso e do logo, quando o valor
persistido não pode ser utilizado com segurança.

Configurações técnicas de inicialização continuam no ambiente: segredo de
sessão, host local, porta e localização do banco. O servidor aceita somente
`127.0.0.1`.

## Pagamento e Caixa são entidades diferentes

`Payment` registra que o cliente quitou uma Nota de Serviço. `CashMovement`
registra o efeito financeiro realizado. Para uma Nota positiva, um recebimento
confirmado cria ambos, vinculados entre si, mas cada registro conserva seu papel
no domínio.

Uma Nota com total maior que zero admite no máximo um `Payment` confirmado. O
índice único parcial no banco também protege essa regra em concorrência. Não há
pagamento parcial nesta versão.

Uma Nota de total zero mantém `financial_status=PAGO` com motivo `ZERO_TOTAL` e:

- não cria `Payment`;
- não cria `CashMovement`;
- não solicita forma de pagamento.

## Formas de pagamento

O catálogo existente `cash_payment_methods` é compartilhado entre Caixa e
Pagamentos. Cada forma possui um tipo semântico:

- `CASH`: dinheiro;
- `PIX`: Pix;
- `CARD`: cartão;
- `BOLETO`: boleto já recebido;
- `OTHER`: outra forma.

Administração > Pagamentos e taxas permite criar, editar, ordenar, ativar e
inativar formas. Inativar impede novos usos, sem remover as referências dos
registros anteriores. Boleto selecionado no recebimento significa que o valor já
foi recebido; o ERP não emite boleto nem trata um boleto pendente como pagamento.

## Cartão e terminais

Uma forma do tipo `CARD` exige:

- terminal ativo;
- modalidade `DEBIT` ou `CREDIT`;
- uma parcela no débito;
- de uma a 999 parcelas no crédito.

Campos de terminal, modalidade e parcelas são rejeitados para outras formas. Os
terminais são genéricos e configuráveis por código, nome, descrição, ordem e
status. Nenhuma marca ou adquirente é criada como dependência do sistema.

## Regras e cálculo de taxas

Uma regra de taxa pode considerar forma, terminal opcional, modalidade,
quantidade de parcelas e período de vigência. Ela combina um percentual de até
quatro casas decimais com um valor fixo em centavos. O servidor escolhe a regra
aplicável na data do pagamento e prefere a candidata mais específica. Uma regra
encerrada continua valendo para datas dentro do seu intervalo histórico; uma
regra futura desativada antes de começar não participa de nenhum cálculo.

O cálculo usa `Decimal`, `ROUND_HALF_UP` e centavos inteiros:

```text
taxa = arredondar(valor bruto x percentual) + valor fixo
líquido = valor bruto - taxa
```

Para uma Nota de R$ 100,00 e taxa de 3,03%, o Pagamento registra bruto de
R$ 100,00, taxa de R$ 3,03 e líquido de R$ 96,97. A Nota fica totalmente paga;
a taxa não permanece como dívida do cliente.

Alterar uma regra cria outra versão e encerra a vigência anterior. O Pagamento
congela nome e tipo da forma, terminal, modalidade, parcelas, percentual, taxa
fixa, taxa calculada, valor bruto e valor líquido. Mudanças posteriores de
cadastro não reescrevem o histórico financeiro.

## Fluxos de recebimento

### A — Registrar pagamento sem mudar a operação

O pagamento pode ser registrado antes de a produção terminar. O servidor cria o
`Payment`, marca a situação financeira como `PAGO`, cria a entrada automática no
Caixa e registra eventos e auditoria. O estado operacional da Nota permanece
inalterado.

### B — Entregar e receber

Em uma Nota `PRONTO` e financeiramente pendente, **Entregar e receber** muda o
estado operacional para `ENTREGUE`, cria o `Payment`, marca a Nota como `PAGO`,
cria o Caixa e registra eventos e auditoria como uma única operação.

### C — Entregar sem receber

**Entregar sem receber** muda a Nota para `ENTREGUE` e mantém a situação
financeira `PENDENTE`. Nenhum Pagamento ou Caixa é criado nessa etapa. A ação
**Registrar pagamento** pode quitar a Nota posteriormente e então gerar os dois
registros financeiros.

## Atomicidade, idempotência e concorrência

O recebimento usa uma transação SQLite exclusiva. `Payment`, situação financeira
da Nota, transição operacional opcional, `CashMovement`, eventos e auditoria são
confirmados em conjunto. Uma falha em qualquer etapa causa rollback integral.

Cada formulário recebe um UUID de requisição. Repetir a mesma requisição com os
mesmos dados retorna o resultado existente; reutilizar o UUID com dados diferentes
gera conflito. O Caixa automático usa `origin=SYSTEM`, `source_type=PAYMENT` e o
ID técnico do Pagamento. Restrições únicas no banco e atualização da revisão da
Nota impedem pagamentos e lançamentos duplicados em requisições concorrentes.

O novo domínio financeiro persiste dinheiro em centavos inteiros. Como o Caixa
legado usa `Numeric(14,2)`, o serviço relê os valores bruto, taxa e líquido após a
gravação e os compara em centavos; qualquer diferença cancela toda a transação.

Movimentações `SYSTEM` não podem ser editadas nem canceladas isoladamente pelo
Caixa. Um fluxo completo de estorno, contas a receber e integrações com
adquirentes não faz parte desta versão.

## Permissões principais

- `admin.overview.view`: visão geral administrativa;
- `admin.services.*`: gestão administrativa do catálogo e das unidades;
- `finance.overview.view`: resumo e histórico financeiro globais;
- `finance.reports.view`: relatórios financeiros;
- `finance.config.manage`: formas, terminais e taxas;
- `payments.receive`: registrar o recebimento de Notas;
- `admin.users`: gerir usuários;
- `admin.permissions`: gerir a matriz de permissões;
- `admin.settings`: gerir dados da empresa.
- `admin.support.manage`: autorizar e revogar suporte temporário;
- `admin.audit.view`: consultar eventos de auditoria;
- `admin.system.view`: consultar metadados locais do sistema;
- `admin.backups.manage`: criar, listar e baixar backups locais;
- `admin.restore`: preparar ou cancelar restauração, sempre limitada ao papel
  Proprietário.

As permissões antigas recebem sucessoras apenas de forma aditiva. Papéis e
concessões personalizadas existentes são preservados.

## Persistência

As migrations aditivas desta integração são:

- `0009_payment_configuration`: tipo semântico das formas, terminais e regras de
  taxa;
- `0010_payments`: entidade Pagamento, snapshots e garantias de unicidade;
- `0011_customer_activity_sources`: origem idempotente das atividades automáticas
  de Clientes.
- `0012_administration_security`: versão de autenticação dos usuários, concessões
  temporárias de suporte e índices de consulta da auditoria.

Nenhuma migration converte movimentações manuais antigas em Pagamentos. Dados
históricos permanecem com sua origem original.
