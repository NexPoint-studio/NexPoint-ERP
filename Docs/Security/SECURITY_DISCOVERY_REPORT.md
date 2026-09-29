# Security Discovery — NexPoint ERP

**Rodada local concluída com limitações explícitas; não é aprovação de segurança de PROD.**
Data: 29/09/2026. Nenhuma vulnerabilidade corrigida, dependência do produto atualizada,
secret rotacionado, dado real alterado ou deploy executado.

## Baseline, autorização e preservação

- ERP: branch `main`, baseline `b09c48e2f5d6e7ecfb9d1aef91ef95753cf1f2c6`;
  último código funcional anterior `37f18e531488590b369a9ab30b9c7be321ec33b7`.
- Nexa: cópia da árvore rastreada de `f1b8df84b0acd1684f5bac65a35941b860558641`.
  Checkout Nexa permaneceu limpo e não recebeu alterações.
- Contexto lido: PROD_BASELINE, SECURITY_GATE e SECURITY_FINDING_TEMPLATE. O gate
  era planejamento; esta execução decorre da autorização posterior de discovery do proprietário.
- Fonte ERP exportada por Git archive. `.env`, cofres reais, login do proprietário,
  bancos locais operacionais e dados de clientes não foram usados nos fixtures.
- QA web 127.0.0.1:18771; ZAP 127.0.0.1:18080; PostgreSQL descartável sem portas.
  Containers existentes de outros projetos não receberam migrations/testes. Iniciar
  Docker Desktop também reativou containers locais preexistentes por política de restart;
  eles foram deixados intactos. Somente o container exclusivo da auditoria foi parado.
- Downloads de ferramentas/advisories usaram fontes oficiais; nenhum scanner foi
  direcionado ao Render/Supabase PROD ou a serviço de terceiro como alvo.

## Resultado executivo

| Classe dos registros SD | Quantidade |
| --- | ---: |
| CONFIRMED | 4 |
| LIKELY | 0 |
| NEEDS_VALIDATION | 3 |
| Total OPEN | 7 |

| Severidade técnica atribuída | Quantidade |
| --- | ---: |
| CRITICAL | 0 |
| HIGH | 0 |
| MEDIUM | 4 |
| LOW | 1 |
| INFO | 2 |

**Confirmados:** replay de cookie após logout (SD-001), custo desproporcional da
sanitização antes do corte (SD-002), perfil de navegador preservado no histórico
Git (SD-003) e erro 500 por CSRF não ASCII (SD-007).

**Ainda exigem validação:** candidatos históricos a secrets (SD-004), alcançabilidade
dos advisories da imagem Docker (SD-005) e aplicabilidade do advisory do lock de
build ao fluxo Windows (SD-006). Classificação, pré-condições, severidade,
reprodução e limites: [SECURITY_FINDINGS.md](SECURITY_FINDINGS.md).

Há **22 grupos de triagem contextual/FALSE_POSITIVE**, incluindo um grupo final
de hashes Git detectados como possível token pelo Gitleaks. Essa contagem é por
grupo, não por ocorrência. Não confundir os 258 alertas SCA da imagem com 258
vulnerabilidades exploráveis confirmadas do ERP.

## Resultado por superfície

| Superfície | Cobertura e resultado | Findings |
| --- | --- | --- |
| Control Center | 114 regressões passam; 135 checks HTTP com status esperados, sem vazamento de tenant B ou HTML cru na amostra; sessão/CSRF adicional encontrou duas falhas; sanitizador encontrado via SAST/reprodução | SD-001, SD-002, SD-007 |
| Supabase/RLS | Migração original em Postgres 17.11; 21 tabelas ENABLE/FORCE RLS, grants/RPCs/roles e contrato upstream; 11 asserções adicionais passam. Sem bypass reproduzido na amostra | Nenhum confirmado; stack Data API não verificada |
| ERP Desktop | Auth/Admin Lock/remembered sessions/DPAPI/sanitização/backup em snapshot; 138 de 139 regressões passam. Um cenário de restore com arquivo em uso falha | Limitação de teste de restore, não tomada de conta comprovada |
| Sync/Outbox/ACK | 36 testes de Sync/Recovery passam, 8 Edge ERP passam; ACK/idempotência/nonce/escopo/retention no contrato SQL | Nenhum confirmado; transporte cloud não exercitado |
| Nexa | ERP bridge nos testes desktop + 166 testes Nexa (26 steps), HMAC, replay, snapshots, escopo, limites e tool policy; Semgrep 1 alerta CORS triado como FP | Nenhum bypass confirmado; LLM/provider real não chamado |
| Admin Recovery | SQL + testes Python, oito negações explícitas, dois clientes concorrentes com apenas um consumo e rejeição de reuse | Nenhum bypass confirmado nessa amostra |
| Supply Chain/Git | pip-audit, Trivy, CodeQL, Bandit, Semgrep, Gitleaks/TruffleHog árvore e histórico | SD-003/004/005/006 |
| Network | TCPView por PID QA somente loopback; revisão de HTTPS/TLS/redirects e mocks dos transportes | Nenhum vazamento confirmado; TLS/WebView2 reais não capturados |
| Backup/Restore | Rejeitados truncamento, view, trigger, schema futuro, FK inválida e download adulterado; restore offline separado completa com integridade | Identidade cruzada/ACL real não certificadas |

## Evidência de testes

| Execução | Resultado | Evidência local em artifacts/security |
| --- | --- | --- |
| Python Sync/Recovery | 36 pass | sync/python_focused.xml |
| Python Control Center | 114 pass | webregressions/regressions.xml |
| Python Desktop/Nexa/backup | 138 pass, 1 fail | desktop/regressions.xml |
| Trace da falha de restore | PermissionError, WinError 5, linha 867 | desktop/restore-exceptions.json |
| Restore offline em novo processo | COMPLETED, integrity_check ok, foreign_key_check vazio | desktop/backup-cases.json |
| SQL pgTAP existente | 1 umbrella com múltiplas asserções DO, rollback | supabase/pgtap.log |
| SQL pgTAP adicional | 11 pass, rollback | supabase/discovery.log |
| Recovery adicional | 10 cenários, duas sessões concorrentes, uso único | recovery/cases.json |
| Deno Edge ERP | 8 pass | supabase/deno.log |
| Deno Nexa | 166 pass, 26 steps, 0 fail | nexa/tests.log |
| HTTP manual automatizado | 135 registros, nenhuma divergência do oracle inicial | web/adversarial.json |
| Fuzz alinhado ao runtime | 27 operações, 258 exemplos, 31 erros 500 da mesma causa CSRF e 1 esperado 503 | schemathesis/results.json |
| Hypothesis origin | 350 + 150 exemplos | schemathesis/results.json |
| ZAP | 15 páginas autenticadas, 5 alertas informativos | zap/results-redacted.json |
| Nuclei | 10 observações de headers, dois templates com erro | nuclei/redacted.json |

Não somar asserções/casos/steps como se fossem unidades equivalentes. A falha do
teste original permanece registrada; o sucesso do processo separado não a apaga.
Não foi executada a suíte funcional completa de todos os módulos do ERP, pois
nenhum runtime foi modificado e o alvo desta rodada é segurança.

## Inventory e configuração

Python/FastAPI/Jinja2/SQLAlchemy/SQLite no ERP e Control Center; pywebview no launcher
Windows, sem `js_api` customizado e com debug desabilitado no fluxo examinado.
Backend PROD do painel usa Supabase; Edge/RPCs para sync, recovery e Nexa.
DPAPI protege credencial da instalação no usuário Windows atual; teste real de DPAPI
usou material gerado e arquivo exclusivo de QA, nunca o cofre instalado.

Dockerfile executa como UID/GID 10001, fonte com permissões somente leitura, sem
logs de acesso ou cabeçalho de servidor no CMD. Não declara HEALTHCHECK próprio,
mas `render.yaml` configura `/health`, plano free, storage Supabase, secrets
`sync: false` e sessão `generateValue: true`; QA/seed desativados. Proxy trust `*`
depende do ingress de confiança: simulado nas regressões, topologia cloud não validada.
Nenhuma configuração Render foi alterada. Flags de filesystem/capabilities do
deploy real não foram consultadas; usuário non-root não prova todas essas restrições.

Comunicações previstas no código: API Supabase, ponte Nexa e providers configurados
na Nexa. TLS é obrigatório nos clientes PROD; sync/recovery rejeitam redirects.
Não houve observação de destinos de uma instalação real nesta rodada. TCPView
da própria QA não é prova de ausência de tráfego inesperado no pacote Windows.

## Supply chain e secrets

Runtime lock: zero avisos conhecidos pelo pip-audit na data do scan. Build lock:
um advisory único de setuptools. Imagem local: 258 pares pacote/versão/advisory,
123 IDs distintos; versão/fix/status/fonte completos em
[DEPENDENCY_ALERTS.csv](DEPENDENCY_ALERTS.csv). A imagem LIVE não foi inspecionada.

Gitleaks: árvore inicial 7 alertas de fixtures/exemplos; histórico 864 ocorrências
em 22 commits, majoritariamente artefatos de perfil/extensões de navegador.
TruffleHog: árvore 1 URI de fixture; história a mesma fixture e 1 NpmToken candidato.
Não se executou verificação de validade contra provedores. Valores detectados não
constam nos relatórios; somente tipos, locais e fingerprints.

Revisão dos candidatos finais aplica regras do scanner de release sem abrir `.env`
ou cofres reais. Gitleaks final encontrou as mesmas 7 fixtures e 3 hashes de commits
descritos em SECURITY_FINDINGS; estes últimos foram conferidos como objetos Git,
não tratados como credenciais nem ocultados por alteração das regras.

## Cobertura e limitações

[ASVS/WSTG](SECURITY_ASVS_MATRIX.md) distingue tested, partially tested, not tested
e not applicable. Não é aprovação ASVS L1/L2/L3. As principais limitações são:

- SQL QA usa roles/auth.jwt mínimos, não a stack Supabase/PostgREST completa;
  não certifica a configuração efetiva de RLS/grants em PROD.
- HTTPS em TestClient é simulação de flags/origin; sem análise de certificado,
  handshake, proxy real ou TLS downgrade.
- Procmon foi disponibilizado, mas não capturou eventos globais do computador.
  Burp/mitmproxy/tshark não foram necessários para duplicar a cobertura local obtida;
  a cobertura gráfica/TLS que faltou continua explicitamente parcial.
- Nexa usa providers/transportes mockados: limites de ferramentas/contexto testados,
  resistência comportamental de modelo real a prompt injection não certificada.
- DPAPI/ACL só com fixtures do usuário atual; isolamento entre contas Windows,
  memória/clipboard e filesystem operacional não inspecionados.
- Backup com tabela extra/nome de empresa diferente passa no validador de schema;
  tenant/installation/DPAPI cruzados não testados de ponta a ponta.
- SAST não é prova de ausência de bugs; CodeQL somente Python, Semgrep em JS/TS;
  2 templates Nuclei falharam. Advisories exigem revisão de alcançabilidade individual.
- Hashes e versões fixam a observação, mas ferramentas/regras/base Docker/advisories
  podem mudar. Resultados de novas rodadas não devem substituir estes silenciosamente.

## Entrega e Git

Relatórios: este documento, [findings](SECURITY_FINDINGS.md),
[ferramentas](SECURITY_TOOL_MATRIX.md), [ASVS](SECURITY_ASVS_MATRIX.md).
Anexos sanitizados: SAST_ALERTS.csv, DEPENDENCY_ALERTS.csv, SECRET_LOCATIONS.csv,
EVIDENCE_MANIFEST.csv. Scripts: [scripts/security](../../scripts/security/README.md).
Evidências brutas, ferramentas, bancos e credenciais fictícias permanecem ignorados.

Somente TODO, documentação e scripts de auditoria são candidatos a commit.
Nenhum arquivo de produto, migration, lock, Dockerfile ou render.yaml foi modificado.
Servidores QA/ZAP e container de banco exclusivo foram encerrados; snapshots e
evidências locais preservados. Não há push/deploy nesta rodada: divulgação pública
dos findings exige decisão própria, mesmo com valores sensíveis redigidos.

**Discovery encerrada para esta rodada. Todos os SD continuam OPEN. Nenhuma
vulnerabilidade foi corrigida. Remediação permanece tarefa separada.**
