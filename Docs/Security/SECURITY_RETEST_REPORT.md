# Security Retest + Coverage Expansion — preparação de release

30/09/2026. **Em andamento; não é autorização para deploy Render.**
Baseline remoto conferido: `9e6bcf18fed4a7b5350016fc8d80eab59ec0cf42`, main,
NexPoint-studio/NexPoint-ERP. Árvore inicialmente limpa. Runtime, locks, dados
operacionais, Render e Supabase PROD permanecem sem alteração nesta rodada.

## Retestes concluídos

| Evidência local, sob artifacts/security | Resultado | Escopo |
| --- | --- | --- |
| remediation/tests/run-429539879d | 145/145, zero falhas/erros/skips | Sessões, sanitização, CSRF, timing, browser guard, Origin e hardening |
| retest/desktop/run-10510fbdc7 | Todos os checks verdadeiros | Quatro ciclos pywebview, remember, Admin Lock, recuperação, logout, crash/cópia/Doctor |
| retest/restore/run-9ad560bf34 | Integridade/FK/auditoria válidos | Backup A restaurado em B; cofre B inalterado e decrypt DPAPI válido; runtime mantém B; quatro envelopes de outra instalação recusados localmente, zero chamadas de transporte |
| retest/webview/run-a86653bfef | 8/8 | WebView2 real: nenhuma API Python exposta, debug/devtools/download desativados, erros TLS não ignorados; outra origem não lê aplicação; nova janela delegada ao navegador com abertura interceptada em QA |
| retest/database/run-b7c64802e0 | 121/121 | PostgreSQL 17 + PostgREST 16.2 + GoTrue 2.197 + Deno 2.7.5 + gateway QA/TLS; sessões reais do repositório, Auth JWT, RLS, RPC, grants, migração reaplicada, ACK/idempotência, ingress, login/logout/replay |
| retest/tls/run-00c829acb5 | 15/15 | Clientes Sync/Recovery, Nexa e Control Center: certificado não confiável e hostname incorreto negados; CA restrita ao processo aceita; redirects recusados sem acesso ao destino |
| retest/codeql/run-358639a563 | Extração/análise exit 0; 32 alertas | Mesmas regras, arquivos e linhas da baseline final; nenhuma adição/remoção |
| remediation/scanners/run-85dbfd6a92 | Ferramentas concluídas | Gitleaks 12 e TruffleHog 1, mesmos candidatos conhecidos; Bandit 217 (31 adicionais em harnesses QA, runtime inalterado); Semgrep 3, mesmos casos após remover prefixo variável do diretório |
| retest/supply/run-0e677fbf84 | SD-004 reconferido | Candidato histórico somente em memória; zero ocorrências nos candidatos Git atuais; sem consulta a provedor, rotação ou decrypt de navegador |

O gateway integrado é um roteador exclusivo de QA, sem portas no host, em rede
Docker interna. Não equivale ao Kong/ingress gerenciado nem certifica configuração
LIVE. Foram executadas as asserções SQL oficiais dentro de rollback; nessa imagem
mínima apenas o invólucro de apresentação pgTAP foi omitido. Certificados/chaves,
senhas, tokens, bancos e outputs brutos são fixtures locais ignoradas pelo Git.

## Findings

| ID | Estado do reteste | Residual |
| --- | --- | --- |
| SD-001 | REMEDIATED_VERIFIED em QA | Sessão persistida e revogada via PostgREST real; migration PROD ainda pendente |
| SD-002 | REMEDIATED_VERIFIED | Limites testados em ambos sanitizadores; nenhuma certificação geral de capacidade |
| SD-003 | Prevenção REMEDIATED_VERIFIED | Histórico antigo continua CONFIRMED/OPEN; sem reescrita |
| SD-004 | UNRESOLVED | Formato UUID no Sync LevelDB não prova credencial, atividade, ambiente ou rotação |
| SD-005 | UNRESOLVED | Advisories não equivalem a exploração confirmada do ERP |
| SD-006 | NOT_APPLICABLE | Fluxo PyInstaller/ZIP não publica sdist |
| SD-007 | REMEDIATED_VERIFIED | Unicode rejeitado também pelo ingress TLS real, sem HTTP 500 |
| SD-008 | REMEDIATED_VERIFIED no SQLite | Equivalência criptográfica e amostra estatística focada; sem extrapolar timing PROD |

Nenhuma vulnerabilidade nova confirmada nesta rodada até este registro.
Restore autorizado por administrador aceita dados de outra instalação. A fila
preserva a origem desses eventos e recusa enviá-los como B; não houve troca de
cofre ou de identidade. Isso exige atenção operacional aos eventos antigos,
mas o cenário validado não demonstrou quebra de isolamento remoto.

## Dependências e nova base de advisories

pip-audit: 28 dependências runtime, zero advisories/skips; build mantém as duas
ocorrências brutas de SD-006. Nenhum lock alterado.

Trivy 0.74.0 atualizou a base para 30/09/2026 13:11 UTC e reavaliou o digest
`25fe38472f3b6818b998fbd729362c8c42630a59cab3626bfb9f709144666c93`.
Agora são **271 pares**, antes 258: 14 adições de metadados e uma remoção
(perl-base/CVE-2026-48961). Não se chama essa remoção de correção do produto.
Severidades brutas: 5 critical, 59 high, 114 medium, 90 low e 3 unknown.

A triagem do runtime preserva seis N/A anteriores e acrescenta dez ocorrências
N/A restritas ao código inspecionado: DTLS, assinatura SM2/curvas genéricas e
cliente CMP não usados. A imagem usa Python TLS/TCP, CERT_REQUIRED e validação
de hostname; o produto não chama esses protocolos/APIs. A definição das
pré-condições vem do [OpenSSL upstream](https://openssl-library.org/news/vulnerabilities/)
e do [tracker Debian do alerta DTLS](https://security-tracker.debian.org/tracker/CVE-2026-84782).

Permanecem **255 UNRESOLVED e 16 NOT_APPLICABLE** na imagem. O novo alerta de
alocação CRLDP permanece inconclusivo: o cliente TLS processa certificados,
e não foi executado teste de exaustão. Os dois novos alertas dash também não
foram fechados apenas pela ausência de subprocess no produto. Fontes Red Hat
consultadas não puderam ser abertas; a alternativa foi metadado do scanner e
revisão estática, com incerteza preservada. Nenhum Critical/High explorável no
ERP foi confirmado. [Triagem por ocorrência](RETEST_DEPENDENCY_TRIAGE.csv).

## Cobertura e limitações

As **39 categorias e oito superfícies** foram revisadas individualmente em
[RETEST_COVERAGE_MATRIX.md](RETEST_COVERAGE_MATRIX.md). Restore cruzado,
WebView runtime, Supabase integrado e ingress TLS ganharam execução concreta;
a cobertura continua parcial onde indicado.

- Sem segunda conta/VM Windows segura: DPAPI/ACL entre usuários e leitura de
  memória/clipboard permanecem NOT_TESTED. Conta atual não elevada, Windows
  Sandbox ausente; nenhuma conta, firewall, trust store ou proxy do host alterado.
- WebView permite navegação file:// pela API Python privilegiada usada pelo
  harness; isso é observação do default, não prova de ataque remoto. Não há
  métodos Python expostos à página. Custom schemes nativos não foram acionados.
- Modelo/provider Nexa real e capacidade/failover sustentados não testados.
  Contratos locais, falhas limitadas e clientes TLS são a cobertura alternativa.
- O executável PROD força o projeto oficial Supabase. Testes funcionais desse
  pacote exigem isolamento de rede efetivo; não se deve iniciar uma fixture
  operacional dele sem esse isolamento. Build RC e seu escopo de validação
  ainda pendentes; os testes GUI acima executaram o código fonte em QA.
- ZAP/Nuclei/Procmon não repetidos por rotina: HTTP/TLS/WebView reais e testes
  determinísticos cobriram os controles descritos. Os dois templates Nuclei
  que falharam originalmente não ganharam aprovação presumida.

## Execuções inválidas preservadas

O primeiro teste focado referenciou um arquivo inexistente (zero testes, exit 4).
O harness de restore inicialmente usou o nome errado do parâmetro e um usuário
sem papel admin; corrigiu-se apenas a fixture. A primeira integração Deno ligou
o handler diretamente e passou o argumento ServeHandlerInfo como dependency;
foi alinhada ao wrapper já existente no index oficial. Outro caso RPC enviava
assinatura incompleta e obteve 404; foi repetido com todos os parâmetros e
confirmou negação por permissão. São erros de harness, não correções do produto.

Suíte completa final, builds RC, preparação Supabase PROD, commit/push e entrega
Render permanecem pendentes. O gate será publicado somente após consolidar
esses resultados; não houve deploy ou smoke PROD.


## Prepara??o Supabase PROD conclu?da

Projeto `scfncgaiovztrbgrcvkt` confirmado ACTIVE_HEALTHY e link local conferido.
Ap?s gate QA verde, backup schema/dados cloud, tr?s bancos SQLite consistentes,
configura??o/cofres e manifesto anterior: nove arquivos cifrados DPAPI, roundtrip
validado, ACL conta/SYSTEM, fora do Git. Local:
`%LOCALAPPDATA%/NexPoint/ERP/backups/pre-release-b6324b8c10ea`.

Aplicada em transa??o a migration oficial `20260929010000_platform_sessions.sql`,
com lock/statement timeout, pr?-condi??es de schema/version e registro em
`supabase_migrations.schema_migrations`. Migration SHA-256:
`0fd4e46be08ff1f1ac1ef86da8fc4d4821479d3932ae364e8ce10afa73aa04da`.

Schema passou de 21 para 22 tabelas. Compara??o completa confirmou schema
preexistente, fun??es e policies iguais. Dumps de dados anteriores/posteriores,
comparados somente em mem?ria por tabela, confirmaram as 21 tabelas p?blicas
preexistentes inalteradas. Nova tabela sem grants anon/authenticated/PUBLIC,
RLS/FORCE RLS, service_role apenas SELECT/INSERT/DELETE. Nenhum secret rotacionado.

Apenas consultas read-only de metadados/preserva??o depois da migration;
nenhum smoke de autentica??o/aplica??o PROD. Edge Functions sem mudan?as e sem
redeploy. Render continua intocado. Evid?ncias sanitizadas locais:
`prod-migration-result.json`, `prod-schema-diff.json`, `prod-data-preservation.json`
e `pre-release-backup.json` sob `artifacts/security/retest/`.
