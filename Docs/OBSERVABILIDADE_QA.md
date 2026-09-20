# Observabilidade e QA

Esta etapa adiciona observabilidade estruturada, correlação ponta a ponta e um
ambiente QA local ao ERP. A implementação é local-first: o trabalho operacional
continua quando a store de logs, o Control Center ou a Nexa ficam indisponíveis.
Observabilidade complementa a auditoria do ERP; ela não substitui os registros
de auditoria nem participa da transação financeira.

## Arquitetura

```text
requisição HTTP
  -> middleware: request_id + correlation_id + sessão pseudonimizada
  -> serviços do ERP / Financeiro / Suporte / Nexa
  -> DiagnosticMonitor
       -> memória limitada do processo
       -> data/<banco>_observability.sqlite3
            -> evento local_only
            -> evento pending
                 -> Outbox sanitizada em data/<banco>.sqlite3
                 -> SyncWorker + ACK idempotente
                 -> data/control_center.sqlite3 / diagnostic_events

Control Center
  -> Log Explorer, timeline, fingerprints e export JSON sanitizado
  -> investigação Nexa com snapshots read-only e escopo de tenant
  -> tenant TEST, QATestRun, cenários controlados e reset restrito

ERP Doctor
  -> banco operacional em consultas de preflight
  -> sidecar de observabilidade aberto com SQLite mode=ro/query_only
  -> configuração QA e guarda de fault injection
```

Os principais componentes são:

| Componente | Responsabilidade |
| --- | --- |
| `app/observability/events.py` | Contrato tipado, níveis, estados de sync, fingerprint e limites de retenção. |
| `app/observability/context.py` | Contexto por requisição e emissor best-effort. |
| `app/observability/sanitization.py` | Allowlist de metadados, redação e limites de tamanho. |
| `app/observability/store.py` | SQLite durável separado do banco operacional. |
| `app/services/erp_diagnostics.py` | Monitor, fallback em memória, agregação de risco e persistência estruturada. |
| `app/repositories/sync.py` e `app/services/sync_engine.py` | Outbox, retry, ACK, idempotência e entrega local. |
| `control_center/local_repository.py` | Persistência, consultas, timeline, fingerprints, export e execuções QA. |
| `control_center/qa.py` | Seed TEST, guards e cenários QA, incluindo ACK perdido real sobre Outbox/Sync isoladas. |
| `scripts/create_qa_environment.py` | Criação ou reconstrução confirmada do banco QA isolado. |
| `run_qa.py` | Validação e startup explícito dos serviços ERP/Control Center QA. |
| `scripts/erp_doctor.py` | Diagnóstico local sem reparo ou limpeza. |

## Contrato de evento

Cada evento possui `event_id`, horário UTC, nível, ambiente, tenant, instalação,
módulo, componente, tipo, operação, status, fingerprint, retry, versão do schema
e estado de sincronização. Quando existirem, também leva usuário e sessão
pseudonimizados, `correlation_id`, `request_id`, duração, código de erro, versão
do aplicativo, build e metadados técnicos permitidos.

Os níveis aceitos são `DEBUG`, `INFO`, `WARNING`, `ERROR` e `CRITICAL`. `DEBUG`
só é persistido quando `ERP_OBSERVABILITY_DEBUG=1`; o padrão é desativado. Os
estados locais são:

| Estado | Significado |
| --- | --- |
| `local_only` | O evento fica somente no sidecar local. |
| `pending` | O evento precisa de ACK do destino antes de poder ser removido. |
| `synced` | A entrega foi confirmada e a linha pode participar da retenção. |

O fingerprint usa SHA-256 truncado a 20 caracteres sobre a assinatura técnica
do evento: módulo, componente, tipo, operação, status, código de erro e tipo da
exceção. Mensagem livre, payload HTTP e dados comerciais não entram nessa
assinatura.

Os eventos instrumentados incluem:

| Origem | Tipos principais |
| --- | --- |
| HTTP e segurança | `http.request.started`, `http.request.completed`, `http.request.failed`, `security.request_rejected`. |
| Pagamentos e Caixa | `payment.requested`, `payment.recorded`, `payment.idempotent_duplicate`, `payment.validation.ok`, `cash.entry.created`, `cash.entry.prevented_duplicate`. |
| Fechamento e recebíveis | `note.close.requested`, `note.closed`, `receivable.created`, `receivable.payment_requested`, `receivable.updated`, `receivable.settled`. |
| Outbox e sync | `outbox.created`, `sync.started`, `sync.batch_started`, `sync.item_sent`, `remote.persisted`, `sync.ack_received`, `sync.failed`, `sync.retry`, `sync.dead_letter`, `sync.recovered_after_restart`, `sync.cleanup`, `sync.worker_failed`. |
| Nexa | `nexa.request.started`, `nexa.request.completed`, `nexa.request.failed`, `nexa.tool.used`, `nexa.unavailable`, `nexa.timeout`. |
| QA | `qa.environment.created`, `qa.scenario.started`, os eventos do cenário e `qa.scenario.completed`. |

Eventos informativos normais podem permanecer `local_only`. Avisos, erros e
falhas críticas são `pending` por padrão. Operações financeiras e outros pontos
que pedem `sync_required=True` também podem sincronizar eventos `INFO`.

## Correlação ponta a ponta

Para cada requisição, o middleware cria UUIDs independentes para `request_id` e
`correlation_id`, guarda ambos no contexto da execução e devolve os cabeçalhos
`X-Request-ID` e `X-Correlation-ID`. Uma rejeição de origem local inválida também
recebe seus próprios identificadores.

Os serviços usam `ContextVar`, portanto eventos emitidos durante a mesma
requisição herdam correlação, request e sessão. Quando o emissor recebe o
`user_id`, o monitor deriva um pseudônimo estável dentro do tenant; eventos sem
ator informado não recebem esse campo. A Outbox inclui o `correlation_id` no
payload sanitizado. O worker o recupera do envelope para correlacionar envio,
persistência remota, retry, dead letter e ACK.

Ao abrir um chamado sem correlação explícita, a Central de Suporte reutiliza o
`correlation_id` da requisição atual. O chamado, seus eventos técnicos e a
Outbox podem assim ser localizados pela mesma timeline.

No Control Center, a timeline consulta a correlação dentro do mesmo tenant e
ordena os eventos cronologicamente. Uma execução QA recebe uma correlação
exclusiva, registrada tanto no `QATestRun` quanto em todos os eventos simulados.

## Armazenamento separado

Para `data/erp.sqlite3`, o sidecar padrão é
`data/erp_observability.sqlite3`. A regra é determinística para outro banco:
`data/exemplo.sqlite3` usa `data/exemplo_observability.sqlite3`. A store recusa o
mesmo caminho configurado para o banco operacional e usa SQLite em WAL, com
transações curtas e índices para horário, correlação, fingerprint, filtros e
estado de sync.

O Control Center usa `data/control_center.sqlite3`, também separado. Ele recebe
somente contratos técnicos sanitizados na tabela `diagnostic_events`; não abre
o banco operacional para pesquisar clientes, Notas, pagamentos ou Caixa.

A Outbox continua no banco operacional porque é o registro durável da entrega.
Ela aceita apenas os tipos `support_ticket`, `health`, `heartbeat`, `risk`,
`incident` e `diagnostic_event`, com payload sanitizado, versão e chave de
idempotência. Isso permite recuperar envios após reinício e distinguir trabalho
operacional de cópias diagnósticas.

Se o sidecar de observabilidade não puder abrir ou gravar, o ERP inicia e mantém
um fallback limitado em memória. Uma falha de emissão nunca desfaz a operação do
usuário. Esse fallback não é durável e se perde ao encerrar o processo.

## Impacto de desempenho

O teste `tests/test_observability_performance.py` compara 200 transações SQLite
locais simples com outras 200 transações acompanhadas por um evento estruturado
persistido no sidecar. Na validação final local de 19/09/2026, três execuções
isoladas mediram entre 0,751 e 7,063 ms adicionais por ação. Na execução
representativa, o baseline levou 130,00 ms, o ciclo instrumentado 1.410,30 ms e
o custo adicional foi 6,401 ms por evento persistido. O gate aceita no máximo
20 ms adicionais por ação.

Esse é um limite conservador porque o produto não registra cada clique ou função:
somente pontos relevantes de operação, erro, segurança, sync, financeiro,
suporte e Nexa geram escrita. A Outbox e a entrega remota permanecem fora da
requisição do usuário.

## Retenção

As variáveis locais são:

```dotenv
ERP_OBSERVABILITY_RETENTION_DAYS=30
ERP_OBSERVABILITY_MAX_EVENTS=50000
ERP_OBSERVABILITY_MAX_BYTES=52428800
ERP_OBSERVABILITY_DEBUG=0
```

Os limites validados são de 1 a 365 dias, 100 a 2.000.000 eventos e 1 MiB a
2 GiB. A manutenção do sidecar remove, por idade, contagem ou espaço, somente
linhas `local_only` e `synced`. Ela roda na manutenção periódica do Sync e também
no encerramento normal. Linhas
`pending` são preservadas mesmo vencidas ou acima do orçamento, pois ainda não
possuem ACK.

O Doctor considera `pending` com mais de 24 horas como travado. Ele mede o
arquivo SQLite mais o WAL; a compactação da store considera o arquivo principal
e só executa checkpoint/VACUUM durante a manutenção explícita do encerramento.

Separadamente, a manutenção da Outbox ocorre no máximo a cada seis horas e
remove itens `synced` e diagnósticos legados confirmados há mais de sete dias,
em lotes limitados. O Control Center V1 ainda não possui retenção global
automática para `diagnostic_events` ou `sync_receipts`; o reset QA remove apenas
os artefatos do tenant TEST selecionado. O caminho direto de snapshot de saúde
mantém até 2.000 registros por instalação, mas o recebimento pela Outbox ainda
não aplica esse corte.

## Sincronização e ACK

O adaptador publica heartbeat e, quando há observações, snapshots de saúde,
riscos e eventos diagnósticos. Ele não consulta registros comerciais. A
publicação é best-effort e apenas acorda um worker em background; a requisição do
usuário não realiza o envio remoto. Cada publicação seleciona até 100 eventos
`pending` mais antigos, evitando que um backlog novo deixe eventos antigos sem
tentativa de entrega.

O worker usa lotes de até 25 por padrão, lease durável, no máximo oito tentativas
e backoff exponencial com jitter. O receptor valida que `tenant_id` e
`installation_id` correspondem à origem esperada. Cada efeito grava um recibo
durável por instalação e chave de idempotência.

Se o destino persistir um evento e o ACK se perder, o retry repete a mesma chave.
O receptor devolve o efeito já registrado, sem duplicá-lo; somente um ACK válido
marca a Outbox como `synced`. Para `diagnostic_event`, esse ACK também muda a
linha correspondente no sidecar de `pending` para `synced`. ACK ausente,
incompatível ou ambíguo nunca confirma a entrega.

ERP e Control Center aplicam allowlists próprias aos metadados. Os campos
centrais do evento são preservados, mas alguns detalhes aceitos no sidecar, como
informações de rota ou fila, podem ser descartados pelo receptor do Control
Center. A timeline remota não deve ser tratada como cópia byte a byte do evento
local.

Nesta etapa o destino é o Control Center local. Não há transporte para Supabase
ou cloud.

## Control Center

O painel abre em `http://127.0.0.1:8770`, tem conta, segredo de sessão e cookie
próprios, exige sessão interna e protege formulários com CSRF. O proprietário do
ERP não é automaticamente usuário do Control Center. `platform_admin` pode
consultar todos os tenants. `nexpoint_control_admin` começa sem acesso e só vê
logs, exports, chamados e investigações Nexa dos tenants gravados explicitamente
em seu escopo; escopo vazio falha fechado. Ações QA continuam exclusivas de
`platform_admin`.

Em **Diagnóstico e logs**, o Log Explorer permite filtrar por tenant,
instalação, intervalo UTC, severidade, módulo, componente, tipo, status,
fingerprint e correlação. A busca livre é limitada a correlação, fingerprint,
código de erro e operação. A tela retorna até 500 eventos por consulta.

O detalhe mostra timeline, identificadores, duração, retry, versão/build,
metadados seguros e resumo do fingerprint. A página própria do fingerprint
mostra ocorrências, primeira/última ocorrência, empresas afetadas dentro do
escopo autorizado, módulo, severidade e eventos recentes, com ação para a Nexa.
A exportação usa o schema
`nexpoint.diagnostics.export.v1`, passa novamente pela sanitização e responde com
`Cache-Control: no-store`.

## Nexa somente leitura

A investigação parte de um evento ou chamado já autorizado no servidor. O
navegador fornece somente o ID selecionado e a pergunta; não monta filtros de
tenant, não envia SQL e não escolhe o conteúdo das Tools.

Para logs, o backend oferece snapshots limitados das Tools:

- `search_erp_logs`;
- `get_log_timeline`;
- `get_error_fingerprint`;
- `get_recent_errors`;
- `get_incident_diagnostics`;
- `search_erp_help` e os contextos técnicos já existentes.

As cinco Tools específicas de logs carregam o mesmo escopo de tenant e
instalação derivado do evento ou chamado. A Nexa não recebe ferramenta de escrita, comando
financeiro, acesso ao SQLite ou SQL arbitrário. No fluxo iniciado por evento, o
payload inclui uma política explícita que classifica logs, metadados, traces,
tickets e stacks como evidência não confiável: instruções contidas nesses dados
não devem ser seguidas, e a resposta deve separar fatos, inferências e
confiança. A mesma política acompanha investigações iniciadas por chamado.

Os snapshots das Tools passam pelo contrato HMAC da ponte e por nova
sanitização. A ponte rejeita chaves ou valores com credenciais, verifica aliases
de escopo em toda a árvore e exige que tenant e instalação permaneçam iguais
entre os cinco snapshots. A pergunta do operador e o histórico curto do chat são texto
intencional da conversa e seguem assinados, mas não passam pelo sanitizador de
metadados dos logs; não cole segredos ou dados de clientes nesse campo. Se a
Nexa estiver desconfigurada ou indisponível, o painel retorna indisponibilidade
sem interromper o Log Explorer, o ERP ou a gestão local. A validação final
também executou o checkout local da Nexa, incluindo a Edge Function e as cinco
Tools de observabilidade: 188 de 188 testes passaram. Consulte também
[Integração local ERP ↔ Nexa](INTEGRACAO_NEXA.md).

## Ambiente QA

O ambiente oficial usa dados inteiramente fictícios. O script padrão cria
`data/nexpoint_qa_lab.sqlite3`, mantém o destino dentro de `data/`, exige que o
nome contenha `qa`, aceita apenas SQLite e bloqueia `data/erp.sqlite3`. Ele
também recusa o banco do Control Center quando ele aponta ao banco operacional e
recusa que QA e Control Center compartilhem o mesmo arquivo ou hardlink; essas
checagens ocorrem antes da geração. Depois prepara uma identidade opaca e
registra no Control Center um tenant do tipo `TEST` com sua instalação, saúde,
risco, chamado e evento iniciais.

O usuário QA é `qa.owner@nexpoint.invalid`. Todos os demais usuários gerados são
desativados. A senha pode ser gerada aleatoriamente e exibida uma única vez após
o sucesso, ou recebida por `NEXPOINT_QA_INITIAL_PASSWORD` com 16 a 256
caracteres; o valor em claro não é gravado em arquivo nem deve ser registrado em
logs.

### Criação deliberada

Este comando cria dois bancos isolados: `data/nexpoint_qa_lab.sqlite3` e
`data/nexpoint_qa_control_center.sqlite3`. Ele não abre os bancos operacionais e
não deve ser executado para uma inspeção comum:

```powershell
Set-Location 'D:\NexStudio\sistema ERP'
.\.venv\Scripts\python.exe scripts\create_qa_environment.py --qa --confirm CRIAR-NEXPOINT-QA
```

Para evitar que a senha seja impressa, forneça-a de forma transitória no
processo e remova a variável ao terminar. Nenhum valor de senha deve ser salvo
no script ou na documentação:

```powershell
$qaSecure = Read-Host 'Senha inicial QA' -AsSecureString
$qaCredential = [PSCredential]::new('qa.owner@nexpoint.invalid', $qaSecure)
$env:NEXPOINT_QA_INITIAL_PASSWORD = $qaCredential.GetNetworkCredential().Password
try {
    .\.venv\Scripts\python.exe scripts\create_qa_environment.py --qa --confirm CRIAR-NEXPOINT-QA
} finally {
    Remove-Item Env:NEXPOINT_QA_INITIAL_PASSWORD -ErrorAction SilentlyContinue
}
```

O launcher dedicado valida identidade, integridade, chaves estrangeiras,
hardlinks e o vínculo TEST antes de iniciar qualquer serviço. Para validar sem
abrir portas:

```powershell
.\.venv\Scripts\python.exe run_qa.py --qa --confirm ABRIR-NEXPOINT-QA --check
```

Para abrir ERP e Control Center, use dois terminais:

```powershell
.\.venv\Scripts\python.exe run_qa.py --qa --confirm ABRIR-NEXPOINT-QA --service erp
.\.venv\Scripts\python.exe run_qa.py --qa --confirm ABRIR-NEXPOINT-QA --service control-center
```

O ERP usa `127.0.0.1:8767` e o painel usa `127.0.0.1:8771`. O login ERP é
`qa.owner@nexpoint.invalid` com a senha exibida na criação. O launcher do painel
gera uma senha interna forte e a exibe uma vez, ou recebe
`NEXPOINT_QA_CONTROL_ADMIN_PASSWORD` e
`NEXPOINT_QA_CONTROL_SESSION_SECRET` apenas no ambiente local.

### Reconstrução do banco QA

O reset do script substitui somente o destino QA validado e exige outra frase:

```powershell
.\.venv\Scripts\python.exe scripts\create_qa_environment.py --qa --reset-qa --confirm RESETAR-NEXPOINT-QA
```

Esse comando é destrutivo para o banco QA fictício. Ele não deve apontar para o
banco operacional nem ser usado como rotina de diagnóstico. Quando o ambiente
já existe, o reset reutiliza a identidade opaca anterior: tenant e instalação
mantêm os mesmos IDs e o Control Center não acumula registros QA órfãos.

### Cenários no Control Center

O `run_qa.py` aplica `environment=qa`, `qa_mode=true` e `tenant_type=TEST`
antes de criar monitor, heartbeat, Outbox ou Sync. Ele exige caminhos explícitos
dos dois bancos e não reutiliza configuração operacional.

Somente `platform_admin`, em ambiente diferente de produção, com modo QA ativo
e tenant `TEST`, pode executar um cenário. O formulário exige a confirmação
`EXECUTAR <cenario>`. Os cenários disponíveis são:

| Código | Evidência controlada |
| --- | --- |
| `ack_lost` | Persistência remota, perda do ACK, retry e deduplicação. |
| `nexa_unavailable` | Início da chamada e indisponibilidade da Nexa. |
| `sync_remote_unavailable` | Falha de sync e agendamento de retry. |
| `http_500` | Resposta HTTP 500 simulada. |
| `timeout` | Timeout simulado no sync. |
| `retry` | Retry seguido de ACK. |
| `duplicate_request` | Requisição financeira duplicada e impedida. |
| `validation_error` | Rejeição de validação. |
| `dead_letter` | Retry seguido de dead letter. |
| `database_locked` | Banco bloqueado e rollback. |

Cada execução cria um `QATestRun` com cenário, ator interno, horários, resultado
esperado/observado, status e `correlation_id`. `ack_lost` usa Outbox,
`OfflineSyncEngine` e `LocalSyncRemote` reais em um SQLite QA separado: o destino
persiste, o primeiro ACK é descartado, ocorre retry, o recibo idempotente impede
duplicação e o segundo ACK sincroniza a Outbox. `validation_error` chama a
validação real da Outbox e prova que nenhuma linha inválida foi persistida. Os
demais cenários produzem sequências determinísticas e fictícias, sem derrubar
providers, bloquear SQLite de verdade ou alterar pagamentos e Caixa.

As flags `ERP_QA_MODE=1` e `ERP_TENANT_TYPE=TEST` formam uma guarda separada no
ERP e no Doctor. Não as habilite na instância operacional de cliente. A
configuração falha fechada se QA for ativado em produção ou em tenant diferente
de `TEST`.

### Reset de artefatos no painel

No detalhe do tenant TEST, o reset exige `platform_admin`, CSRF e a confirmação
`RESETAR <tenant_id>`. Ele remove daquele tenant somente execuções QA, eventos,
snapshots de saúde, incidentes, chamados, riscos e recibos de sync. A identidade
do tenant e da instalação é preservada e a saúde volta a `unknown`. Esse reset
do painel não reconstrói `data/nexpoint_qa_lab.sqlite3`.

## ERP Doctor

Execute o Doctor no diretório do projeto:

```powershell
.\.venv\Scripts\python.exe scripts\erp_doctor.py
$LASTEXITCODE
```

Os códigos são `0` para PASS, `1` para WARN e `2` para FAIL. O comando verifica:

- tabelas, migration esperada, `quick_check` e chaves estrangeiras;
- leases vencidos, dead letters e versões dos payloads da Outbox;
- persistência de nonce e telemetria;
- consistência entre pagamentos, alocações, Caixa, fechamentos e recebíveis;
- configuração do cadeado, sessões persistentes hasheadas e ausência de códigos
  de recuperação legados ainda ativos;
- autorizações temporárias de reset expiradas/ativas no sidecar do Control
  Center e chamados de recuperação pendentes há mais de 24 horas;
- schema e `integrity_check` do sidecar de observabilidade;
- retenção, espaço, estados de sync e `pending` com mais de 24 horas;
- canary, JWT, chave privada, Authorization, senha/hash, código de recuperação,
  token de reset e outros indícios de segredo;
- coerência das flags QA e comportamento fail-closed da guarda;
- configuração opcional da ponte Nexa e alterações Git em arquivos críticos.

O sidecar é aberto por URI SQLite `mode=ro` e `query_only=ON`. O Doctor não cria
o arquivo ausente, não migra, não poda, não executa checkpoint/VACUUM, não
repara e não reseta bancos. A ausência do sidecar é WARN. O comando também não
executa testes e não substitui `pytest`. Ao detectar material sensível, informa
apenas a contagem e a origem lógica da falha; o valor nunca é impresso.

## Uso local e validação sem gerar QA

Estes comandos não criam nem resetam o ambiente QA:

```powershell
Set-Location 'D:\NexStudio\sistema ERP'
.\.venv\Scripts\python.exe scripts\erp_doctor.py
.\.venv\Scripts\python.exe -m pytest -q tests\test_observability_qa.py tests\test_erp_doctor_observability.py tests\test_control_center_observability_ui.py
```

Os testes usam bancos temporários. Para validar o fluxo de sync e ACK perdido em
isolamento, inclua:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests\test_offline_sync.py
.\.venv\Scripts\python.exe -m pytest -q tests\test_qa_observability_nexa_integration.py
```

## Segurança

- O ERP e o Control Center aceitam somente `127.0.0.1`; sessões, cookies e
  credenciais são independentes.
- Metadados usam allowlist. Payload HTTP, formulário, SQL, stack bruto, nomes de
  clientes e valores financeiros não são campos aceitos.
- A sanitização remove canary, chaves privadas, JWT, Authorization, tokens,
  cookies, credenciais Google, e-mails, CPF/CNPJ, RG, cartões, telefones,
  endereços, nomes completos, valores monetários e caracteres de controle;
  texto e coleções têm tamanho limitado.
- O campo técnico `route` registra somente o path, sem query string. Segmentos
  dinâmicos do path podem conter IDs internos e permanecem no evento; rotas não
  devem transportar PII em seus segmentos.
- Usuários são pseudonimizados por tenant; IDs técnicos são validados e
  limitados.
- O receptor confere tenant e instalação e recusa recibos de idempotência com
  identidade remota, tipo ou versão incompatíveis. O emissor compara o conteúdo
  ao reutilizar uma chave, com exceção deliberada de `diagnostic_event`.
- Log Explorer, export e Nexa sanitizam novamente o conteúdo. A exportação não é
  cacheável e a Nexa trata logs como entrada hostil.
- O escopo de tenant é persistido no schema V6 do Control Center. Um suporte sem
  grants explícitos não recebe consulta global por fallback em dashboard,
  empresas, chamados, saúde, riscos, incidentes, Log Explorer ou Nexa.
- IDs técnicos já validados são preservados literalmente nos snapshots da Nexa;
  texto livre e metadados continuam sujeitos à redação de dados privados.
- Fault injection exige ambiente não produtivo, modo QA, tenant TEST, cenário
  conhecido e usuário autorizado. As rotas ainda exigem sessão, CSRF e frase de
  confirmação.
- Falha de observabilidade não relaxa autorização, auditoria nem integridade da
  operação; apenas reduz a evidência diagnóstica disponível.

## Limites atuais

- Toda persistência e sincronização são locais. Não há Supabase, cloud ou
  agregação entre computadores.
- O Control Center V1 não tem retenção automática global de logs ou recibos de
  sync recebidos; snapshots de saúde vindos pela Outbox também não usam o corte
  do caminho direto.
- A limpeza do sidecar é best-effort; falha de manutenção é reportada pelo
  Doctor e tentada novamente no ciclo periódico ou no encerramento.
- Eventos `pending` podem ultrapassar os limites configurados para preservar
  dados sem ACK.
- O adaptador envia o backlog em lotes de até 100 eventos `pending`; um volume
  maior precisa de vários ciclos de publicação.
- As allowlists de metadados do ERP e do Control Center não são idênticas;
  metadados remotos podem ser um subconjunto dos locais.
- O cálculo de risco relê a janela persistida do sidecar após restart; a janela
  analítica continua limitada aos últimos 15 minutos.
- O fallback em memória mantém no máximo 1.000 eventos por 24 horas e não
  sobrevive ao reinício.
- Risco usa uma janela local de 15 minutos; alertas começam no score 75 e têm
  cooldown de 15 minutos. Isso é sinal técnico, não previsão de falha.
- `ack_lost` e `validation_error` exercitam componentes reais em SQLite QA.
  Os outros oito cenários continuam simulações determinísticas: não derrubam
  rede/provider e `database_locked` não mantém um lock real.
- Pergunta e histórico do chat Nexa não são campos de log e não recebem a mesma
  redação automática; o operador deve manter dados sensíveis fora da conversa.
- A política contra instruções hostis acompanha os fluxos por evento e chamado;
  a ponte da Nexa também valida escopo e trata o conteúdo das Tools como dado.
- O Control Center transmite o papel real da sessão. `erp.logs.read` só é
  incluída quando há snapshots de log previamente autorizados e limitados.
- O chat Control Center → Nexa registra eventos locais de início, conclusão,
  falha, indisponibilidade/timeout e Tools utilizadas, sem registrar a pergunta.
- O Doctor é diagnóstico pontual e somente leitura; PASS não substitui a suíte
  completa, teste de performance, auditoria de segurança ou validação manual.

Detalhes do painel e da ponte estão em [Control Center](CONTROL_CENTER.md) e
[Integração local ERP ↔ Nexa](INTEGRACAO_NEXA.md).
