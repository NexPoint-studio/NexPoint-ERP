# Módulo Clientes

O módulo Clientes mantém cadastros, endereços e a linha do tempo local do
relacionamento. Ele utiliza SQLAlchemy e o SQLite do ERP e recebe atividades
automáticas das Notas de Serviço.

## Funcionalidades

- cadastro e edição de pessoa ou empresa;
- CPF e CNPJ opcionais, normalizados e validados localmente;
- bloqueio de documento duplicado;
- aviso confirmável para telefone ou WhatsApp possivelmente duplicado;
- endereço estruturado com preenchimento manual;
- pesquisa, filtros, ordenação e paginação;
- perfil com dados, situação, último retorno e último serviço;
- linha do tempo individual e histórico geral paginados;
- registro manual de visita;
- inativação e reativação sem exclusão do histórico;
- classificação configurável do tempo sem retorno;
- alertas administrativos de inatividade.

## Persistência e atividades

As tabelas do domínio são:

- `customers`: cadastro principal, status e autoria;
- `customer_addresses`: endereço estruturado individual;
- `customer_activities`: linha do tempo imutável de eventos.

As atividades aceitas são `CUSTOMER_CREATED`, `CUSTOMER_UPDATED`,
`CUSTOMER_DEACTIVATED`, `CUSTOMER_REACTIVATED`, `VISIT`, `NOTE`,
`SERVICE_CREATED` e `SERVICE_COMPLETED`.

Criar uma Nota gera `SERVICE_CREATED` na mesma transação, com a data de
recebimento, referência legível e snapshots suficientes para apresentar a Nota e
os serviços posteriormente. A transição da Nota para `PRONTO` gera
`SERVICE_COMPLETED`, também de forma transacional.

Atividades vindas de Nota recebem `source_type=SERVICE_NOTE`, ID técnico e
referência legível. A unicidade de tipo da atividade, origem e cliente impede que
um retry registre o mesmo evento duas vezes.

## Último retorno e último serviço

**Último retorno** mede quando o cliente apareceu ou iniciou atendimento. Por
isso, considera somente:

- `VISIT`;
- `SERVICE_CREATED`.

`SERVICE_COMPLETED` aparece na linha do tempo, mas não reinicia a contagem de
inatividade quando a produção termina dias depois.

**Último serviço** é obtido das Notas reais do cliente. O perfil mostra número e
série, data do atendimento, estado operacional e nomes dos serviços congelados
nos itens da Nota. O histórico não é reconstruído usando o nome ou o preço atual
do catálogo.

## Relacionamento e inatividade

Os limites são uma fonte única de verdade nas configurações locais:

- `customers.inactivity.recent_days`;
- `customers.inactivity.attention_days`;
- `customers.inactivity.distant_days`.

Os valores iniciais são 30, 60 e 90 dias. Com esses valores, a classificação é:

- até 30 dias: `Recente`;
- de 31 a 60 dias: `Atenção`;
- de 61 a 90 dias: `Afastado`;
- mais de 90 dias: `Há muito tempo`;
- sem visita ou Nota: `Nunca atendido`.

Os filtros 30+, 60+ e 90+ usam os mesmos limites configurados. O dashboard da
Administração mostra as contagens correspondentes e abre a lista de Clientes já
filtrada. Ele não carrega todos os registros para formar os indicadores.

O status cadastral (`Ativo` ou `Inativo`) é independente da classificação.
Inativar um cadastro preserva o Cliente, o endereço e todas as atividades e não
altera artificialmente a data do último retorno.

## Busca, filtros e paginação

A lista pesquisa parcialmente por nome, nome fantasia, documento, telefone,
WhatsApp e e-mail. Os filtros cobrem Pessoa/Empresa, Ativo/Inativo, Nunca
atendido, faixas configuradas e um número customizado de dias. A ordenação aceita
nome, cadastro e último retorno. A consulta pagina 25, 50 ou 100 clientes.

O histórico geral oferece busca, Cliente, tipo de atividade, usuário, período e
paginação. O perfil também pagina sua própria linha do tempo. Assim, novas Notas
não fazem as páginas carregar um histórico crescente sem limite.

## Permissões e status cadastral

- `customers.view`: lista, perfil e histórico;
- `customers.create`: novo cadastro;
- `customers.edit`: edição;
- `customers.deactivate`: inativar ou reativar;
- `customers.activity.create`: registrar visita.

Editar dados cadastrais preserva o status existente. Mesmo um envio manual de
`is_active` no formulário de edição é ignorado pelo repositório. As ações
**Inativar cliente** e **Reativar cliente** usam uma operação de status separada
e exigem `customers.deactivate` no backend.

O Proprietário e o papel `user` recebem essas permissões por padrão. O papel de
Entrega não recebe acesso automático ao módulo.

## Testes

Execute offline:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Os testes usam bancos SQLite temporários e dados fictícios. Eles não modificam o
banco utilizado pelo aplicativo desktop.
