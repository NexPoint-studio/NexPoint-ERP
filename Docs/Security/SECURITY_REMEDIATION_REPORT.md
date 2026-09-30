# Security Remediation — QA/local

Marco preservado no remoto antes das alterações:
`f91edf983b151f494c113cfabe8344252c626c73`, branch `main`,
`NexPoint-studio/NexPoint-ERP`. Os relatórios de discovery permanecem como
evidência histórica; este documento registra a remediação, sem substituir suas
limitações de cobertura. Nenhuma alteração de PROD ou deploy está autorizada
nesta etapa.

## SD-001 — revogação de sessão

**REMEDIATED_VERIFIED** em QA/local; gates globais registrados ao final.
Transição: CONFIRMED → REMEDIATED_PENDING_RETEST → REMEDIATED_VERIFIED.

- Causa: cookie assinado autossuficiente; logout apagava apenas a cópia do cliente.
- Correção: token aleatório por login, somente SHA-256 persistido, consulta de
  titular/hash/expiração em cada requisição autenticada e exclusão no logout.
  SQLite e Supabase compartilham o contrato; não há cache por processo.
- Preservados: flags de cookie, CSRF, Origin, credential_version, usuário ativo,
  papel permitido e independência entre duas sessões. Cookies antigos exigem
  novo login. Expiração absoluta de oito horas; falhas de storage retornam 503.
- Arquivos: `control_center/{repository,local_repository,supabase_repository,web}.py`,
  migration `20260929010000_platform_sessions.sql` e testes correspondentes.
- Regressão antes: `run-7430d87005`, cookie reutilizado após logout retornou 200,
  falhando a expectativa 303 no baseline f91edf9. Evidências em
  `artifacts/security/remediation/tests/` (ignoradas pelo Git).
- Reteste: `run-128f8a6eac`, 62 casos passaram, incluindo dez cenários HTTP,
  dezesseis de repositório e 36 de configuração/health de produção simulada.
- PostgreSQL 17 QA descartável, sem portas publicadas, rede desconectada durante
  os testes: migrations aplicadas e `prod_foundation.sql`/`platform_sessions.sql`
  passaram. pgTAP verifica privilégios, RLS forçada, revogação, expiração,
  constraints e preservação de outra sessão. Logs em
  `artifacts/security/remediation/database/`. A primeira execução do runner
  não incluiu `extensions` no search_path do pgTAP; o runner foi corrigido e
  os dois contratos passaram sem mudança no SQL do produto.
- Regressão do componente: `run-260009c72e`, 146/146, incluindo Control Center,
  Origin, hardening, logs, health e auditoria de sessão desktop.
- Revisão estática independente: nenhuma falha introduzida comprovada.
- Implantação futura: aplicar a migration antes do código. Ela NÃO foi aplicada
  em PROD. ERP desktop/remembered sessions/Admin Lock não foram modificados.

## Limites da etapa

Correções específicas validadas em QA/local; SD-003 mantém residual histórico
para ação humana. SD-004/005 reavaliados, ainda UNRESOLVED. SD-006 continua NOT_APPLICABLE.
Resultados da suíte completa, scans, builds e registro Git constam ao final.

## SD-002 — limites antes da sanitização

**REMEDIATED_VERIFIED** em QA/local, após REMEDIATED_PENDING_RETEST.

- Causa: regex procurava o fechamento de blocos PRIVATE KEY para cada abertura
  antes de truncar a entrada; coleções também eram copiadas integralmente e o
  limite por nível permitia multiplicar trabalho com estruturas aninhadas.
- Correção em `control_center/sanitization.py`: entrada acima de 8.000 caracteres
  é ocultada antes de regex/normalização, sem cortar uma credencial ao meio.
  Abertura de chave privada, mesmo incompleta, oculta o texto inteiro. Percurso
  com islice, limites de profundidade/coleção preservados, orçamento compartilhado
  de 512 nós e 64.000 caracteres. Chaves sensíveis são verificadas antes do corte
  de exibição. `contains_secret_material` falha fechado para texto excessivo.
- Regressão: `tests/test_security_sanitizer_bounds.py`; baseline f91edf9,
  `run-95051084e4`: nove falhas e um caso normal aprovado. Versão corrigida,
  `run-261899c9f0`: 10/10. IDs curtos foram adicionados aos parâmetros porque
  nomes gerados pelo pytest excediam o limite de variável de ambiente no Windows
  no primeiro ensaio; isso foi falha do harness, não do produto.
- Cenário original repetido: nove pares tipo/tamanho do script bounded_sanitizer,
  sem rede. Texto PRIVATE KEY de 221.185 caracteres termina sem timeout; evidência
  `artifacts/security/remediation/sanitizer-timing.json`. Medições são observação,
  não SLO: o teste usa um limite amplo de dez segundos e asserções de trabalho
  limitado, sem exigir milissegundo exato.
- Fronteira compartilhada com suporte, logs, Nexa e observabilidade; builds Windows
  e Docker de validação concluídos conforme registro final, sem publicação.
- Regressão do componente: `run-5b4c05f85b`, 111/111, incluindo os dez casos
  novos e observabilidade, diagnósticos, estabilização de segurança e suporte.
- Complemento da mesma causa em `app/observability/sanitization.py`: a cópia
  local ainda executava o padrão com fechamento antes do corte. O middleware
  registra `request.url.path` como metadata.route antes do roteamento, inclusive
  em POST para `/static/`; logo o caminho de entrada integra o escopo SD-002.
  Não houve chamada contra PROD nem abertura de nova campanha de discovery.
- A cópia local agora também rejeita mais de 8.000 caracteres antes de matching,
  oculta aberturas incompletas e limita canary/coleções a profundidade 6, 512 nós
  e 64.000 caracteres por percurso compartilhado. Mantém saída padrão de 512
  caracteres, allowlist de metadados, canary fail-closed e informações normais.
  Lookup pelas chaves permitidas evita ordenar todas as chaves não confiáveis.
- `tests/test_security_observability_sanitizer_bounds.py`: baseline f91edf9,
  `run-2fc9ac3c57`, 11 falhas/1 normal aprovado; corrigido `run-5178665475`, 12/12.
  Regressões de observabilidade, diagnósticos, Nexa e ambos os sanitizadores:
  `run-e63e2081bf`, **57/57**, zero erros/skips. O padrão original de 221.185
  caracteres termina com resultado protegido nos dois entrypoints e timeout
  máximo de dez segundos por processo. Evidência: `observability-sanitizer-scope.json`.
  Os builds finais incluem este complemento no commit 209d5ea.

## Revalidação restrita de SD-004 e SD-005

SD-004 permanece **UNRESOLVED / INFO**. Fingerprint
`2f8f8034d985ceb23562971640b7a7ada4827e6becc4e22b660fc65723a60601`
reconfirmado em uma ocorrência do blob histórico; zero nos rastreados atuais.
Formato UUID junto a metadados de sincronização favorece hipótese de identificador,
mas não prova a semântica nem exclui token legado. Ambiente, proprietário,
consumidores, atividade e rotação continuam desconhecidos. Nenhum valor foi usado
contra provedor. Rotação depende de proveniência e consumidores identificados;
não está justificada nesta etapa.

SD-005 permanece **UNRESOLVED / MEDIUM provisório**. Mesma imagem original
`sha256:dedc527873c46ef28ebb391ca9e53a029b23dd51c7bb87fb30cc965fcc480ac4`,
inspecionada sem rede, read-only, sem portas/volumes. SQLite 3.40.1 está instalado.
Dois refinamentos no recorte de serving examinado:

- CVE-2025-29088: NOT_APPLICABLE; API C LOOKASIDE sem consumidor no produto,
  constante não exposta no binding observado. Reabrir ao adicionar FFI/extensão.
- CVE-2025-70873: NOT_APPLICABLE; extensão SQL zipfile ausente no módulo carregado
  e produto não carrega extensões. Não equivale à biblioteca Python zipfile.

Pré-condições documentadas pelo [SQLite upstream](https://www.sqlite.org/cves.html).
FTS5 está compilado; os demais advisories que exigem SQL arbitrário/índice malformado
continuam inconclusivos. Os cinco N/A anteriores, incluindo SD-006, permanecem.
Recorte atualizado do inventário original: **7 N/A e 252 UNRESOLVED** (259 pares);
na imagem original, 6 N/A e 252 UNRESOLVED. Não são 259 vulnerabilidades ERP
confirmadas. CSVs e evidências anteriores foram preservados: 41 hashes conferidos.
Sem mudança em dependências ou locks. Digests/pacotes das imagens de validação
foram comparados conforme gate final; esta decisão não certifica PROD.

Nota técnica completa, sem valores de secrets:
`artifacts/security/remediation/unresolved-review.md` (local, ignorada).

## SD-007 — CSRF não ASCII

**REMEDIATED_VERIFIED** em QA/local, após REMEDIATED_PENDING_RETEST.

- Causa: compare_digest sobre str aceita apenas ASCII e levantava TypeError
  diante de Unicode. O erro impedia a operação, mas escapava como HTTP 500.
- Correção mínima em `_require_csrf`, `control_center/web.py`: validar tipo,
  comprimento 32–128 e ASCII antes de comparar. Preservado compare_digest para
  tokens admissíveis, CSRF obrigatório, Origin/Referer e sessão.
- `tests/test_security_csrf_inputs.py`: null/missing, vazio, ASCII incorreto,
  Unicode, combinantes, emoji, surrogate, texto excessivo, codificações inválidas,
  token válido e comprovação de uso da comparação segura.
- Antes: `run-605ea4278a` sobre 45ce4b0, 9 falhas em 17 casos (TypeError esperado
  no código antigo). Depois: `run-1cd8468cf6`, **66/66**, incluindo regressões de
  Origin e hardening. Nenhuma asserção anterior removida.
- Script original unicode_csrf repetido sobre export corrigido: três respostas
  403, nenhuma traceback, nenhuma nota não autorizada gravada. Evidência
  `artifacts/security/remediation/csrf-original/result.json`.

## SD-003 — perfil de navegador no histórico

**CONFIRMED; prevenção futura REMEDIATED_VERIFIED; residual histórico OPEN —
AÇÃO HUMANA NECESSÁRIA.** Não equivale a remoção do histórico.

- Introduzido em `16020ca5b0d061509545fb5a06a51f3347cafdbd`, removido da árvore
  em `a960e7b6e98ce925cff3c3ef8e9ff0ad2ca7d056`. Inventário: 1.093 caminhos em
  `carcaça/Default/` e SmartScreen. Zero desses caminhos rastreados atualmente.
- Não é somente cache: contagens SQL em blobs desserializados em memória,
  sem exibir valores nem decifrar conteúdo: History com 35 URLs/3 visitas;
  Login Data com 126 registros, 37 com campo de senha cifrada não vazio;
  Network/Cookies com 60 registros cifrados; Web Data com 2 autofill e zero
  cartões; Login Data For Account vazio. Há arquivos de Sessions e LevelDB de
  Local Storage/Sync Data. Conteúdo/atividade dos tokens e sessões não determinado.
- Arquivos com histórico, cookies e credenciais cifradas não pertencem ao código;
  o histórico alcançável exige tratamento separado. Não se presumiu que criptografia
  torne segura sua divulgação. Não houve descriptografia nem uso de credenciais.
- Prevenção: `.gitignore` exclui perfis Chromium/WebView e seus nomes de dados;
  `scripts/secret_scan.py` rejeita também inclusão forçada no índice Git. Todos os
  1.093 caminhos históricos seriam bloqueados pelo guard atual.
- Regressão: `tests/test_security_browser_artifacts.py`; baseline
  `run-799acd1498`, 19 falhas esperadas/23; corrigido `run-4137ff3e23`, **28/28**
  com configuração de instalação. Teste Git usa entrada NUL para não confundir
  CRLF de pipe do Windows com parte do nome. Scanner de release: zero ocorrências.
- Ação humana: decidir remoção dos caminhos históricos citados, coordenar clones,
  forks, refs e backups; obter a proveniência dos registros e avaliar credenciais
  potencialmente ativas antes da limpeza. Reescrita exige autorização própria;
  nenhuma execução de filter-repo/BFG/force-push foi feita. SD-004 mantém a
  incerteza específica de candidato histórico, sem rotação cega.
- Evidências locais: `browser-profile-inventory.json`, `browser-profile-counts.json`
  e `browser-prevention-retest.json` em `artifacts/security/remediation/`.

## SD-008 — caminho rápido no login SQLite

**REMEDIATED_VERIFIED** em QA/local, após REMEDIATED_PENDING_RETEST.

- Causa: repositório SQLite retornava antes de verificar senha quando o usuário
  não existia/estava inativo. Usuário existente com senha errada executava scrypt.
- Correção em `control_center/local_repository.py`: dummy hash aleatório criado
  uma vez com o mecanismo vigente; uma verificação scrypt em cada tentativa
  sintaticamente admissível, antes de negar usuário inexistente/inativo. Nenhum
  sleep, mudança no hash, política de senha ou alteração do adapter Supabase.
- `tests/test_security_sqlite_login_timing.py`: conta chamadas e parâmetros
  criptográficos, valida sucesso/negação e 12 pares alternados após aquecimento.
  Banda estatística relativa ampla 0,2–5; não depende de milissegundo exato.
- Antes: `run-90bd0d4c30`, ambos os testes falham no baseline. Depois:
  `run-305a0d294a`, **64/64**, incluindo contratos de sessão/adapter e produção
  simulada. Reteste original de sete pares HTTP: todas as respostas 401,
  medianas 65,804 ms (existente/senha errada) e 65,778 ms (inexistente).
  Evidência `artifacts/security/remediation/timing-original/result.json`.
- Conclusão restrita ao SQLite local; não é promessa de tempo constante de rede,
  ausência de enumeração em todos os canais ou verificação de PROD.

## Restore — classificação e ciclo de vida do teste

A falha histórica (138/139) não era finding confirmado. O trace preservado
registrou PermissionError/WinError 5 na troca do arquivo; não identificou o
proprietário do handle. A reprodução isolada atual passou (`run-d8fdc8736e`).
O teste chamado offline mantinha TestClient/lifespan/worker ativos e apenas
descartava o pool SQLAlchemy. Esse procedimento não estabelecia a fronteira
offline documentada e permitia reabertura concorrente do SQLite.

Alterado somente `tests/test_phase_four_backup_restore.py`: encerrar TestClient,
comprovar worker parado, restaurar e usar novo TestClient com o cookie anterior
em memória. Todas as verificações de estado, auditoria, rollback e invalidação
de sessão permanecem. Módulo inteiro: **12/12**, `run-1a9b86862c`.
Classificação: ajuste de lifecycle do harness, não vulnerabilidade de produto
comprovada. Binding entre instalações e demais gaps originais não foram ampliados.

## Método dos gates finais

`scripts/security/remediation_full_suite.py` executa todos os arquivos de teste
em até quatro processos, por arquivo inteiro, cada um com export, appdata e bancos
fictícios próprios. Manifesta união exata e compara hashes antes/depois; não
instala plugins ou elimina casos. Não testa ordenação entre arquivos de shards
diferentes. `remediation_scanners.py` mantém somente metadados permitidos dos
scanners, desativa telemetria e verificação de credenciais e preserva evidências.

### Primeira suíte e revisão do ambiente de QA

- `run-bde778ccaa`: 1.006 casos, 1.004 aprovados e duas falhas; zero erros/skips.
  Todos os arquivos foram incluídos uma vez e os hashes permaneceram idênticos.
  O benchmark de observabilidade excedeu 20 ms/ação e o worker do teste de
  restore ainda estava encerrando depois do timeout padrão de cinco segundos.
- Restore: o teste agora aguarda explicitamente até 30 segundos pelo worker,
  exige que esteja parado e descarta o pool antes da substituição offline.
  Nenhuma asserção de rollback, estado ou sessão foi removida; produto inalterado.
- Desempenho: no HD D:, o reteste isolado corrigido mediu 107,908 ms/ação;
  o baseline f91edf9 também falhou, com 104,594 ms/ação (`run-a4a2d28ed9`).
  Isso não é uma regressão demonstrada das correções de segurança.
- O runner havia movido os bancos temporários do local padrão do pytest para
  o disco do repositório. Ele agora cria uma pasta exclusiva no temporário do
  Windows (SSD C: nesta máquina), incluindo appdata sintético. Exports, relatórios
  e registro do caminho de QA continuam em artifacts. Nenhum banco real é usado.
  O benchmark executa antes dos demais shards, sem builds/scanners concorrentes.
- A comparação exploratória no SSD também registrou uma falha inicial do
  baseline (102,004 ms/ação), preservada em `performance-environment.json`;
  portanto o volume por si só não garante desempenho em qualquer carga do host.
  O corrigido passou com 10,512 ms/ação. Pelo runner final, o baseline passou
  (`run-a8afa87120`, 1/1), e restore + benchmark passaram (`run-48cc294cec`, 13/13).
  Limite de 20 ms, 200 operações, transações reais e verificações de integridade
  permanecem intactos. A primeira falha não foi apagada nem contada como sucesso.
- Semgrep: caminho temporário curto e exclusivo evita o limite de socketpair
  do OCaml no Windows; nenhuma regra foi removida para contornar a falha do runner.

A segunda implementação da causa SD-002 foi corrigida e retestada como descrito
acima. A execução completa final inclui os 12 novos casos, sem remover testes.

## Resultado consolidado

Código validado: `209d5eaa41fda05d828da6ee956ec325dc9948af`.
Suíte completa `run-cff2805c24`: **1.018/1.018**, zero falhas, erros ou skips,
70 arquivos, união exata dos shards e hashes de origem/export conferidos antes
e depois. Duração total: 463,222 segundos. O benchmark executou sozinho antes
dos quatro shards; nenhuma asserção ou limite de desempenho foi relaxado.
Abrange auth/session, Control Center, adapters Supabase, sync, Admin Recovery,
Nexa, desktop, backup/restore e demais suítes existentes. Trata-se de QA/local.

| ID | Severidade original | Estado após esta etapa | Residual |
| --- | --- | --- | --- |
| SD-001 | MEDIUM | REMEDIATED_VERIFIED em QA/local | Migration preparada e validada; não aplicada em PROD |
| SD-002 | MEDIUM | REMEDIATED_VERIFIED, Control Center e observabilidade local | Trabalho limitado nos cenários testados; sem garantia universal de desempenho |
| SD-003 | MEDIUM | Prevenção futura REMEDIATED_VERIFIED; histórico CONFIRMED/OPEN | Ação humana para proveniência, histórico, clones/forks e credenciais eventualmente ativas |
| SD-004 | INFO | UNRESOLVED | Candidato histórico não usado contra provedores; sem prova de credencial ativa e sem rotação cega |
| SD-005 | MEDIUM provisório | UNRESOLVED | 252 pares inconclusivos no recorte original; nenhuma exploração do ERP confirmada |
| SD-006 | N/A | NOT_APPLICABLE preservado | Advisory de sdist fora do fluxo oficial |
| SD-007 | LOW | REMEDIATED_VERIFIED em QA/local | Sem deploy |
| SD-008 | LOW | REMEDIATED_VERIFIED no SQLite local | Não extrapolado para enumeração no Supabase PROD |

### Ferramentas e artefatos

| Gate | Resultado e limite |
| --- | --- |
| CodeQL 2.27.1 / Python queries 1.8.11 | Reteste completo do export com 229 arquivos Python. Criação/análise exit 0, 32 alertas, todos mapeados ao reteste anterior. Em relação ao discovery: 30 correspondem, dois alertas PRIVATE KEY removidos e dois adicionais em impressão de metadados sanitizados dos scripts de discovery. Quatro candidatos regex remanescentes têm entrada limitada e regressões comportamentais aprovadas. Não se alega scanner sem alertas |
| Semgrep 1.178.0 | 176 arquivos, 225 regras do pack p/security-audit preservado; três alertas, zero erros. NAVIGATION literal em href e dois parsers XML restritos ao JUnit gerado pelo próprio pytest. Mesma triagem contextual. SHA-256 do pack: b109a039df712f30c6d3e25e1e8358053fd0f1c91b92d0e8d2871cd141fe602f. O pack do discovery não foi preservado, portanto não se afirma igualdade com aquele pack |
| Bandit 1.9.4 | 186 alertas, zero erros de parser/nosec; mesmos pares regra/arquivo do reteste anterior. Todos os 113 antigos mapeados; 73 adicionais nos harnesses de auditoria, contextualizados. Runtime mantém 39, sem novo alerta |
| pip-audit 2.10.1 | Runtime: 28 dependências, zero advisories/skips. Build: sete entradas e duas ocorrências brutas do mesmo CVE-2026-59890, SD-006 não aplicável ao fluxo PyInstaller/ZIP. Locks não mudaram; nenhum upgrade |
| Trivy 0.74.0 | Imagens intermediária 69e4467 e final 209d5ea: mesmos 258 pares pacote/versão/advisory da imagem original, zero adições/remoções. Reteste final offline exit 0, base local preservada de 29/09/2026 13:11 UTC. Dockerfile/locks, Python, SQLite e distribuições Python idênticos. Mantida classificação SD-005; sem alegação de atualização independente de advisories |
| Gitleaks 8.30.1 | Código: 12 alertas contextualizados — sete fixtures/exemplos, três SHA Git conferidos e dois delimitadores TEST_ONLY sem material de chave. Nenhum secret novo. Windows e aplicação extraída do Docker: zero alertas |
| TruffleHog 3.97.9 | Código: uma URI de fixture negativa previamente triada, sem alteração; verificação de provedores desativada. Windows e aplicação Docker: zero alertas |
| Scanner oficial | 381 candidatos na revisão de código e 382 no fechamento documental, zero ocorrências. Gitleaks/TruffleHog finais mantêm os mesmos 12/1 alertas triados, sem adições. Distribuição Windows: 915 arquivos; aplicação Docker: 245 arquivos. Comparação com valores locais feita em memória sem imprimi-los |
| Windows | Build oficial PyInstaller 6.16.0, pip check aprovado, checkout isolado e limpo no início. ZIP final SHA-256: 0e4d3e4d1ea2e388f81ad67412472dec1399a92ac3f572d5d6b5479e2c42cbbb. Avisos de hooks opcionais não impediram build. Sem publicação ou certificação GUI/hardware |
| Docker | Build aprovado: sha256:25fe38472f3b6818b998fbd729362c8c42630a59cab3626bfb9f709144666c93. Smoke read-only, UID 10001, network none, tmpfs, sem mounts/portas: health 200, login 303, acesso 200, CSRF Unicode 403, logout 303, replay revogado 303. Container encerrado/removido |
| Configuração | render.yaml validado, plan free, autoDeploy false e secrets sem plaintext; Dockerfile, render.yaml e locks preservados |
| Preservação | 41/41 hashes dos manifestos originais reconferidos, zero alterações; dados reais não usados em testes |

Evidências finais locais: `scanners/run-da1ee422b8`, `scanners/run-2423f88d31`, `final-static/`,
`full-suite/run-cff2805c24` e `builds/final-209d5eaa41fd-7f1a6c`, todos sob
`artifacts/security/remediation/`, ignorados pelo Git. O manifesto
`REMEDIATION_EVIDENCE_MANIFEST.csv` registra hashes e retenção sem publicar outputs
brutos, credenciais, bancos, binários ou caches.

Falhas de runner preservadas: Docker inicialmente parado, search_path do pgTAP,
destino CodeQL inexistente, TMP longo no socketpair Semgrep e comparação inicial
de Git archive LF com checkout Windows CRLF. Para CodeQL final, o export foi
comprovado byte a byte igual ao checkout limpo; somente CRLF/LF foi normalizado
na comparação com Git archive, sem modificar o código analisado. O build gerou
um ModuleAnalysisCache do PowerShell fora do empacotamento em seu checkout isolado;
nenhum rastreado mudou, e o cache não está no ZIP, no contexto Docker ou no Git
principal. A resolução desse REVIEW_REQUIRED está registrada separadamente.

A variante de observabilidade integra SD-002 e foi corrigida nesta etapa.
Nenhuma vulnerabilidade independente nova foi confirmada. Alertas adicionais
foram classificados pelo caminho real dos dados, sem supressão de regras.

## Rastreabilidade Git e encerramento

| Commit | Conteúdo |
| --- | --- |
| 45ce4b0 | SD-001: revogação persistente, migration QA e regressões |
| 1179154 | SD-002 Control Center e revalidação restrita SD-004/005 |
| 9b2eab2 | SD-007: rejeição controlada de CSRF malformado |
| f8d59b9 | SD-003: prevenção futura de perfis em Git/release |
| 95daf34 | SD-008: trabalho criptográfico equivalente no SQLite |
| 69e4467 | Harnesses e lifecycle offline de restore |
| a2a6520 | Isolamento do ambiente, benchmark serial e encerramento do worker QA |
| 209d5ea | SD-002 observabilidade local, 12 regressões adicionais |

O marco `f91edf983b151f494c113cfabe8344252c626c73` foi publicado e confirmado
antes de qualquer remediação; reconfirmado no remoto durante o fechamento.
As correções e a consolidação documental foram publicadas até `e4ae25e`, após
secret scan final e git diff --check aprovados. O SHA remoto foi conferido igual
ao HEAD local e o working tree estava limpo. Esta atualização registra o push
já realizado; o SHA da última atualização documental será informado na entrega.

Não houve rewrite/force-push, rotação de secrets, alteração de dados, RLS,
platform_admin, tenant ou installation em PROD, deploy Edge Functions ou Render.
A migration de sessões **deve preceder o futuro deploy** e continua aplicada
somente em QA. Esta entrega não autoriza ativação em PROD, não fecha o residual
histórico SD-003 e não transforma SD-004/005 em falsos positivos.

Etapa encerrada: SECURITY RETEST + COVERAGE EXPANSION e deploy dependem de
instrução própria. As oito superfícies NOT_TESTED e 39 categorias parciais
originais permanecem fora desta etapa.
