# Security Retest + Coverage Expansion — preparação de release

30/09/2026. **Concluído dentro do escopo viável; QA PASS_WITH_KNOWN_LIMITATIONS.**
Baseline remoto conferido: `9e6bcf18fed4a7b5350016fc8d80eab59ec0cf42`, main,
NexPoint-studio/NexPoint-ERP. Árvore inicialmente limpa. Código do produto, locks,
dados operacionais e Render preservados. Versão elevada para 1.0.1rc1 e migration
cloud aditiva aplicada após QA/backup; detalhes abaixo. Nenhum deploy ou smoke
de aplicação PROD foi executado.

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
| remediation/full-suite/run-b30b2b4d22 | 1.018/1.018 | Cinco shards, 70 arquivos, zero falhas/erros/skips; exports e origem conferidos por hash |
| remediation/tests/run-47a408632c | 14/14 | Instalação, migrations e distribuição após a versão RC |
| retest/edge/run-647229b98f | 8/8 | Deno com typecheck; container sem rede |
| retest/builds/final-df7cdc5fb37d-1614e2 | Windows + Docker concluídos | Fonte limpa isolada; guard de distribuição em 915 e 245 arquivos, Gitleaks/TruffleHog sem novos secrets |
| retest/frozen/run-b336a3ea05 | 28 checks verdadeiros | Executável imutável com fixture SQLite/DPAPI, dois ciclos; login/remember/Admin Lock/clientes/serviços/notas/pagamentos/caixa/suporte/offline/Nexa/logout/integridade |

O gateway integrado é um roteador exclusivo de QA, sem portas no host, em rede
Docker interna. Não equivale ao Kong/ingress gerenciado nem certifica configuração
LIVE. Foram executadas as asserções SQL oficiais dentro de rollback; nessa imagem
mínima apenas o invólucro de apresentação pgTAP foi omitido. Certificados/chaves,
senhas, tokens, bancos e outputs brutos são fixtures locais ignoradas pelo Git.

## Findings

| ID | Estado do reteste | Residual |
| --- | --- | --- |
| SD-001 | REMEDIATED_VERIFIED em QA | Sessão persistida e revogada via PostgREST real; migration PROD aplicada; smoke PROD aguarda Render Live |
| SD-002 | REMEDIATED_VERIFIED | Limites testados em ambos sanitizadores; nenhuma certificação geral de capacidade |
| SD-003 | Prevenção REMEDIATED_VERIFIED | Histórico antigo continua CONFIRMED/OPEN; sem reescrita |
| SD-004 | UNRESOLVED | Formato UUID no Sync LevelDB não prova credencial, atividade, ambiente ou rotação |
| SD-005 | UNRESOLVED | Advisories não equivalem a exploração confirmada do ERP |
| SD-006 | NOT_APPLICABLE | Fluxo PyInstaller/ZIP não publica sdist |
| SD-007 | REMEDIATED_VERIFIED | Unicode rejeitado também pelo ingress TLS real, sem HTTP 500 |
| SD-008 | REMEDIATED_VERIFIED no SQLite | Equivalência criptográfica e amostra estatística focada; sem extrapolar timing PROD |

Nenhuma vulnerabilidade nova confirmada nesta rodada.
Restore autorizado por administrador aceita dados de outra instalação. A fila
preserva a origem desses eventos e recusa enviá-los como B; não houve troca de
cofre ou de identidade. Isso exige atenção operacional aos eventos antigos,
mas o cenário validado não demonstrou quebra de isolamento remoto.

## Dependências e nova base de advisories

pip-audit: 28 dependências runtime, zero advisories/skips; build mantém as duas
ocorrências brutas de SD-006. Nenhum lock alterado.

Trivy 0.74.0 atualizou a base para 30/09/2026 13:11 UTC e reavaliou o digest
`25fe38472f3b6818b998fbd729362c8c42630a59cab3626bfb9f709144666c93`.
O scan da imagem RC `753b899a4295e0a580251a738d4b53c05281e3c73d3ed1c12932bad64de9a548`
reproduziu exatamente os mesmos pares pacote/versão/advisory/severidade.
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
- O executável PROD força o projeto oficial Supabase. O pacote foi executado com
  appdata/cofre/SQLite inteiramente fictícios e proxy exclusivo dos processos QA
  que recusa CONNECT/HTTP e nunca encaminha tráfego. Os transportes reais usam
  esse proxy; WebView2 recebeu proxy/perfil próprios. Foram negados 44 CONNECT,
  zero encaminhamentos. Não é sandbox geral contra código malicioso ou acesso
  de outro processo da mesma conta. Não houve mudança de proxy/firewall do host.
- No pacote foram exercitados startup, login, remember após reinício, Admin Lock,
  CRUD amostrado, nota de R$ 60,00, pagamento/caixa atômicos, suporte em Outbox,
  erro offline controlado da Nexa, logout/replay e encerramento normal. Exe/cofre
  preservados, porta fechada, integrity_check/FK válidos. Reconexão e ACK foram
  exercitados na integração QA separada e na suíte; o binário congelado não
  contatou a nuvem real. Assinatura de código/SmartScreen não certificados.
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

O primeiro roteiro do pacote consultou a coluna fictícia `amount_cents` em vez
de `gross_amount_cents`; login/negócio já haviam passado. O roteiro foi corrigido
e repetido em novo diretório, sem alterar o produto. A janela oculta passou a
ser encerrada por WM_CLOSE somente no PID criado pelo harness, incluindo
janelas ocultas. A repetição final encerrou normalmente em ambos os ciclos.


## Preparação Supabase PROD concluída

Projeto `scfncgaiovztrbgrcvkt` confirmado ACTIVE_HEALTHY e link local conferido.
Após gate QA verde, backup schema/dados cloud, três bancos SQLite consistentes,
configuração/cofres e manifesto anterior: nove arquivos cifrados DPAPI, roundtrip
validado, ACL conta/SYSTEM, fora do Git. Local:
`%LOCALAPPDATA%/NexPoint/ERP/backups/pre-release-b6324b8c10ea`.

Aplicada em transação a migration oficial `20260929010000_platform_sessions.sql`,
com lock/statement timeout, pré-condições de schema/version e registro em
`supabase_migrations.schema_migrations`. Migration SHA-256:
`0fd4e46be08ff1f1ac1ef86da8fc4d4821479d3932ae364e8ce10afa73aa04da`.

Schema passou de 21 para 22 tabelas. Comparação completa confirmou schema
preexistente, funções e policies iguais. Dumps de dados anteriores/posteriores,
comparados somente em memória por tabela, confirmaram as 21 tabelas públicas
preexistentes inalteradas. Nova tabela sem grants anon/authenticated/PUBLIC,
RLS/FORCE RLS, service_role apenas SELECT/INSERT/DELETE. Nenhum secret rotacionado.

Apenas consultas read-only de metadados/preservação depois da migration;
nenhum smoke de autenticação/aplicação PROD. Edge Functions sem mudanças e sem
redeploy. Render continua intocado. Evidências sanitizadas locais:
`prod-migration-result.json`, `prod-schema-diff.json`, `prod-data-preservation.json`
e `pre-release-backup.json` sob `artifacts/security/retest/`.

## Encerramento técnico da RC

Versão 1.0.1rc1, recurso Windows prerelease 1.0.1.1. Build do commit
`df7cdc5fb37df207202cc3bde0072d9858dade48` em worktree limpo e isolado;
código runtime/locks/Blueprint idênticos na revisão documental posterior.
Hashes do ZIP/exe/imagem e rollback estão nas [release notes](../RELEASE_1.0.1rc1.md).
Imagem executada como UID 10001 com rootfs read-only, sem rede, portas ou mounts;
login/logout/CSRF/replay e liveness aprovados. Readiness, headers e Supabase
foram cobertos pela integração QA real. Não foi instalado binário sobre dados reais.

Revisão final `run-b6f74f1d36`: **396 candidatos**, zero violações do guard;
Gitleaks 12 e TruffleHog 1, mesmos canários/documentação/hashes Git e URI negativa
conhecidos. Nenhum secret novo confirmado. Temporários, bancos, backups, logs,
cofres e outputs brutos ficam ignorados; somente código de QA e documentação
oficial entram no Git. Os 101 hashes das três rodadas anteriores foram novamente
conferidos; o [manifesto desta rodada](RETEST_EVIDENCE_MANIFEST.csv) preserva a
rastreabilidade sem publicar o conteúdo dos artefatos.

Entrega autorizada: commits finais/push main, sem deploy Render. `autoDeploy: false`
e Environment permanecem inalterados. O SHA exato da entrega é informado ao
proprietário após conferência do remoto e working tree limpo. O próximo teste
de aplicação PROD depende da confirmação humana de que esse SHA está Live.
