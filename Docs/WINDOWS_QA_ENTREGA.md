# Gate de Windows limpo para a entrega Nil lav

Estado em 04/10/2026: o backend QA local foi exercitado, mas o **Windows limpo
continua não executado**. Este documento distingue esses resultados; nenhum
teste de backend substitui a evidência de instalação real.

O host de desenvolvimento é Windows 10 Pro 19045, mas não possui
`WindowsSandbox.exe`, VirtualBox, VMware ou QEMU disponível. A consulta DISM ao
recurso Windows Sandbox exigiu elevação (erro 740). O host e o notebook real
não substituem uma VM/ambiente descartável para este teste.

Há também um gate de arquitetura do ensaio: o executável congelado atual aceita
somente manifesto `production/PROD` e a ativação empacotada chama o projeto
Supabase PROD fixo. Assim, abrir um Setup candidato em VM sem código de ativação
testa instalação e primeira tela, mas **não** conclui ativação, sync, recovery ou
updater A→B sem tocar PROD. Antes de executar essas etapas, preparar um backend
QA isolado e uma build de ensaio que se vincule exclusivamente a ele, ou outro
ambiente equivalente que demonstre o mesmo comportamento sem dados PROD.
Nenhuma alteração no Supabase/Render PROD está autorizada como atalho para QA.

## Sequência quando o ambiente Windows estiver disponível

1. Criar VM Windows limpa e descartável, usuário Windows novo, snapshot inicial,
   sem acesso a arquivos reais da Nil lav. Registrar versão do Windows e hash do
   instalador candidato.
2. Conferir no candidato os atalhos, pasta do programa, pasta de dados separada,
   desinstalação registrada e versão. Não instalar no usuário Windows principal.
3. Com backend QA isolado, ativar instalação fictícia e confirmar DPAPI no mesmo
   usuário Windows, fechamento/reabertura direta e ausência de login operacional.
4. Exercitar Clientes, Serviços, Notas, Caixa, Financeiro, Administração sem
   senha, Empresa, Admin Lock, mudança/remoção da senha, recovery com consumo
   único, heartbeat, sync, versão e isolamento entre duas empresas QA.
5. Criar dados fictícios; instalar build QA A, publicar B somente no backend QA;
   validar detecção, download, hash, fechamento, instalação, reabertura e
   persistência de SQLite, company profile, Admin Lock e DPAPI.
6. Repetir com rede ausente, download interrompido, hash incorreto, artefato
   corrompido e cancelamento de UAC quando aplicável. Validar recuperação sem
   restaurar banco antigo sobre dados novos.
7. Desinstalar e reinstalar na VM; conferir política de preservação dos dados.
   Guardar evidência sanitizada (versões, hashes, contagens e resultados), nunca
   código de ativação, credencial, cliente real ou segredo.
8. Restaurar/destruir a VM ao fim do ensaio. Só então considerar os gates
   pré-PROD junto aos testes de código, PostgreSQL/Edge e backup real.

O status de cada item deve ser registrado com evidência da VM quando executado.
Nenhum item foi marcado como aprovado por testes unitários ou navegador local.

Foi feita somente uma compilação de **sonda QA não distribuível** do helper
`NexPointUpdater.exe` a partir do worktree atual. O binário executou
`--verify-runtime` com exit code 0 e passou no scan de conteúdo do pacote.
Em um `%LOCALAPPDATA%` sintético e isolado neste mesmo host, o helper compilado
executou `--handoff-prepare`: criou journal DPAPI, backup e relatório, mantendo
credencial e SQLite originais. Não contatou a nuvem. Isso valida o caminho
empacotado de preparação neste Windows, mas não valida instalação limpa,
preparação no notebook, confirmação cloud, update A→B ou limpeza do Setup.

## Rodada de backend isolado — 04/10/2026

**Decisão atual: NO-GO para a entrega Nil lav.** Não houve instalação em Windows
descartável nem update real A→B. A execução abaixo foi somente no host de
desenvolvimento e em contêineres QA próprios. Não houve push, deploy, acesso
invasivo a PROD nem alteração em `Entrega/NilLavLavanderia`.

### Infraestrutura e separação

| Componente | Estado | Evidência e limite |
| --- | --- | --- |
| QA local preexistente | PASS para isolamento local | `run_qa.py --qa --confirm ABRIR-NEXPOINT-QA --check` validou `data/nexpoint_qa_lab.sqlite3` e `data/nexpoint_qa_control_center.sqlite3`; ambos tiveram `PRAGMA integrity_check=ok` e zero linhas em `PRAGMA foreign_key_check`. Essa dupla SQLite não é o backend da ativação empacotada. |
| Supabase de desenvolvimento `nexpoint-erp` | Não reutilizado | Já existia com portas 55420–55429 e Edge parado. Ele não foi tratado como QA de entrega. |
| Supabase QA separado | PASS para startup/schema; parado ao final | Cópia apenas dos arquivos Supabase versionados em `artifacts/qa-handoff-backend-20261004`, projeto `nexpoint-erp-handoff-qa`, portas 56420–56429; seis serviços QA próprios (Postgres, Kong, Auth, REST, Storage e Edge), com rede e volumes Docker distintos dos de `nexpoint-erp`. Nove migrations aplicadas em banco novo. O projeto QA foi parado sem `--no-backup`; volumes e evidência foram preservados. |
| Credenciais do stack QA | NOT VERIFIED para separação completa | Nenhuma credencial PROD foi usada. O CLI não apresentou as chaves locais do projeto QA após a parada, portanto não foi demonstrado que as chaves geradas para os dois stacks locais sejam diferentes. Não reutilizar a fixture como backend de entrega até verificar/rotacionar chaves QA exclusivas sem expô-las em logs. |
| Storage de releases QA | PASS para configuração | Bucket `nexpoint-releases` privado; nenhum objeto/release publicado nesta rodada. |
| Tenant e installation fictícios no PostgreSQL QA | PASS para fixture de backend | `qa-nexpoint-lab` / `qa-win-clean-01`, nome `NexPoint QA Lab`, label `Windows QA Clean`; um tenant, uma instalação, uma credencial e um código consumido. A instalação foi movida para `update_channel=internal`. Código e secret foram gerados em memória e não foram impressos/persistidos no relatório. |
| Classificação lógica QA | KNOWN LIMITATION | O contrato cloud atual só aceita tenant `customer`/`internal` e marca a instalação como `environment=prod`, `channel=prod`, mesmo no projeto fisicamente isolado. A fixture usa tenant `customer`; `internal` é somente o **canal de update**. Isso não é uma build `QA` do desktop nem autoriza contato com PROD. |
| Control Center QA integrado ao PostgreSQL QA | BLOCKED | O Control Center QA preexistente usa SQLite próprio e identidade diferente. Não foi demonstrado que ele administra o tenant/installation do novo PostgreSQL QA. |
| Exposição das portas QA | BLOCKED | O Supabase CLI publicou Kong e PostgreSQL em `0.0.0.0:56421` e `0.0.0.0:56422` (também IPv6). Foi testada uma rede Docker exclusiva com `com.docker.network.bridge.host_binding_ipv4=127.0.0.1` e `supabase start --network-id np-handoff-qa-loopback`, conforme a [orientação oficial do Supabase](https://supabase.com/docs/guides/local-development); nesta máquina/CLI 2.119.0 o bind publicado continuou em todas as interfaces. O projeto QA foi parado novamente, com volumes preservados; o Supabase de desenvolvimento continuou ativo. Antes de reiniciar, restringir o acesso da VM e fornecer origem TLS confiável sem usar PROD. |
| Isolamento de PROD | PASS para ações executadas | Todas as escritas desta rodada ocorreram no projeto Docker `nexpoint-erp-handoff-qa` ou em `artifacts/` ignorado. A migration foi editada apenas no checkout local e na cópia QA. Nenhum comando de deploy/push ou migration remota foi usado. |

### Defeito de migration encontrado em banco novo

Migration afetada: `supabase/migrations/20261004100100_company_profiles.sql`.
Na primeira subida isolada, a aplicação dessa migration falhou com
`unexpected ingestion contract`. O bloco de verificação procurava na saída de
`pg_get_functiondef` uma sequência que incluía quebra de linha e espaços.
O arquivo de trabalho Windows estava em CRLF, mas o PostgreSQL restituiu a
definição com LF; por isso o contrato correto foi rejeitado. A correção mantém
o fail-closed e substitui **somente** o token estável
`'risk', 'incident', 'diagnostic_event'` pela lista que inclui
`'company_profile'`. A segunda subida do projeto QA aplicou as nove migrations,
e a função resultante contém `company_profile`. O impacto é destravar
instalações/migrations futuras em Windows; não há alteração de dados existentes.
PROD não foi afetado porque não houve execução de SQL remoto, deploy ou push.

### Contratos exercitados

| Fluxo | Estado | Evidência e limite |
| --- | --- | --- |
| Edge activation QA | PASS de backend | POST ao Edge local retornou HTTP 200 para código one-time fictício; retry do mesmo request foi idempotente (200); novo request com o mesmo código foi rejeitado (409). GET não autorizado foi rejeitado (405). O código foi consumido na fixture de backend, **não** em um ERP instalado. |
| PostgreSQL focado em Empresa/ativação | PASS sintético | `scripts/security/test_company_activation_database.py` passou em 29/29 checks num Postgres descartável, incluindo todas as migrations, sync/rename, replay, conflito de revisão e RLS. Uma tentativa concorrente com a suíte Python falhou apenas no startup do contêiner temporário; a repetição isolada passou. |
| RLS/transferência PostgreSQL | PASS sintético | `scripts/security/test_handoff_database.py` passou em 83/83 checks num Postgres descartável, inclusive isolamento de tenants, replay e integridade de escopo. Resultado não prova transferência física ou acesso de VM. |
| Edge automatizado | PASS | 57/57 testes Node/TypeScript com `--experimental-transform-types`, incluindo ativação v2 e contratos de sync, recovery, update e handoff. A primeira tentativa sem esse flag falhou por limitação do runner TypeScript; a execução correta passou. |
| Empresa, rename, heartbeat, sync no ERP instalado | NOT TESTED | O SQLite QA local e o PostgreSQL QA novo não representam a mesma installation. Falta executar o fluxo em build QA instalada. |
| Admin Lock, Recovery, replay de reset no ERP instalado | NOT TESTED | Há regressões automatizadas, mas nenhum teste com Setup/Control Center QA vinculado foi feito. |
| Integridade SQLite após update | NOT TESTED | Os dois SQLite QA preexistentes estão íntegros, mas nenhum deles passou por update A→B instalado. |

### Regressão após a correção

| Verificação | Resultado |
| --- | --- |
| Python completo | PASS: exit code 0, 1.227 testes coletados e progresso em 100%; runner final gravou o exit code em `artifacts/qa-handoff-backend-20261004/pytest-final-verified.exit`. |
| PostgreSQL Empresa/ativação | PASS: 29/29 checks em contêiner descartável. |
| PostgreSQL transferência/RLS | PASS: 83/83 checks em contêiner descartável. |
| PostgreSQL releases/update | PASS: 63/63 checks em contêiner descartável. |
| Edge TypeScript | PASS: 57/57 testes com `node --experimental-transform-types --test`. |
| Secret scan de candidatos Git | PASS: 479 arquivos, sem segredos detectados. O Setup candidato e seu bundle também passaram no scan de distribuição, mas permanecem impróprios para ativação QA. |
| `git diff --check` | PASS: sem erro; apenas aviso de conversão LF/CRLF da configuração Git no Windows. |

Não há screenshots de instalação/ativação/update: esses passos não foram
executados em VM. Os logs brutos de startup do CLI foram sanitizados para não
reter possíveis chaves QA; este documento conserva somente resultados e hashes.

### Windows e updater

| Gate | Estado | Motivo |
| --- | --- | --- |
| VM Windows limpa e descartável | BLOCKED | O host é Windows 10 Pro 19045, sem `WindowsSandbox.exe` nem VM conhecida. Consulta/habilitação do Windows Sandbox exige UAC (erro de elevação). Nenhuma instalação foi executada no usuário Windows principal. |
| Release A via Setup | BLOCKED | O candidato `candidate-e50ed62/installer/NexPointERP-Setup.exe` tem SHA-256 `b37958c211b7b8f3cdfc225fb9f83e7da862e99399493ce2c47e311317aee2e7`, mas manifesto `production/PROD`, referência fixa ao projeto PROD e nenhum `activation-profile.json`. Portanto, **não deve ser aberto para ativação QA**. |
| Release B e publicação `internal` | NOT TESTED | O canal da fixture cloud está `internal`, mas não existe Release B QA válida nem artifact publicado no bucket QA. |
| Detecção/download/hash/instalação/reinício/versão B | NOT TESTED | Dependem de duas builds QA vinculadas ao backend QA e da VM. |
| Preservação de dados, empresa, Admin Lock, DPAPI, heartbeat/sync | NOT TESTED | O ensaio de backend não criou uma credencial DPAPI na VM nem atualizou o SQLite de uma instalação. |
| Sem rede, hash inválido, UAC cancelado, update repetido, uninstall/reinstall | NOT TESTED | Exercitar somente em VM descartável com build QA e artifact B próprios. |

O próximo passo técnico é disponibilizar uma VM Windows descartável (ou
habilitar Windows Sandbox com UAC/reinício manual), criar uma build de ensaio
**inequivocamente QA** que aceite apenas o backend QA e sua origem segura, e
integrar o Control Center QA ao mesmo tenant. Só depois gerar Setup A/B,
publicar B no canal `internal` do storage QA e executar o roteiro instalado.
O candidato atual não serve como Release A desse ensaio. Manter **NO-GO** até
que instalação, ativação, Empresa, Admin Lock, Recovery, update A→B e
preservação de SQLite/DPAPI passem na VM, seguidos dos testes finais verdes.
