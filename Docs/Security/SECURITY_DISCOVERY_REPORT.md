# Security Discovery — NexPoint ERP

## Encerramento da triagem final e coverage review

29/09/2026, continuação de `277a5b293dbc83797997a9df08759d34c78a8888`.
Fonte funcional auditada permanece a mesma. Os 31 artefatos do manifesto anterior
mantêm os mesmos hashes; inventários antigos não foram substituídos. Os resultados
atuais abaixo prevalecem sobre as contagens da rodada histórica preservada adiante.

| Classe final dos registros SD | Quantidade |
| --- | ---: |
| CONFIRMED | 5 |
| LIKELY | 0 |
| FALSE_POSITIVE | 0 |
| UNRESOLVED | 2 |
| NOT_APPLICABLE | 1 |
| Total | 8 |

Severidades de todos os oito registros: **CRITICAL 0, HIGH 0, MEDIUM 4, LOW 2, INFO 2**.
Sete continuam OPEN; SD-006 foi encerrado como NOT_APPLICABLE ao build examinado,
sem alterar sua dependência. Os quatro confirmados anteriores permanecem confirmados.
Novo **SD-008/LOW**: diferença de custo do login no adaptador SQLite, comprovada em
fixture local e código; não atribuir esse resultado ao adaptador Supabase usado em PROD.

SD-004 está UNRESOLVED: candidato UUID presente no blob histórico, ausente dos arquivos
rastreados atuais, sem prova de credencial real/ativa. SD-005 está UNRESOLVED: presença
de dependências não prova uso do caminho vulnerável. **Nenhuma CVE teve aplicabilidade
explorável no ERP confirmada nesta rodada**. A revisão de fontes e módulos excluiu
cinco ocorrências no cenário observado; outras 254 permanecem UNRESOLVED. SD-006 é
NOT_APPLICABLE porque o fluxo oficial gera PyInstaller/ZIP, não sdist publicado.

Classificações e justificativas: [findings](SECURITY_FINDINGS.md) e
[dependências/secrets](SECURITY_DEPENDENCY_TRIAGE.md). Falsos positivos de scanner
são contados separadamente: SAST 140 FALSE_POSITIVE, 1 CONFIRMED e 5 UNRESOLVED;
Gitleaks 14 fixtures FALSE_POSITIVE e 857 localizações históricas UNRESOLVED;
TruffleHog 2 fixtures FALSE_POSITIVE e 1 candidato UNRESOLVED. Repetições entre
commits e ferramentas não são credenciais ou vulnerabilidades distintas.

### Verificações adicionais executadas

| Execução | Resultado e limite | Evidência sob artifacts/security/final |
| --- | --- | --- |
| Autenticação/sessão/roles/grants/objetos/mass assignment | 149/149 verificações de comportamento esperado + 1 registro de tempos; inclui SD-001 reproduzido, portanto não significa 149 controles seguros | auth/run-45441f7d36/results.json |
| Data API/RPC/RLS real | 90/90 casos em Postgres 17.11 + PostgREST 16.2, rede Docker interna, sem portas publicadas; anon/auth/service_role, extras, nonce e idempotência | database/data-api.json |
| Cliente Sync com TLS real loopback | 5/5: certificado não confiável e hostname errado negados, trust QA explícito aceito, redirect recusado/destino não chamado | tls/run-e65b7955a3/results.json |
| Presença/alcançabilidade de módulos da imagem | Container read-only sem rede; fallback msgpack, ausência de _cmsgpack/setuptools de topo, versões dpkg | image-reachability.json |
| Triagem allowlist e preservação | 31/31 artefatos anteriores inalterados; saídas por ocorrência, sem valores | supply/summary.json; supply/secret-triage.json |

A primeira execução auth preservada tinha 147/149 expectativas satisfeitas. As duas
divergências eram **404 corretos para incidente selecionado fora do grant**, não bugs
do produto; corrigido somente o oracle do script e repetido com sucesso. Warning de
depreciação httpx/Starlette não impediu os testes, nem motivou atualização de runtime.
A observação temporal usou sete pares fixos de contas fictícias conhecidas, sem
busca de senha/contas externas. Os casos de limite são regressões determinísticas.

PostgREST acrescenta cobertura de protocolo real à evidência SQL prévia. Acesso global
de service_role no fixture é intencional: isolamento depende da aplicação/RPC, não
de uma promessa de RLS para esse papel privilegiado. Nenhum secret real foi utilizado.
O TLS loopback não certifica ingress Render, WebView2, Recovery ou Nexa. Containers
exclusivos foram parados; o listener TLS foi encerrado pelo harness.

### Cobertura e limites finais

[Matriz por fronteira](FINAL_COVERAGE_MATRIX.md): 39 categorias PARTIALLY_TESTED,
8 superfícies NOT_TESTED, 10 casos delimitados TESTED e 4 grupos NOT_APPLICABLE.
São unidades com sobreposição, não um percentual de conformidade. Todos os limites
estão no [registro de gaps](SECURITY_COVERAGE_GAPS.md), incluindo todas as linhas
parciais/não executadas da matriz [ASVS/WSTG](SECURITY_ASVS_MATRIX.md).

Lacunas principais: stack Supabase integrada; ACL/DPAPI entre usuários Windows;
restore entre instalações com identidade/cofre completos; pacote WebView2; ingress
TLS/proxy; memória/clipboard; provider/LLM real; capacidade/failover. O teste antigo
de restore em uso continua falhando e registrado (138/139); a causa do handle não
foi determinada. A análise estática da retenção mostra preservação deliberada de
pending além do orçamento: não houve teste de exaustão nem nova vulnerabilidade
de disponibilidade declarada sem validar alcance/impacto.

Ferramentas acumuladas: pytest/TestClient, PostgreSQL/pgTAP, PostgREST, Deno,
CodeQL, Semgrep, Bandit, pip-audit, Trivy, Gitleaks, TruffleHog, ZAP, Nuclei,
Schemathesis, Hypothesis e TCPView. Limitações específicas e alternativas constam
na [matriz de ferramentas](SECURITY_TOOL_MATRIX.md). Não foram repetidas varreduras
agressivas ou cargas. Procmon gráfico, tshark/mitmproxy/Burp não cobrem os gaps por
mera disponibilidade; o provider real e PROD permaneceram fora dos alvos.

**Nota de retenção:** JSONL TruffleHog antigo conserva SecretParts; não é seguro para
publicação mesmo com Raw redigido. A inspeção auxiliar relatou exposição inadvertida
de candidato em sua saída anterior. Nenhum valor é reproduzido nos documentos; os
originais ignorados foram preservados e a nova projeção usa campos permitidos.
Esse limite de higiene de evidência não foi ocultado nem convertido em secret ativo.

Entrega final: quatro relatórios atualizados, gaps, matriz por fronteira, triagem
detalhada, três CSVs de classificação final, manifesto final e scripts defensivos.
Somente documentação/TODO/scripts de auditoria entram no commit local. Nenhum
arquivo do produto, migration, lock, Dockerfile ou configuração de deploy foi alterado.
Secret scan dos candidatos finais: release sem ocorrências, Gitleaks 10 alertas
conhecidos (7 fixtures/exemplos e 3 objetos Git conferidos), TruffleHog 1 URI de fixture.
Nenhum novo secret identificado nos candidatos; isso não encerra SD-004 histórico.
Nenhum push, deploy, rotação de secret ou acesso invasivo a PROD. **Discovery encerrada;
remediação não iniciada.** SHA do commit é informado na entrega, sem autorreferência.

## Rodada anterior — evidência e contagens históricas preservadas

As seções abaixo descrevem a rodada registrada em 277a5b2. Os estados finais e as
novas coberturas estão acima; números antigos não devem ser usados como resultado atual.

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
