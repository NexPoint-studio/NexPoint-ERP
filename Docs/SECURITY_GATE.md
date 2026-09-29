# Security Gate / Red Team — planejamento do próximo ciclo

**Estado: planejado, não iniciado.** Esta página acompanha o encerramento formal
do deploy em [PROD_BASELINE.md](PROD_BASELINE.md). Não autoriza execução de
ferramentas, ataques, mudanças de configuração ou correções. O proprietário
fornecerá instrução específica para Security Discovery.

## Sequência e autorização

1. **Preparação:** fixar escopo, responsáveis, alvos de QA, dados fictícios,
   commits, versões de artefatos, identidades, limites de carga, janela e
   procedimento de interrupção/recuperação. Confirmar isolamento de PROD.
2. **Descoberta automatizada:** executar apenas as ferramentas/escopos aprovados,
   inicialmente sobre fonte e artefatos isolados e serviços QA.
3. **Descoberta manual e arquitetural:** examinar fronteiras e contratos em QA,
   com os mesmos limites e evidências reproduzíveis.
4. **Consolidação:** deduplicar, reproduzir e classificar evidências e impactos;
   separar hipóteses, falsos positivos e descobertas confirmadas.
5. **Remediação, em tarefa separada:** após consolidar a descoberta, autorizar
   priorização, correções e testes de regressão.
6. **Reteste em QA:** repetir a reprodução e verificar regressões; registrar
   evidência para fechamento ou reabertura de cada finding.
7. **Smoke/security verification em PROD:** somente após os gates anteriores e
   autorização, não destrutivo, com contas/fluxos aprovados e limites explícitos.

**Security Discovery ≠ Security Remediation.** Durante discovery, a sequência é
achar → documentar → reproduzir → classificar evidência → continuar auditoria.
O scanner não autoriza correção imediata. Preservar evidência e a visão do
conjunto antes de alterar o comportamento. Uma situação que demande interrupção
deve ser comunicada ao proprietário, sem transformar descoberta em remediação
silenciosa.

**QA antes de PROD:** ferramentas e ataques primeiro em QA; descoberta invasiva
somente em QA; corrigir em fase separada e retestar QA antes de qualquer smoke
PROD. Produção nunca será laboratório ofensivo. Sem fault injection, reset,
seed destrutivo, exploração destrutiva ou alteração de dados reais para testes.

## Inventário de superfícies — não testado nesta etapa

| Grupo | Superfícies futuras | Referências do repositório |
| --- | --- | --- |
| A. Control Center Web | Login/logout, sessão e versão da credencial, cookies, CSRF, origin, headers, rate limiting, autorização, platform roles/grants, páginas administrativas, Nexa, `/health`, `/health/dependencies` e APIs | `control_center/web.py`, `request_security.py`, `security.py`, `config.py`, templates/static |
| B. Supabase | RLS, RPCs, service credentials, grants, tenant isolation, exposição Data API, Edge Functions e contratos de requisição/resposta | Migration PROD, `control_center/supabase_repository.py`, `supabase/functions/` |
| C. ERP Desktop | Autenticação, remembered session, Admin Lock, SQLite, DPAPI, filesystem, backup/restore, API local e pywebview | `app/routes/auth.py`, `app/services/remember_sessions.py`, `admin_lock.py`, `system_maintenance.py`, `app/core/installation_identity.py`, `run_desktop.py` |
| D. Sync | Outbox, ACK, retry, leases, idempotência, replay, duplicatas, transição offline/online e retenção | `app/repositories/sync.py`, `app/services/sync_engine.py`, `sync_remote.py`, `supabase/functions/erp-sync/` |
| E. Support/Admin Recovery | Autorização, expiração, uso único, vínculo ao solicitante/tenant/instalação/chamado, consumo remoto e alteração local do cadeado | `app/services/support_tickets.py`, `admin_lock.py`, `admin_recovery_remote.py`, `supabase/functions/erp-admin-recovery/`, rotas do painel |
| F. Nexa | Bridge auth, sanitização de contexto, mensagens/histórico, permissões das Tools, provider secrets, autenticidade de resposta e isolamento de falha | `app/services/nexa_adapter.py`, `app/routes/nexa.py`, integração no painel; `erp-chat` no repositório Nexa |
| G. Supply chain | Dependências Python e Deno, locks, imagem base Docker, GitHub, scripts de build, artefatos Windows/container, credenciais de publicação e secrets no histórico | `pyproject.toml`, `requirements/`, `supabase/functions/deno.lock`, `Dockerfile`, `scripts/build_windows_prod.ps1`, histórico Git |

Inventário de RPCs de interesse: `erp_ingest_sync_batch`,
`np_admin_provision_installation`, `np_admin_rotate_installation_credential`,
`np_admin_revoke_installation_credential`, `np_authorize_nexa_request`,
`nexa_claim_erp_nonce`, `np_admin_authorize_reset`,
`np_admin_get_reset_authorization`, `np_admin_consume_reset`,
`np_erp_access_reset_authorization` e `np_admin_prune_expired_telemetry`.
É uma lista para delimitar a auditoria futura, não uma alegação de exposição ou
vulnerabilidade. O estado remoto de RLS/grants não foi consultado.

O código Nexa é uma dependência externa a este checkout; a preparação deverá
identificar seu repositório/commit e obter o escopo autorizado. Não aplicar
migrations de outro projeto ao Supabase ERP para facilitar testes.

## Matriz de ferramentas futuras

Matriz solicitada pelo proprietário; seleção, versões, compatibilidade e regras
de execução serão definidas na preparação. Nada foi instalado ou executado a
partir desta lista, e não se afirma cobertura completa por qualquer ferramenta.

| Categoria | Ferramenta | Finalidade planejada e alvo inicial |
| --- | --- | --- |
| SAST | CodeQL | Análise de fluxo de dados/código nas linguagens suportadas do checkout aprovado |
| SAST | Semgrep | Regras estáticas e padrões do código/contratos do projeto |
| SAST | Bandit | Revisão automatizada de padrões de segurança em Python |
| Dependências | pip-audit | Inventário e avisos conhecidos para dependências Python/locks, em ambiente isolado |
| Supply chain | Trivy | Imagem Docker, dependências e configuração de artefatos aprovados |
| Secrets | Gitleaks | Padrões de credenciais no checkout/histórico delimitado; relatórios sem valores |
| Secrets | TruffleHog | Descoberta de material de credencial no escopo aprovado; não presumir autorização para verificar chaves reais em serviços externos |
| DAST | OWASP ZAP | Navegação e testes web autenticados sobre QA, com escopo e limites de escrita |
| DAST | Nuclei | Templates previamente revisados e limitados aos serviços QA autorizados |
| API fuzzing | Schemathesis | Geração de casos a partir dos contratos/OpenAPI, com dados descartáveis |
| Propriedades | Hypothesis | Propriedades e combinações de entradas dos contratos quando adequado |
| Testes web manuais | Burp Suite | Inspeção e manipulação controlada de requests/sessões QA |
| Banco | pgTAP | Asserções de schema, privilégios e contratos em PostgreSQL QA |
| Banco | Testes específicos de RLS/tenant isolation | Matriz de papéis/tenants e negações esperadas em projeto QA isolado |
| Rede | Wireshark | Captura delimitada do tráfego do laboratório, com retenção e redação da evidência |
| Rede | mitmproxy | Inspeção controlada do protocolo em QA; certificados de teste somente no laboratório |
| Desktop | Sysinternals Procmon | Acesso do processo a arquivos/registro e comportamento do pacote Windows QA |
| Desktop/rede | TCPView | Conexões, processos e portas usadas no laboratório |
| Checklist | OWASP ASVS | Organizar requisitos/controles aplicáveis; fixar versão na preparação |
| Checklist | OWASP WSTG | Organizar casos e procedimentos de teste web; fixar versão na preparação |

Nenhum scanner dessa matriz foi usado no encerramento documental. A única
verificação de conteúdo permitida nesta etapa é o secret scan de release já
existente, restrito aos arquivos candidatos ao Git e sem leitura de cofres.

## Evidências e findings futuros

Usar [SECURITY_FINDING_TEMPLATE.md](SECURITY_FINDING_TEMPLATE.md). O template está
vazio; não há finding, CVE inventada ou severidade atribuída nesta baseline.

- Registrar versão/commit do alvo, ambiente, ferramenta e condições necessárias
  para reproduzir. Uma saída de scanner sozinha não equivale a confirmação.
- Manter evidências redigidas, com acesso restrito; nunca valores de secrets,
  cookies, tokens, senhas ou dados pessoais reais em Git, tickets públicos ou logs.
- Classificar a evidência como hipótese, reproduzida ou não reproduzida; documentar
  limitações. A severidade depende de descoberta e impacto demonstrados.
- Registrar falso positivo/deduplicação sem apagar o rastro que permite revisar
  a decisão. Remediação e reteste terão histórico próprio.
- Preservar o ponto de partida antes de corrigir. Nenhuma alteração automática
  de dependências, runtime, RLS, headers, secrets ou deploy pertence à descoberta.

## Checklist de entrada da futura auditoria

- [ ] Instrução específica do proprietário recebida.
- [ ] Baseline documental e commits/artefatos dos alvos registrados.
- [ ] Escopo ERP/Control Center/Supabase/Nexa e terceiros delimitado.
- [ ] QA, dados fictícios, contas/tenants e isolamento de PROD confirmados.
- [ ] Ferramentas, versões, limites, janela e critérios de interrupção definidos.
- [ ] Repositório de evidências redigidas e responsáveis definidos.
- [ ] Processo de consolidação e fase separada de correções aceitos.

Esta tarefa encerra-se na documentação. Nenhum item acima foi executado.
