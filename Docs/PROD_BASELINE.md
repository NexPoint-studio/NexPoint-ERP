# Baseline PROD — encerramento da implantação

## Identificação e alcance da evidência

Baseline documental de **29/09/2026**, anterior ao Security Gate. A implantação
está encerrada operacionalmente conforme confirmação do proprietário. Isso não
constitui certificação de segurança nem resultado de pentest.

| Referência | Estado registrado |
| --- | --- |
| Projeto / checkout | NexPoint ERP; `D:\NexStudio\sistema ERP` |
| Repositório / branch | `NexPoint-studio/NexPoint-ERP`; `main` |
| Base de código inspecionada | `37f18e531488590b369a9ab30b9c7be321ec33b7` |
| Identidade operacional declarada | tenant `nexpoint`; installation `nexpoint-pc-01`; platform_admin `nexpoint-admin` |
| Supabase PROD | projeto `scfncgaiovztrbgrcvkt` |
| Control Center PROD | serviço Render `nexpoint-erp-control-center` |
| URL pública | `https://nexpoint-erp-control-center.onrender.com` |
| Persistência do painel | Supabase/PostgreSQL; perfil `production`, storage `supabase` |
| Blueprint versionado | um Web Service Docker `plan: free`, uma instância, `autoDeploy: false`, `/health`; sem banco, disco, worker, cron ou Key Value Render declarado |

Os rótulos tenant/installation acima não são apresentados como UUIDs internos.
Não foram consultados registros ou credenciais de produção para produzi-los.

Fontes de evidência:

- **Proprietário:** confirmações operacionais abaixo, aceitas nesta etapa.
- **Repositório:** inspeção não destrutiva do código/configuração/documentação
  versionados; comprova contratos implementados, não o estado de cada recurso remoto.
- **Histórico da tarefa:** correção de origem, validação anterior do login e
  redefinição controlada da credencial; não repetidas nesta etapa.

O commit desta documentação identifica o fechamento formal no Git; o SHA acima
fixa a base funcional anterior. Não se afirma um novo deploy, digest de imagem,
versão de pacote Windows instalada ou versão remota das funções a partir desse
SHA. Esses identificadores de artefatos não foram fornecidos nesta tarefa e
deverão compor o registro de alvos antes da auditoria. Não há pendência de login.

## Aceite operacional confirmado pelo proprietário

| Camada | Validações já confirmadas |
| --- | --- |
| ERP desktop PROD | Provisionamento e instalação real; banco PROD separado de DEV; inicialização e login reais; sessão persistente; Admin Lock separado |
| Operação e conectividade | Sync/ACK; offline/reconexão; heartbeat, health e observabilidade |
| Integrações e manutenção | Nexa; suporte; Admin Recovery; backup/restore; DPAPI operacional |
| Control Center | Publicado e acessível externamente por HTTPS; login PROD e página autenticada abertos; ambiente exibido como PROD |
| Mobile | Acesso pelo navegador do celular e interface utilizável |

**Concluída:** “Confirmar login publicado após deploy”. A redefinição controlada
da credencial administrativa foi uma operação anterior, com hash persistido e
autenticação validada; nenhum valor secreto integra esta baseline. Nesta tarefa
não houve novo login, revalidação remota, reset, deploy ou teste operacional.

## Componentes e localização do estado

| Componente | Arquitetura atual e responsabilidade | Fonte versionada |
| --- | --- | --- |
| ERP Desktop | Windows, pywebview, API FastAPI em loopback, Jinja2/JS/CSS locais; SQLAlchemy e SQLite operacional; offline-first | `run_desktop.py`, `app/main.py`, `app/core/database.py` |
| Persistência local | Clientes, notas, pagamentos, Caixa, permissões, sessão persistente e Outbox; observabilidade em SQLite separado; backups locais | `app/models/`, `app/observability/store.py`, `app/services/system_maintenance.py` |
| Identidade de instalação | Credencial específica protegida por DPAPI da conta Windows; dados persistentes PROD fora do checkout | `app/core/installation_identity.py`, `app/core/config.py` |
| Cloud | Supabase PROD/PostgreSQL; Data API, RPCs e Edge Functions; plano de controle e recebimento dos envelopes previstos | `supabase/migrations/20260920010000_prod_cloud_foundation.sql`, `supabase/functions/` |
| Control Center | FastAPI no Docker/Render, HTTPS público, autenticação interna `platform_admin` e papel restrito `nexpoint_control_admin`; `SupabaseControlCenterRepository` em PROD | `control_center/web.py`, `config.py`, `supabase_repository.py`, `Dockerfile`, `render.yaml` |
| Nexa | Backend de assistência separado; `erp-chat` pertence ao repositório Nexa e integra o mesmo projeto Supabase do ERP em PROD; bridges do ERP e do Control Center | `app/services/nexa_adapter.py`, `Docs/INTEGRACAO_NEXA.md`, `Docs/PROD_DEPLOYMENT.md` |
| GitHub e distribuição | Fonte em `main`, locks de dependências, build Windows e definição da imagem/Blueprint Render; publicação controlada | `requirements/`, `scripts/build_windows_prod.ps1`, `Dockerfile`, `render.yaml` |

O painel web/mobile é separado do ERP operacional. Não acessa diretamente o
SQLite do cliente. Supabase não é uma réplica integral desse SQLite nem uma
fonte de reconstrução de clientes, notas ou ledger financeiro. LOCAL/DEV conserva
o sidecar `LocalControlCenterRepository`; esse modo não descreve o painel PROD.

O provisionamento é um fluxo administrativo externo ao runtime web:
`scripts/provision_prod.py` estabelece identidade e credencial; os scripts
`create_prod_database.py` e `install_prod_database.py` tratam a preparação do
banco operacional. Esses scripts foram apenas inventariados, não executados.

## Fluxos de dados atuais

1. **Operação local:** usuário → interface local → rotas/autorização → serviços
   transacionais → SQLAlchemy/SQLite. Dados comerciais permanecem locais.
2. **Sync:** evento permitido (`heartbeat`, `health`, `diagnostic_event`, `risk`,
   `incident`, `support_ticket`) → Outbox durável → `SyncWorker`/`SupabaseSyncRemote`
   → HTTPS `erp-sync` → validação do contrato → RPC `erp_ingest_sync_batch`
   → persistência/ACK → conferência de identidade, chave e versão → `synced`.
   A requisição autentica a instalação por credencial e HMAC, com timestamp e
   nonce. Retry, lease e idempotência preservam itens sem ACK; não se envia um dump do banco.
3. **Observabilidade:** ERP → heartbeat/health e diagnósticos sanitizados,
   fingerprints, versão/build e IDs técnicos → Outbox → Supabase → consultas do
   Control Center. O sidecar local conserva os eventos segundo seu contrato.
4. **Suporte:** solicitação local com contexto técnico permitido → Outbox →
   `erp-sync`/backend → chamado no Supabase → fila do Control Center. Texto e
   metadados passam pela sanitização; notas internas não são listadas ao cliente.
5. **Admin Recovery:** Proprietário solicita recuperação do Admin Lock →
   Outbox/backend → chamado vinculado → `platform_admin` autoriza no painel →
   autorização temporária → ERP consulta/consome pelo contrato
   `erp-admin-recovery` → nova senha definida localmente. Vínculos de tenant,
   instalação e solicitante, expiração e uso único pertencem ao contrato; o
   painel não recebe a nova senha do cadeado.
6. **Nexa no ERP:** navegador local → backend ERP autenticado → contexto/Tools
   sanitizados → bridge assinada → Nexa/provider → resposta autenticada → ERP.
   Em PROD a autenticação utiliza a credencial de instalação protegida por DPAPI.
7. **Nexa no painel:** navegador → Control Center autorizado → contexto limitado
   ao escopo do ator → bridge com segredo server-side → Nexa → resposta ao painel.
   A assistência é somente leitura; sua indisponibilidade não interrompe o ERP.
8. **Acesso web:** browser HTTPS → proxy Render → FastAPI → autenticação por hash
   no Supabase → sessão assinada → páginas e operações autorizadas. A sessão
   depende da versão da credencial; troca do hash invalida cookies anteriores.
9. **Backup/restore:** SQLite local → snapshot consistente e manifesto → backup;
   restauração preparada em candidato e aplicada antes do engine no próximo
   início, com rollback. Supabase tem domínio de backup separado; configuração
   efetiva de PITR/RPO/RTO não foi inspecionada nesta etapa.
10. **Release:** GitHub/main → build Docker/Blueprint → promoção controlada no
    Render; build Windows tem processo próprio. Um push documental não equivale
    a deploy e não modifica a instalação operacional.

## Fronteiras de confiança

| ID | Fronteira | Dados/capacidades que atravessam; contrato a examinar futuramente |
| --- | --- | --- |
| TB1 | Usuário local ↔ ERP Desktop | Login, sessão, formulários e permissões; Admin Lock separado |
| TB2 | ERP Desktop ↔ SQLite local | Dados comerciais, ledger, Outbox e hashes; transações, caminhos e acesso ao arquivo |
| TB3 | ERP Desktop ↔ DPAPI | Proteção/recuperação da credencial na conta Windows; conteúdo não destinado à UI |
| TB4 | ERP Desktop ↔ Internet | Saída HTTPS, indisponibilidade e reconexão; nenhuma exposição pública da API local pretendida |
| TB5 | ERP ↔ Supabase PROD | Identidade da instalação, envelopes, ACK e recuperação; HMAC, nonce, timestamp e vínculo de ambiente |
| TB6 | Browser ↔ Render Control Center | TLS, proxy, login, cookies, CSRF, origem e limites HTTP |
| TB7 | Render ↔ Supabase | Credencial server-side, Data API/RPC, estado de autenticação e plano de controle |
| TB8 | ERP/Control Center ↔ Nexa | Autenticação da ponte, contexto permitido, Tools somente leitura e resposta autenticada |
| TB9 | GitHub ↔ Render deployment | Código/locks/Blueprint, build e promoção da imagem; credenciais de publicação |
| TB10 | platform_admin ↔ operações privilegiadas do Control Center | Escopo global, autorizações de recuperação, auditoria e separação dos demais papéis |

Estas fronteiras são um inventário para threat modeling; não foram atacadas ou
certificadas nesta tarefa.

## Segredos: tipos, custodiante e armazenamento esperado

Somente nomes/tipos são registrados. O armazenamento esperado é um contrato a
conferir posteriormente, não uma inspeção de cofres ou arquivos reais.

| Tipo/nome | Uso e quem pode acessar | Cliente ou servidor; armazenamento esperado |
| --- | --- | --- |
| `CONTROL_CENTER_SUPABASE_SERVICE_ROLE_KEY` / `SUPABASE_SERVICE_ROLE_KEY` | Runtime Control Center, Edge Functions e provisionador autorizado | Somente server-side/operador de provisionamento; cofre do ambiente, nunca pacote ERP/browser |
| Installation credential | Backend da instalação e verificação remota de sync/Nexa/recovery | Cliente Windows via DPAPI vinculado à conta; backend mantém material verificador previsto no contrato |
| `CONTROL_CENTER_SESSION_SECRET` | Processo web assina sessões | Somente servidor; secret do Render (`generateValue` no Blueprint) |
| Segredo da sessão ERP | Backend local assina sessões | Em PROD derivado da credencial DPAPI; segredo de desenvolvimento separado |
| `CONTROL_CENTER_NEXA_BRIDGE_SECRET` | Backend do painel e Nexa autenticam a ponte | Somente servidores; cofres Render/Edge, distinto do segredo de sessão |
| `NEXA_ERP_BRIDGE_SECRET` | Ponte compartilhada nos perfis previstos e endpoint Nexa | Em LOCAL/DEV, backend local e Nexa; em PROD ERP usa credencial de instalação. Cofre/arquivo ignorado conforme ambiente, nunca frontend |
| `GROQ_API_KEY`, `GEMINI_API_KEY` | Nexa acessa providers configurados | Somente backend Nexa; cofre Edge/provider. Inventário conceitual não atesta providers ativos |
| Senha platform_admin | Administrador e autenticação interna | Hash scrypt no Supabase; valor transitório no formulário e custódia do proprietário, nunca configuração bootstrap do web PROD |
| Senha de login ERP / Admin Lock | Usuário/Proprietário e backend local, domínios separados | Hash no SQLite; nenhuma senha restaurada pelo “manter conectado” |
| Cookies/session tokens e CSRF | Browser correspondente e backend validador | Cookies/estado de sessão segundo o contrato; remembered session ERP opaca, com hash persistido; não registrar em logs |
| Recovery authorization verifier | Backend de autorização/consumo | Material verificador em hash conforme ambiente: SHA-256 no contrato Supabase PROD e scrypt no sidecar LOCAL; temporário, uso único e vínculos explícitos; não mostrado ao cliente |
| Credenciais GitHub/deploy | Operadores e automação de publicação autorizados | Cofre/agente de autenticação; fora do código, imagens e artefatos cliente |

Nenhum secret real foi consultado, copiado ou incluído nesta tarefa.

## Classificação conceitual de dados

Classificação documental, sem mudança de código ou política de retenção:

| Classe | Exemplos | Tratamento esperado |
| --- | --- | --- |
| PUBLIC | Documentação destinada ao público, assets da interface, URL pública de serviço | Pode ser divulgado quando aprovado; não inclui detalhes internos por inferência |
| INTERNAL | Versão/build, arquitetura, identificadores técnicos, saúde agregada, configuração técnica não secreta | Equipe/instalação autorizada; minimizar na resposta pública |
| CONFIDENTIAL | Dados de cliente, notas, pagamentos, Caixa, configuração empresarial, tickets, diagnósticos, logs/observabilidade por instalação | Escopo do usuário/tenant, sanitização e retenção contratual; não copiar dados reais para QA |
| SECRET | Senhas, hashes de credenciais, cookies/tokens, provider keys, service_role, installation credentials, segredos HMAC e verificadores de recuperação | Acesso mínimo, armazenamento protegido ou hash conforme finalidade; nunca logs/documentação/Git |

Logs e telemetria não se tornam públicos por estarem sanitizados. Quando um
artefato reúne classes diferentes, aplica-se a classe mais restritiva. Hash de
credencial é SECRET; fingerprint diagnóstico não é hash de credencial.

## Ambientes e limites operacionais

| Ambiente | Função e isolamento |
| --- | --- |
| LOCAL | Execução loopback/sidecars e dados próprios de desenvolvimento; não sinônimo de PROD só porque ambos rodam em Windows |
| DEV | Desenvolvimento com dados fictícios e infraestrutura/credenciais separadas; não implica projeto remoto DEV provisionado |
| QA | Ambiente isolado para futuros fuzzing, fault injection, scanners, replay, manipulação de requests, isolamento e testes destrutivos controlados; preparar alvos e dados antes de autorizar a execução |
| PROD | Dados reais e instalação operacional confirmada; sem fault injection, reset, seed destrutivo ou testes ofensivos destrutivos |

O ERP configura `LOCAL`, `QA`, `PROD`; o Control Center reconhece `local`,
`development`, `test`, `qa`, `production`. Estes nomes não comprovam que todos
os ambientes remotos estejam provisionados; a baseline confirma somente PROD
conforme o proprietário e inventaria os modos existentes no código.

Limitações conhecidas, sem classificação como vulnerabilidade:

- Render Free pode suspender por inatividade; cold start aumenta a latência
  inicial. Não é infraestrutura always-on.
- O limitador de login atual é por processo; o Blueprint mantém uma instância,
  conforme o runbook. Escala horizontal exige planejamento separado.
- `/health` é liveness local; `/health/dependencies` informa prontidão remota.
  Liveness positivo não prova disponibilidade Supabase/Nexa nem login.
- ERP continua local/offline-first; suporte sincronizado, recuperação remota e
  Nexa dependem de conectividade. Pendências permanecem na Outbox.
- Control Center é web/mobile, sem acesso direto ao SQLite operacional nem
  comandos financeiros ou execução arbitrária no cliente.
- A nuvem não substitui backup do ERP. Backup/PITR da nuvem e retenção automática
  descritos no runbook não foram comprovados por inspeção remota nesta baseline.
- Atualizador automático do ERP segue não configurado no runbook atual; não se
  presume canal de atualização ativo pela existência do painel.
- Pergunta e histórico do chat Nexa são conteúdo intencional, distinto dos
  metadados técnicos sanitizados; não recebem necessariamente a mesma redação
  automática. O contrato orienta não inserir dados sensíveis na conversa.
- Eventos de observabilidade `pending` podem exceder limites de retenção para
  preservar dados sem ACK; a manutenção do sidecar é de melhor esforço.

## Ponto de partida do próximo ciclo

O inventário de superfícies, matriz de ferramentas, política QA antes de PROD e
separação discovery/remediation estão em [SECURITY_GATE.md](SECURITY_GATE.md).
O [template de finding](SECURITY_FINDING_TEMPLATE.md) permanece vazio. Não há
findings, CVEs, severidades atribuídas ou execução de auditoria nesta etapa.

Para iniciar o ciclo seguinte, registrar o commit documental desta baseline,
o SHA funcional acima e os alvos/artefatos de QA aprovados. Divergências futuras
de versão devem ser registradas antes da descoberta. A próxima fase depende
de instrução separada do proprietário.

Referências operacionais: [deploy PROD](PROD_DEPLOYMENT.md),
[Control Center](CONTROL_CENTER.md), [observabilidade/QA](OBSERVABILIDADE_QA.md),
[Nexa](INTEGRACAO_NEXA.md), [backup/restore](BACKUP_RESTAURACAO_ATUALIZACAO.md)
e [sessão persistente](SESSAO_PERSISTENTE.md).

## Verificação do fechamento documental

- Escopo restrito a Markdown/documentação e TODO; runtime, migrations,
  Dockerfile, Blueprint e configurações executáveis preservados.
- Links locais revisados e `git diff --check` sem erros.
- Secret scan de release existente aprovado em exportação isolada do índice
  Git (330 arquivos candidatos), sem `.env` real, DPAPI ou cofres. A comparação
  opcional com valores de segredos locais não se aplica a essa exportação;
  foram usadas as regras existentes de padrões e arquivos proibidos.
- Sem testes de runtime, por não haver mudança executável; sem ferramentas da
  matriz futura, pentest, descoberta de vulnerabilidades ou correções de segurança.
- Commit/push documental encerra esta tarefa; nenhum deploy ou ciclo de Security
  Discovery é iniciado automaticamente.

## Atualização pré-release de 30/09/2026

O registro acima permanece como fotografia original. O ciclo posterior foi
autorizado separadamente e está consolidado no
[Security Retest](Security/SECURITY_RETEST_REPORT.md) e na
[RC 1.0.1rc1](RELEASE_1.0.1rc1.md).

Após gate QA e backup cifrado, Supabase `scfncgaiovztrbgrcvkt` recebeu somente
`20260929010000_platform_sessions.sql`. A nova tabela tem RLS/FORCE RLS e
permissões de servidor; as 21 tabelas/dados/funções/policies anteriores foram
comparadas e preservadas. Estado de migrations: `20260920010000` e
`20260929010000`. Nenhuma Edge Function mudou ou foi redeployada.

Render não recebeu deploy nem smoke nesta etapa. O SHA funcional documentado
`37f18e531488590b369a9ab30b9c7be321ec33b7` é a referência de rollback; não foi
reclassificado como SHA atualmente LIVE. Environment e secrets mantêm contratos.
Artefatos RC e backup estão registrados na release; não substituir esta baseline
por uma afirmação de que o novo código já está ativo em PROD.
