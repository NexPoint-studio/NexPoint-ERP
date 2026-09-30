# Security Discovery — findings

> Atualiza??o de 30/09/2026: o [reteste em andamento](SECURITY_RETEST_REPORT.md) e a
> [matriz de cobertura atual](RETEST_COVERAGE_MATRIX.md) complementam esta fotografia
> hist?rica. As evid?ncias anteriores s?o preservadas; n?o representam o gate da release.

> Registro original da descoberta, preservado. Estados e evidências da etapa
> posterior: [Security Remediation](SECURITY_REMEDIATION_REPORT.md).

Baseline ERP `b09c48e2f5d6e7ecfb9d1aef91ef95753cf1f2c6`, 29/09/2026.
Responsável: coordenação da auditoria autorizada. Somente QA/local, dados fictícios.
Triagem final de 29/09/2026, continuação de `277a5b2`: sete registros OPEN e um
encerrado como NOT_APPLICABLE ao fluxo examinado. Nenhuma remediação aplicada.
Severidades abaixo são técnicas, sem pontuação CVSS; não são a severidade bruta do scanner.

| ID | Componente | Evidência | Severidade | Descrição |
| --- | --- | --- | --- | --- |
| SD-001 | Control Center | CONFIRMED | MEDIUM | Cookie copiado continua válido após logout |
| SD-002 | Control Center | CONFIRMED | MEDIUM | Sanitização processa expressão regular custosa antes de limitar tamanho |
| SD-003 | Git / Supply Chain | CONFIRMED | MEDIUM | Perfil de navegador permanece no histórico alcançável |
| SD-004 | Git / Supply Chain | UNRESOLVED | INFO | Candidato histórico presente; natureza e atividade não confirmadas |
| SD-005 | Docker / Supply Chain | UNRESOLVED | MEDIUM | Inventário presente; alcançabilidade restante não confirmada |
| SD-006 | Build Windows | NOT_APPLICABLE | INFO | Caminho de sdist do advisory não integra o build oficial examinado |
| SD-007 | Control Center | CONFIRMED | LOW | CSRF não ASCII provoca erro 500 sem executar operação |
| SD-008 | Control Center / SQLite | CONFIRMED | LOW | Caminhos de login existente/inexistente têm custo distinguível localmente |

Total: **8 registros**, 5 CONFIRMED, 0 LIKELY, 0 FALSE_POSITIVE, 2 UNRESOLVED,
1 NOT_APPLICABLE. CRITICAL 0, HIGH 0, MEDIUM 4, LOW 2, INFO 2, incluindo o registro
encerrado SD-006. Entre os sete OPEN: MEDIUM 4, LOW 2, INFO 1. Não se afirma ausência de riscos
graves nas superfícies não verificadas. Os 259 registros de dependência do anexo
são candidatos deduplicados por pacote/versão/advisory/artefato, agrupados em SD-005/006.

## SD-001 — replay após logout

- Categoria/fronteira: encerramento de sessão entre navegador e backend.
- Local: `control_center/web.py:1270`; logout somente limpa a sessão do cliente.
  Middleware revalida usuário e versão derivada da credencial, mas o logout não a altera.
- Pré-condição: posse prévia de uma cópia válida do cookie. Não foi demonstrado roubo
  inicial de cookie, acesso sem credencial nem escalada de papel.
- Ambiente/ferramenta: TestClient HTTPS simulado, flags de produção, SQLite QA,
  Python 3.13.3/FastAPI 0.141.1/Starlette 1.6.0.
- Reprodução: executar `scripts/security/web_api/reproduce_sessions.py`; autenticar
  usuário fictício, copiar cookie somente em memória, fazer logout, reutilizar cópia
  em cliente separado. Esperado: redirecionamento para login. Observado: GET `/` 200.
  Troca da senha fictícia faz o mesmo cookie retornar 303.
- Evidência: `artifacts/security/web/session-replay.json`; apenas códigos e flags.
- Impacto: sessão previamente comprometida conserva os privilégios após logout,
  até outra condição de invalidação. MEDIUM pela pré-condição de posse do cookie;
  nenhuma exfiltração foi executada. Confiança alta na reprodução local.
- Mapeamento: [CWE-613](https://cwe.mitre.org/data/definitions/613.html), ASVS 5 V7.4,
  WSTG 4.2 SESS-06. Sem CVE atribuída ao produto. Estado **OPEN**.

## SD-002 — custo da sanitização antes do corte

- Categoria/fronteira: consumo de CPU por texto fornecido ao backend; CWE-1333.
- Local: `control_center/sanitization.py:108`, `_PRIVATE_KEY_BLOCK`, seguido do
  corte apenas no retorno; `control_center/web.py:1287` sanitiza `q` antes de cortá-lo.
- Pré-condição comprovada na rota: conta autenticada e consulta longa chegando ao ASGI.
  O limite de URL do proxy Render não foi medido. Não é um DoS remoto comprovado em PROD.
- Ferramentas: CodeQL 2.27.1, revisão de fluxo, cronômetro e subprocesso Python limitado.
- Reprodução: `bounded_sanitizer.py` testa texto fictício com marcadores incompletos,
  interrompendo cada processo em 5 s; `bounded_query.py` realiza apenas uma requisição
  ASGI, sem servidor de rede e sem concorrência.
- Evidência: 13.825 caracteres levaram aproximadamente 0,057 s; 55.297, 0,687 s;
  subprocesso com 221.185 caracteres atingiu timeout de 5 s (inclui inicialização).
  A rota `/empresas` aceitou 55.297 caracteres com 200 em 0,841 s. Tempos variam por host.
- Impacto potencial: gasto desproporcional de CPU por usuário interno. MEDIUM pela
  possibilidade de afetar capacidade compartilhada, com autenticação e limites de
  proxy como restrições. Confiança alta no custo local; disponibilidade sob carga não testada.
- Evidência local: `web/sanitizer-timing.json`, `web/query-timing.json` sob artifacts/security.
  Outros padrões apontados pelo CodeQL não tiveram complexidade patológica comprovada;
  permanecem candidatos correlacionados, não seis vulnerabilidades confirmadas.
- Mapeamento: [CWE-1333](https://cwe.mitre.org/data/definitions/1333.html), ASVS V2.2/V15.3,
  WSTG INPV. Sem CVE do produto. Estado **OPEN**.

## SD-003 — perfil de navegador no histórico Git

- Categoria/fronteira: distribuição de artefatos privados pela história do código.
- Local: commit `16020ca5b0d061509545fb5a06a51f3347cafdbd`, árvore histórica `carcaça/`.
  Inventário encontrou 1.093 caminhos de perfil em `Default/` ou `SmartScreen/`,
  inclusive `History`, `Login Data`, `Network/Cookies` e Local Storage.
  A remoção posterior no commit `a960e7b6e98ce925cff3c3ef8e9ff0ad2ca7d056` não remove os blobs antigos.
- Pré-condição: acesso à história Git. Visibilidade atual do GitHub não foi consultada.
- Ferramentas: Git 2.49.0, Gitleaks 8.30.1, TruffleHog 3.97.9; inspeção de nomes de
  objetos, sem abrir registros pessoais ou tentar descriptografar cookies/senhas.
- Reprodução segura: listar caminhos com `git ls-tree -r --name-only` no commit indicado;
  não executar binários, scripts ou extensões do perfil histórico.
- Evidência: `artifacts/security/history-profile-summary.json`, `SECRET_LOCATIONS.csv`.
- Impacto potencial: exposição de metadados de navegação e material de sessão/credencial
  se os arquivos contiverem dados utilizáveis. A presença dos artefatos é confirmada;
  conteúdo pessoal, validade e descriptografia **não** foram confirmados. MEDIUM por
  retenção indevida de perfil na história distribuída; não se afirma tomada de conta.
- Mapeamento: CWE-538, ASVS V13.3/V14.2/V15.2. Sem CVE. Estado **OPEN**.

## SD-004 — candidatos históricos ainda não verificados

- Categoria/fronteira: possível credencial em dados de navegador e código de extensão.
- Localização principal: mesmo commit de SD-003,
  `carcaça/Default/Sync Data/LevelDB/000003.log:98`, detector NpmToken do TruffleHog.
  SHA-256 do candidato: `2f8f8034d985ceb23562971640b7a7ada4827e6becc4e22b660fc65723a60601`.
- Ferramentas: TruffleHog 3.97.9 `--no-verification`; Gitleaks 8.30.1 `--redact=100`.
  Alertas generic-api-key, private-key e sourcegraph em arquivos históricos de
  configurações/extensões são relacionados aqui. Não se promove padrão de scanner a secret válido.
- Reprodução: repetir scan local do Git sem verificação externa. Resultados só com
  tipo, localização e fingerprint; nunca copiar o valor para tickets ou relatórios.
- Evidência: JSONL local sanitizado do TruffleHog e índice `SECRET_LOCATIONS.csv`.
- Impacto e pré-condições: dependem de ser credencial real, ativa e com privilégios.
  A forma moderna `npm_` não foi confirmada; padrão legado ou falso positivo permanece
  possível. Nenhum provedor foi chamado para validar. Confiança baixa, INFO, **OPEN**.
- Mapeamento condicionado à confirmação: CWE-798, ASVS V13.3. Sem CVE.
  Não constitui evidência de vazamento de qualquer secret PROD atual.
- **Triagem final:** UNRESOLVED. Presença no blob original confirmada; formato UUID
  de 36 caracteres e nenhuma ocorrência exata nos arquivos rastreados atuais.
  Esses fatos não distinguem token legado de identificador de sincronização.
  Não se verificou validade, escopo nem propriedade contra um provedor. Ver
  [triagem detalhada](SECURITY_DEPENDENCY_TRIAGE.md), inclusive limite de sanitização
  dos artefatos locais antigos. Rotação só se credencial confirmada em tarefa própria.

## SD-005 — inventário de advisories da imagem

- Categoria/fronteira: dependências distribuídas na imagem Docker do backend.
- Artefato: imagem local `erp-security-discovery:b09c48e`,
  ID `sha256:dedc527873c46ef28ebb391ca9e53a029b23dd51c7bb87fb30cc965fcc480ac4`.
  Build do Dockerfile inalterado; base mutável `python:3.13-slim-bookworm`.
  Não é evidência de que a imagem LIVE possua o mesmo digest ou composição.
- Ferramenta: Trivy 0.74.0, imagem Debian 12.15; 258 pares pacote/versão/advisory,
  123 identificadores distintos. Severidade bruta: 5 CRITICAL, 57 HIGH, 105 MEDIUM,
  86 LOW, 5 UNKNOWN. Essas contagens **não são findings ERP confirmados**.
- Evidência/reprodução: scan da imagem local, `trivy/image.json`,
  [inventário integral](DEPENDENCY_ALERTS.csv) com versão, correção conhecida, fonte,
  status do fornecedor e classificação. 16 ocorrências têm versão corrigida indicada;
  campo vazio não significa ausência de risco.
- Bibliotecas Python detectadas: msgpack 1.1.2 (GHSA-6v7p-g79w-8964, fix 1.2.1);
  setuptools 70.3.0 (CVE-2025-47273, fix 78.1.1; CVE-2026-59890, fix 83.0.0).
  Em container sem rede, `find_spec` não encontrou msgpack/setuptools como módulos
  de topo, mas encontrou `pip._vendor.msgpack`; tratar a cópia empacotada do pip
  separadamente do código executado pelo servidor.
- Pré-condições/impacto: variam por advisory; exigem alcançar a função vulnerável com
  entrada relevante. Não foi demonstrada execução de Perl, PackageIndex ou Unpacker
  com dados de requisição do ERP. MEDIUM provisório para triagem do conjunto;
  confiança alta no inventário, baixa na explorabilidade no ERP, **OPEN**.
- CWE/CVE: usar o identificador específico de cada linha; não atribuir um CWE único
  ao conjunto. ASVS V15.2, WSTG CONF. Sem exploração de CVEs nesta rodada.
- **Triagem final:** UNRESOLVED. Quatro ocorrências da imagem foram descartadas no
  cenário observado (zlib/minizip, msgpack C ausente, dois advisories setuptools fora
  do serving); as outras 254 permanecem UNRESOLVED. São 258 ocorrências da imagem,
  não 258 findings. [Razões e fontes](SECURITY_DEPENDENCY_TRIAGE.md).

## SD-006 — advisory no lock de build

- Local: `requirements/windows-build.lock`, setuptools 80.9.0; pip-audit 2.10.1.
- Evidência: `pip-audit/build.json`; duas entradas do mesmo advisory deduplicadas
  em uma linha: CVE-2026-59890 / GHSA-h35f-9h28-mq5c, fix 83.0.0.
- Pré-condição: construção de sdist com exclusões MANIFEST e nomes com normalização
  Unicode distinta em filesystem compatível. A descrição do advisory se concentra
  em APFS/HFS+; o build Windows e runtime Linux examinados não demonstraram esse caminho.
- Reprodução segura: analisar o lock com pip-audit, sem atualizar dependências.
- Impacto potencial: inclusão indevida de arquivos em distribuição, condicionado
  ao fluxo anterior. INFO, confiança baixa na aplicabilidade ao projeto, **OPEN**.
- Fonte: [advisory oficial](https://github.com/advisories/GHSA-h35f-9h28-mq5c).
  ASVS V15.2; nenhuma publicação de sdist foi executada.
- **Triagem final:** NOT_APPLICABLE, encerrado no escopo atual, sem correção.
  `scripts/build_windows_prod.ps1` executa PyInstaller + ZIP, não publicação de sdist.
  A versão continua no lock. Reabrir se mudar o processo de distribuição; não se
  afirma que o pacote esteja corrigido ou seguro em todos os usos.

## SD-007 — validação CSRF não ASCII

- Local: `control_center/web.py:357`, comparação de strings usando `compare_digest`.
- Categoria/fronteira: entrada inválida provoca exceção não tratada no endpoint.
- Pré-condição reproduzida: sessão autenticada fictícia e Origin válido. Alterar
  somente `_csrf` para um caractere não ASCII, sem modificar o produto.
- Ferramentas: Schemathesis 4.28.0/Hypothesis 6.168.3 e reprodução independente
  `scripts/security/web_api/unicode_csrf.py` com o runtime do ERP.
- Esperado: 403. Observado: ASCII inválido retorna 403; dois exemplos não ASCII
  retornam 500 por TypeError. Nenhuma nota foi gravada, nenhum traceback foi enviado.
- Evidência: `web/unicode-csrf.json`; `schemathesis/results.json` contém somente
  tipos de exceção, linhas e metadados, sem cookies ou payloads de autenticação.
- Impacto: falha controlável de requisição e ruído operacional. **Não** é bypass de
  CSRF nem prova de queda do processo. LOW, confiança alta, estado **OPEN**.
- Mapeamento: CWE-248, ASVS V2.2/V16.5, WSTG ERRH-01. Sem CVE do produto.

## SD-008 — diferença de custo no login do adaptador SQLite

- Componente/categoria: Control Center local, informação por tempo de resposta;
  CWE-208, ASVS V6.3, WSTG IDNT/ATHN. CONFIRMED, LOW, confiança alta no caminho local.
- Fonte: `control_center/local_repository.py:3549` retorna antes de `verify_password`
  para usuário ausente/inativo; usuário ativo com senha errada passa pelo scrypt.
- Pré-condição: acesso ao login configurado com repositório SQLite e amostras
  comparáveis. Não permite login nem revela senha; limitador continua atuando.
- Evidência: sete pares alternados com contas fictícias conhecidas, senha inválida
  gerada, sem dicionário de usuários/senhas, ASGI em processo. Medianas na primeira
  execução: 66,544 ms contra 6,053 ms; repetição final: 75,293 ms contra 7,483 ms.
- Impacto potencial: distinguir existência/atividade de nomes nesse adaptador.
  Não extrapolar para enumeração remota ou distinguir ausente de inativo.
  `supabase_repository.py:1046` usa hash fictício quando usuário não existe; PROD usa
  esse outro adaptador. Rede/ruído e backend Supabase não foram medidos.
- Origem: revisão de código + `scripts/security/final_auth/discover.py`;
  `artifacts/security/final/auth/run-45441f7d36/results.json`, somente números/códigos.
  Ambiente QA SQLite, HTTPS simulado. Sem CVE. Estado OPEN; nenhuma remediação.

## Falsos positivos e alertas contextuais

Os grupos abaixo não entram no total dos registros SD. As ocorrências e
linhas SAST estão preservadas em [SAST_ALERTS.csv](SAST_ALERTS.csv). São 10 grupos
Bandit, 3 grupos CodeQL, 1 grupo de fixtures de secrets, 2 ZAP, 2 Nuclei, 1 Trivy
e 1 Schemathesis, mais 1 Semgrep Nexa e 1 de hashes Git: **22 grupos de triagem contextual/FALSE_POSITIVE**; unidades
agrupadas, não contagem artificial de vulnerabilidades.
Classificação final por ocorrência SAST: FINAL_SAST_TRIAGE.csv (140 FALSE_POSITIVE,
1 CONFIRMED e 5 UNRESOLVED). Alertas históricos de secrets não foram descartados em
bloco: FINAL_SECRET_TRIAGE.csv. O total 22 é histórico/contextual, não 22 findings
formalmente fechados nem contagem homogênea de alertas.

| Grupo | Decisão e justificativa |
| --- | --- |
| FP-BANDIT-B608 (30) | SQL montado com cláusulas/nomes internos ou placeholders; valores vinculados. Tabelas de migrations/demo de listas fixas; snapshot escapa identificador. Nenhuma concatenação de valor externo demonstrada. |
| FP-BANDIT-B104 (2) | Bind 0.0.0.0 intencional no container web; desktop usa loopback. Não prova exposição indevida por si. |
| FP-BANDIT-B101 (37) | Asserts majoritariamente nos verificadores QA; assert de restore após leitura de plano não substitui autorização. Nenhum bypass com otimização demonstrado. |
| FP-BANDIT-B105 (11), B106 (4) | Mensagens de erro, campos vazios, constantes de demo/QA e chave de inicialização de migração offline; não credenciais PROD. |
| FP-BANDIT-B110 (10) | Falha de observabilidade/contexto auxiliar não cancela transação; checagens de auth não são essas exceções suprimidas. Ausência de telemetria sob falha continua limitação operacional. |
| FP-BANDIT-B311 (4) | Jitter de retry e geração fictícia, não geração de credenciais. |
| FP-BANDIT-B404 (4), B603 (7), B607 (4) | Subprocessos locais com argv explícito em scripts de validação/diagnóstico, sem shell com entrada HTTP. Dependem de PATH/host confiável. |
| FP-CODEQL-clear-text-logging-sensitive-data (5) | Saída intencional de credenciais geradas QA/demo ou relatórios com tipo/localização; não fluxo de secret PROD demonstrado. |
| FP-CODEQL-clear-text-storage-sensitive-data (1) | Cookie de sessão lembrada é identificador opaco, não senha; HTTP loopback desktop é topologia local. Proteção contra processo do mesmo usuário não provada. |
| FP-CODEQL-url-redirection (20) | Prefixos locais e parâmetros codificados; `_safe_next` restringe caminho administrativo e rejeita esquema/netloc/backslash. Não demonstrado redirecionamento externo. |
| FP-SECRET-FIXTURES | 7 alertas Gitleaks na árvore atual: exemplo/documentação/canários de teste. URI no TruffleHog é fixture negativa de URL com userinfo; não credencial ativa. História do navegador separada em SD-003/004. |
| FP-NEXA-CORS (1) | Semgrep aponta reflexão de Origin, porém `cors.ts:37` exige presença na allowlist; config rejeita wildcard. Revisão + testes locais, não configuração cloud. |
| FP-GIT-HASHES (3) | Gitleaks final marcou três SHA-1 de commits no relatório como sourcegraph-access-token. Linhas contêm identificadores de objetos Git conferidos, não chaves. |
| ZAP identificação de sessão/login (4) | Informational, descrição de fluxo detectado. |
| ZAP atributo controlável (1) | Reflexo com escaping; payloads inertes confirmaram ausência de HTML cru. Não demonstrado XSS. |
| Nuclei HSTS (2) | Alvo QA HTTP; cenário ASGI HTTPS com flags PROD apresenta HSTS. TLS remoto não avaliado. |
| Nuclei outros headers (8) | CORP/COEP/Clear-Site-Data/X-Permitted-Cross-Domain-Policies ausentes: observação verdadeira, mas não evidência isolada de bypass ou obrigação universal. |
| Trivy DS-0026 | Dockerfile sem HEALTHCHECK; Render declara `/health` externamente. Não é falha da configuração Render examinada; Docker standalone não foi certificado. |
| Schemathesis 503 Nexa | Bridge intencionalmente não configurada no fixture; indisponibilidade explícita esperada. Não descartar os 500 reais de SD-007 junto com esse caso. |

## Observações que não viraram vulnerabilidades confirmadas

- Um teste de restore falhou com PermissionError/WinError 5 em `os.replace` enquanto
  o TestClient permanecia ativo. Execução offline em processo separado concluiu e
  passou integrity_check/foreign_key_check. Causa exata do handle não foi atribuída.
- Validação de schema aceita tabela adicional e alteração do nome fictício da empresa.
  Isso não é prova de aceitação de credencial de outra instalação. A rotina de restore
  não compara tenant/installation; validação end-to-end de restauração cruzada com DPAPI
  e sessão da instalação está **not tested**, não declarada segura.
- A rodada final acrescentou PostgREST real em rede Docker interna (90 casos),
  mas não certifica gateway/Auth/Edge integrados, configuração cloud efetiva,
  provider real, WebView2 ou implantação PROD.
