# Security Remediation — QA/local

Marco preservado no remoto antes das alterações:
`f91edf983b151f494c113cfabe8344252c626c73`, branch `main`,
`NexPoint-studio/NexPoint-ERP`. Os relatórios de discovery permanecem como
evidência histórica; este documento registra a remediação, sem substituir suas
limitações de cobertura. Nenhuma alteração de PROD ou deploy está autorizada
nesta etapa.

## SD-001 — revogação de sessão

**REMEDIATED_VERIFIED** em QA/local; gates globais ainda pendentes.
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

## Pendências da etapa

SD-002, SD-003, SD-007 e SD-008 ainda em processamento; SD-004 e SD-005 serão
reavaliados apenas no escopo já descoberto. SD-006 continua NOT_APPLICABLE.
Suíte completa, scans, builds, consolidação e push das correções ainda pendentes.

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
- Fronteira compartilhada com suporte, logs, Nexa e observabilidade; build Windows
  de validação será necessário, além do Docker, sem publicação.
- Regressão do componente: `run-5b4c05f85b`, 111/111, incluindo os dez casos
  novos e observabilidade, diagnósticos, estabilização de segurança e suporte.

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
Sem mudança em dependências ou locks. Digest/pacotes da imagem de validação ainda
serão comparados; esta decisão não certifica imagem nova nem PROD.

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
