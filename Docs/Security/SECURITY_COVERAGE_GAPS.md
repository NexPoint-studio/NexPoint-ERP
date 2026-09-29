# Lacunas de cobertura — validação futura

Registro final de 29/09/2026. Todas as categorias PARTIALLY_TESTED e superfícies
NOT_TESTED da matriz estão abaixo. Pendência de cobertura não é vulnerabilidade
confirmada nem autorização para remediação/PROD. Priorizar binding de restore,
fronteiras de instalação/cloud e proveniência de secrets; severidade exige evidência.

| Controle/superfície | Estado | Risco e motivo | Alternativa já usada | Validação futura |
| --- | --- | --- | --- | --- |
| AUTHENTICATION | PARTIALLY_TESTED | Backend Supabase autenticação e latência não medidos no mesmo harness | C/B: final_auth 149 checks; hash, política, Unicode, inativo; SD-008 SQLite | Fixture PostgREST + adaptador Supabase, contas sintéticas; sem enumeração externa |
| AUTHORIZATION | PARTIALLY_TESTED | Matriz amostral não inclui cada ação de todos os módulos financeiros | C/B/E: papéis/grants A/B/vazios, objetos e POST direto; Data API 90 casos | Inventariar rota × papel × propriedade por módulo em QA |
| SESSION | PARTIALLY_TESTED | Sem fluxo gráfico de logout ou longa duração em múltiplos workers | C/B: fixação, rotação, expiração com relógio, tamper, logout, senha e revogação; SD-001 | Browser QA, relógio controlado e dois workers isolados |
| CSRF | PARTIALLY_TESTED | Nem todo formulário/Content-Type foi verificado em navegador | C/B: tokens/Origin válidos e inválidos; SD-007; nenhum efeito da operação inválida | Matriz de formulários e fetch em browser QA, inclusive não ASCII |
| CORS | PARTIALLY_TESTED | Comportamento do browser e gateway integrado não observado | C/B: allowlist Nexa revisada e testes Deno; Semgrep triado | Preflight e credentials em browser contra gateway QA |
| ORIGIN | PARTIALLY_TESTED | Topologia de proxy real não reproduzida | C/B/E: HTTP local + 500 propriedades origin/Unicode | Proxy reverso exclusivamente local com origens configuradas |
| HOST | PARTIALLY_TESTED | DNS rebinding e resolução do WebView não exercitados | C/B/E: TrustedHost e URL local negativos | Cenários DNS sintéticos em VM isolada, sem reconfigurar host real |
| PROXY | PARTIALLY_TESTED | Ingress e cadeia de proxies não equivalem ao ASGI simulado | C/B: regressão de headers e rate limit; configuração trust revisada | Proxy QA com whitelist e conexões diretas/indiretas documentadas |
| INPUT_VALIDATION | PARTIALLY_TESTED | Schemas HTML do gerador são pouco restritivos; amostra não exaustiva | C/B/E: vazio, longo, Unicode, tipos/null/datas/IDs, extras e MIME; 258 exemplos anteriores | Casos determinísticos por schema e limite, sem carga ou payload destrutivo |
| INJECTION | PARTIALLY_TESTED | Nem todo sink DOM/SQL/template recebeu fluxo completo | C/B/E: SQL parametrizado, escapes Jinja e entradas inertes; SAST | Rastrear origem até sink e testar reflexão inerte no browser QA |
| FILESYSTEM | PARTIALLY_TESTED | ACL real, junctions e outra conta Windows fora do teste | C/B: fixture ACL, traversal, cópias SQLite e DPAPI atual | VM descartável com dois usuários e diretórios fictícios |
| DATABASE | PARTIALLY_TESTED | Nem todo constraint/estado financeiro/concorrência foi exercitado | C/B/E: integridade SQLite em cópias; PostgreSQL 17.11 e migração original | Matriz de constraints e transações em bancos QA novos |
| SUPABASE | PARTIALLY_TESTED | Não é stack completa Auth/gateway/Edge nem configuração efetiva PROD | C/B/E: SQL/pgTAP/Deno prévios e PostgREST 16.2 real interno | Stack Supabase inteiramente QA, migração original e dados fictícios |
| RLS | PARTIALLY_TESTED | Claims reais de emissor e todas as policies em todas operações não percorridos | C/B/E: 21 tabelas FORCE/ENABLE; anon/auth negados; grants temporários com rollback A/B | pgTAP por policy/operação e JWT emitido por Auth QA |
| RPC | PARTIALLY_TESTED | Falhas de gateway e combinações completas de parâmetros não verificadas | C/B/E: privilégios, SECURITY DEFINER/search_path; 11 RPCs × 2 roles negativas | Casos limites por assinatura + Edge/gateway local |
| SYNC | PARTIALLY_TESTED | Fluxo de instalação empacotada até Edge e ACK não integrado | C/B/E: 36 regressões Sync/Recovery, 8 Edge; contrato SQL e RPC HTTP | ERP QA offline/online ligado à stack Supabase QA |
| OUTBOX | PARTIALLY_TESTED | Execução prolongada, falha abrupta e espaço em disco não testados | C/B: retry, dead-letter, restart, retention e identity nos contratos existentes | VM QA com volume limitado e interrupções controladas sem dados reais |
| ACK | PARTIALLY_TESTED | Todas as combinações de reordenação/perda/restart não simuladas | C/B/E: duplicata/idempotência/nonce via PostgREST; duas chamadas, um registro | Transport sintético com falhas determinísticas e assert de event identity |
| OFFLINE | PARTIALLY_TESTED | Longa indisponibilidade e reconexão real do pacote ausentes | C/B: regressões de fila/retry e separação disponibilidade/transação | Ciclos de rede em VM local com dados fictícios |
| RECOVERY | PARTIALLY_TESTED | Cadeia UI/DPAPI/Edge/PostgREST completa não exercitada | C/B: 10 cenários SQL, validade/uso único/revogação/bindings/versão e dois consumidores | Duas instalações sintéticas, dois solicitantes e trilha em stack QA |
| NEXA | PARTIALLY_TESTED | Provider e comportamento de modelo reais não avaliados; --no-check não é typecheck | C/B: HMAC, replay, tool policy, tenant/context, limites/erros/fallback; 166 testes Deno | Provider QA controlado, typecheck e cenários sintéticos sem dados pessoais |
| DESKTOP | PARTIALLY_TESTED | WebView2 empacotado e origem do handle no restore não instrumentados | C/B: 138/139 regressões; auth/Admin Lock/remember; launcher sem js_api, debug off | VM Windows com build QA, browser e captura por PID |
| NETWORK | PARTIALLY_TESTED | Recovery/Nexa/ingress/WebView e env proxy não cobertos por esse teste TLS | C/B/E: TCPView próprio; 5 testes TLS reais loopback do Sync | Servidores TLS QA por cliente e inspeção restrita ao processo QA |
| BACKUP | PARTIALLY_TESTED | Dados não cifrados na cópia dependem de ACL; cenário entre contas ausente | C/B: cópias, schema/integridade/FK, downloads e autorização | Fixture de permissões/retention e acesso entre contas em VM |
| RESTORE | PARTIALLY_TESTED | Teste ativo falha WinError 5; binding tenant/installation/DPAPI entre instalações não provado | C/B: truncamento/view/trigger/future/FK negados; offline separado íntegro | Dois fixtures completos; matriz startup/worker/parada, rastrear handle por PID |
| LOGS | PARTIALLY_TESTED | Sem inspeção de destinos/retention reais; não todos os caminhos de exceção | C/B: campos sanitizados nos testes e saída genérica de erros | Canários fictícios por erro e validação de saída em sink QA |
| OBSERVABILITY | PARTIALLY_TESTED | Código preserva pending além do orçamento; operação offline longa não medida | C/B: snapshots, campos proibidos, auditoria e retenção nos contratos | Fila sintética pequena + limite/ACK; capacidade em volume descartável, sem DoS |
| SECRETS | PARTIALLY_TESTED | Realidade/atividade dos históricos desconhecidas; SecretParts em artefato local antigo | C: árvore/história e final_triage; candidato UUID no blob, sem valor no relatório | Proveniência pelo proprietário e metadados de emissão; não usar candidato em provedor |
| SUPPLY_CHAIN | PARTIALLY_TESTED | Backports e alcançabilidade individual ainda sem prova | C/E local: inventário, fontes oficiais e módulos do digest; 5 N/A, 254 unresolved | Revisão por advisory/função/import/build, sem PoC destrutiva |
| DOCKER | PARTIALLY_TESTED | Digest LIVE, capabilities/mounts efetivos e build provenance não inspecionados | C/E local: Dockerfile, Trivy, non-root 10001 e permissões de fonte | Rebuild QA por digest e inventário de imagem/configuração local |
| BUILD | PARTIALLY_TESTED | Nesta rodada não foi gerado/assinado/executado novo pacote Windows | C: PyInstaller/ZIP, lock, regras de distribuição e secrets | Build QA reprodutível e revisão SBOM/assinatura, sem publicação |
| GIT | PARTIALLY_TESTED | Refs remotas inacessíveis/reflogs/forks/cópias externas não inventariados | C: árvore/história alcançável, 1093 caminhos de perfil e scans | Inventário de refs autorizado separado; sem reescrever história nesta fase |
| PRIVACY | PARTIALLY_TESTED | Não acesso a registros pessoais históricos nem aos destinos reais | C/B: minimização/sanitização tickets/context/telemetria e fixtures | Avaliar proveniência/retention por metadados e dados artificiais |
| AVAILABILITY | PARTIALLY_TESTED | Failover e capacidade real fora de escopo | C/B: /health separado de readiness; erros de dependência e limites locais | Simulação de falha QA limitada; capacidade em tarefa autorizada separada |
| RESOURCE_EXHAUSTION | PARTIALLY_TESTED | Não executar DoS/carga; outros padrões CodeQL unresolved | C/B anterior: SD-002 com timeout; limites de corpo e tamanho revisados | Análise estática de complexidade e limites em microtestes de orçamento fixo |
| CONCURRENCY | PARTIALLY_TESTED | Interleavings e locks financeiros/sessão completos não verificados | B/E: dois consumidores recovery, nonce único e idempotência RPC | Dois atores sintéticos por transição em banco QA, sem carga |
| ERROR_HANDLING | PARTIALLY_TESTED | Nem todos os erros de I/O/cloud/cancelamento possuem fixture | C/B/E: 500 CSRF conhecido, 503 Nexa esperado; sem traceback na amostra | Fault injection local em adapters, checando saída e estado persistido |
| CONFIGURATION | PARTIALLY_TESTED | Não foi lida configuração cloud ou cofre real | C: Docker/render/Supabase/runtime/health/secrets/debug/ports revisados | Validar modelo de configuração QA contra contratos, sem PROD |
| DEPLOYMENT | PARTIALLY_TESTED | Segurança efetiva do ingress e plataforma não é inferível do YAML | C: render.yaml free, Supabase, sync:false, QA/seed off; sem alterações | Ambiente equivalente QA isolado, sem deploy nesta tarefa |
| WINDOWS_CROSS_USER | NOT_TESTED | DPAPI/ACL entre dois usuários Windows: Não criar contas ou mudar host; fixture atual testa só o próprio usuário | Ver categoria correspondente acima | VM com duas contas, chaves e arquivos sintéticos |
| RESTORE_CROSS_INSTALLATION | NOT_TESTED | Restore cruzado com tenant/installation/DPAPI completos: Schema aceita metadados diferentes; isso não prova comprometimento do binding de instalação | Ver categoria correspondente acima | Dois bancos/cofres QA; startup e observação de identidade antes/depois |
| WEBVIEW_RUNTIME | NOT_TESTED | WebView2/pywebview empacotado, navegação/bridge/clipboard: Revisão de launcher não equivale a execução gráfica instrumentada | Ver categoria correspondente acima | Build QA em VM e captura apenas do próprio PID |
| SUPABASE_FULL_STACK | NOT_TESTED | Auth + gateway + Edge + PostgREST juntos: SQL/Deno/PostgREST foram testados em fronteiras separadas | Ver categoria correspondente acima | Stack QA completa sem secrets/URLs de PROD |
| INGRESS_TLS | NOT_TESTED | Certificados, headers e proxy equivalente ao Render: TLS loopback testou cliente Sync, não terminação de entrada | Ver categoria correspondente acima | Proxy QA local com certificados próprios e casos de confiança explícitos |
| PROCESS_MEMORY | NOT_TESTED | Leitura de memória/clipboard por outro processo: Fora da coleta defensiva atual; evitar dados de processos reais | Ver categoria correspondente acima | VM dedicada com canários fictícios e instrumentação por PID |
| NEXA_LIVE_MODEL | NOT_TESTED | Comportamento de provider/LLM diante de instruções adversas: Nenhum provider externo foi chamado; testes usam doubles | Ver categoria correspondente acima | Ambiente do proprietário autorizado, prompts/dados artificiais e política de tools |
| REAL_FAILOVER_CAPACITY | NOT_TESTED | Capacidade, falha de disco e failover sustentados: DoS/carga não autorizados; disponibilidade não inferida de limites unitários | Ver categoria correspondente acima | Plano de capacidade separado em recursos descartáveis com orçamento e parada |

## Controles ASVS/WSTG ainda parciais ou não executados

Esta relação é extraída de todas as linhas parciais/não testadas da matriz ASVS/WSTG.
A limitação em cada linha define o risco residual. A validação futura aplica-se
somente a QA e deve resolver exatamente o limite descrito, usando a categoria
correspondente acima. Não é um inventário de todos os requisitos individuais ASVS.

| Referência | Estado | Evidência/limitação | Próxima validação |
| --- | --- | --- | --- |
| V1.1–V1.3 | PARTIALLY_TESTED | Bandit/CodeQL, escapes Jinja, 135 verificações HTTP, sanitização Nexa. SD-002; nem todos os contextos de saída exercitados. | Exercitar em fixture QA o limite descrito; método e restrições nas categorias acima |
| V1.4 | PARTIALLY_TESTED | Inventário de bibliotecas nativas; nenhuma exploração de memória. | Exercitar em fixture QA o limite descrito; método e restrições nas categorias acima |
| V1.5 | PARTIALLY_TESTED | JSON malformado/validações Edge, backups SQLite; não todos os parsers. | Exercitar em fixture QA o limite descrito; método e restrições nas categorias acima |
| V2.1 | PARTIALLY_TESTED | Contratos e testes existentes lidos, sem revisão formal de toda regra financeira. | Exercitar em fixture QA o limite descrito; método e restrições nas categorias acima |
| V2.2 | PARTIALLY_TESTED | 258 exemplos/rodada API, limites de corpo, Unicode, SD-007; gerador HTML possui schemas pouco restritivos. | Exercitar em fixture QA o limite descrito; método e restrições nas categorias acima |
| V2.3 | PARTIALLY_TESTED | Recovery, ACK/idempotência e restore; carga financeira/conflitos longos não simulados. | Exercitar em fixture QA o limite descrito; método e restrições nas categorias acima |
| V2.4 | PARTIALLY_TESTED | Limite de 10 tentativas de login QA e headers de proxy; sem brute force, DoS ou distribuição. | Exercitar em fixture QA o limite descrito; método e restrições nas categorias acima |
| V3.1–V3.2 | PARTIALLY_TESTED | Templates/configuração/MIME estáticos e HTTP local; DOM real não percorrido por browser nesta rodada. | Exercitar em fixture QA o limite descrito; método e restrições nas categorias acima |
| V3.3.4 | PARTIALLY_TESTED | HttpOnly observado; não inventariado todo canal de saída de todos os cookies do desktop. | Exercitar em fixture QA o limite descrito; método e restrições nas categorias acima |
| V3.3.1, V3.3.3, V3.3.5 | PARTIALLY_TESTED | Secure observado no painel; nome atual sem prefixo __Host-/__Secure-. Limite máximo de cookie não exercitado. Não declarar conformidade integral. | Exercitar em fixture QA o limite descrito; método e restrições nas categorias acima |
| V3.4 | PARTIALLY_TESTED | CSP, HSTS simulado, XFO, no-store/nosniff; ausência de headers adicionais não prova exploit. | Exercitar em fixture QA o limite descrito; método e restrições nas categorias acima |
| V3.5 | PARTIALLY_TESTED | Origin/Referer/Host/CSRF negativos e CORS Nexa allowlist; sem DNS rebinding real. | Exercitar em fixture QA o limite descrito; método e restrições nas categorias acima |
| V3.6–V3.7 | PARTIALLY_TESTED | Static/JS e redirecionamentos revisados, sem navegador end-to-end/extensões reais. | Exercitar em fixture QA o limite descrito; método e restrições nas categorias acima |
| V4.1–V4.2 | PARTIALLY_TESTED | Métodos, tipos/corpos, tamanho e exposição de OpenAPI em QA; sem request smuggling em proxy real. | Exercitar em fixture QA o limite descrito; método e restrições nas categorias acima |
| V5.1–V5.4 | PARTIALLY_TESTED | Restore/download, traversal, arquivo truncado, views/triggers, schema/FK. Restauração entre instalações reais não testada. | Exercitar em fixture QA o limite descrito; método e restrições nas categorias acima |
| V6.1–V6.3 | PARTIALLY_TESTED | Hash scrypt, política, Unicode, inativo e resposta genérica; final_auth 149 checks e timing local SD-008. Supabase remoto/estatística de enumeração não medidos. | Exercitar em fixture QA o limite descrito; método e restrições nas categorias acima |
| V6.4 | PARTIALLY_TESTED | Recovery: vínculo incorreto, expiração/revogação, uso único e 2 consumidores locais; PostgREST/Edge completo não exercitado. | Exercitar em fixture QA o limite descrito; método e restrições nas categorias acima |
| V7.1–V7.3 | PARTIALLY_TESTED | Sessão assinada, credencial/geração, remembered session, expiração em testes; não todos os limites de tempo. | Exercitar em fixture QA o limite descrito; método e restrições nas categorias acima |
| V7.4.2–V7.4.5 | PARTIALLY_TESTED | final_auth confirma inativação/senha, demotion/grant e chave diferente; não inventariada toda UI de encerramento de sessões. | Exercitar em fixture QA o limite descrito; método e restrições nas categorias acima |
| V7.5 | PARTIALLY_TESTED | CSRF e elevação Admin Lock; comportamento sob sessão concorrente prolongada não testado. | Exercitar em fixture QA o limite descrito; método e restrições nas categorias acima |
| V8.1–V8.4 | PARTIALLY_TESTED | platform_admin/control_admin, A/B/vazios, 404, POST direto e campos extras; 90 casos PostgREST/RPC/RLS. Matriz não cobre todos os módulos financeiros. | Exercitar em fixture QA o limite descrito; método e restrições nas categorias acima |
| V9.1–V9.2 | PARTIALLY_TESTED | Integridade da sessão e HMAC de pontes; JWT Supabase apenas contrato QA, emissor remoto não exercitado. | Exercitar em fixture QA o limite descrito; método e restrições nas categorias acima |
| V11.1–V11.2 | PARTIALLY_TESTED | Revisão scrypt/HMAC/DPAPI e material fictício. Não auditoria criptográfica independente. | Exercitar em fixture QA o limite descrito; método e restrições nas categorias acima |
| V11.3–V11.4 | PARTIALLY_TESTED | DPAPI usuário atual, hashes e assinaturas/replay nos contratos. Separação entre contas Windows não testada. | Exercitar em fixture QA o limite descrito; método e restrições nas categorias acima |
| V11.5 | PARTIALLY_TESTED | Fontes de aleatoriedade revisadas; B311 em jitter/QA, sem teste estatístico de RNG. | Exercitar em fixture QA o limite descrito; método e restrições nas categorias acima |
| V11.6–V11.7 | NOT_TESTED | Gestão de certificados e proteção contra leitura de memória do processo não verificadas. | Exercitar em fixture QA o limite descrito; método e restrições nas categorias acima |
| V12.1–V12.3 | PARTIALLY_TESTED | Fonte exige HTTPS/TLS>=1.2; 5 checks TLS reais loopback do Sync (certificado, hostname e redirect). Sem ingress Render nem TLS de todos os outros clientes. | Exercitar em fixture QA o limite descrito; método e restrições nas categorias acima |
| V13.1–V13.4 | PARTIALLY_TESTED | Docker/Render estáticos, scans árvore/histórico, flags de produção. SD-003/004; sem consulta de secrets/cloud reais. | Exercitar em fixture QA o limite descrito; método e restrições nas categorias acima |
| V14.1–V14.3 | PARTIALLY_TESTED | Sanitização e campos proibidos em snapshots/Nexa, ACL de fixture e no-store. Retenção/acesso Windows real não auditados. | Exercitar em fixture QA o limite descrito; método e restrições nas categorias acima |
| V15.1–V15.3 | PARTIALLY_TESTED | Baseline/scans; SD-005 UNRESOLVED, SD-006 NOT_APPLICABLE no fluxo atual, 254 advisories ainda sem alcançabilidade individual. Não revisão linha a linha de todo o produto. | Exercitar em fixture QA o limite descrito; método e restrições nas categorias acima |
| V15.4 | PARTIALLY_TESTED | 2 consumidores Recovery; pares idempotência/nonce no PostgREST real, um envelope/um consumo. Não prova ausência de todas as races. | Exercitar em fixture QA o limite descrito; método e restrições nas categorias acima |
| V16.1–V16.4 | PARTIALLY_TESTED | Auditoria e sanitização com dados fictícios, falhas de telemetria separadas da transação. Destino/logs reais não lidos. | Exercitar em fixture QA o limite descrito; método e restrições nas categorias acima |
| V16.5 | PARTIALLY_TESTED | Erros genéricos, falha de dependência 503 e SD-007; não todos os caminhos de exceção. | Exercitar em fixture QA o limite descrito; método e restrições nas categorias acima |
| INFO | PARTIALLY_TESTED | Inventário por fonte/rotas/OpenAPI local; nenhuma enumeração externa. | Exercitar em fixture QA o limite descrito; método e restrições nas categorias acima |
| CONF | PARTIALLY_TESTED | Docker/Render, defaults, método HTTP e arquivos estáticos; deploy e rede cloud não verificados. | Exercitar em fixture QA o limite descrito; método e restrições nas categorias acima |
| IDNT | PARTIALLY_TESTED | Usuários fictícios/papéis/inativação; ciclo organizacional real fora do escopo. | Exercitar em fixture QA o limite descrito; método e restrições nas categorias acima |
| ATHN | PARTIALLY_TESTED | Login/hash/limite/Recovery e SD-008 timing SQLite em sete pares fixos; sem backend cloud, senha real ou enumeração remota. | Exercitar em fixture QA o limite descrito; método e restrições nas categorias acima |
| ATHZ-01/02/03/04 | PARTIALLY_TESTED | Traversal, bypass, papel e IDOR com dois tenants, POST direto e RLS local. | Exercitar em fixture QA o limite descrito; método e restrições nas categorias acima |
| SESS-01/02/03 | PARTIALLY_TESTED | Integridade/flags/fixação via regressões, apenas topologias locais. | Exercitar em fixture QA o limite descrito; método e restrições nas categorias acima |
| SESS-07 | PARTIALLY_TESTED | Expiração nos testes de contrato; sem espera pelo prazo máximo real. | Exercitar em fixture QA o limite descrito; método e restrições nas categorias acima |
| INPV | PARTIALLY_TESTED | SQLi/reflexo/XSS/SSTI/poluição/Unicode/corpos limitados. Nenhuma exploração externa. | Exercitar em fixture QA o limite descrito; método e restrições nas categorias acima |
| ERRH | PARTIALLY_TESTED | Respostas sem traceback, exceção CSRF confirmada, fixtures de falha Nexa/Sync. | Exercitar em fixture QA o limite descrito; método e restrições nas categorias acima |
| CRYP | PARTIALLY_TESTED | Hashes/DPAPI e handshake TLS Sync loopback com CA fictícia; sem certificados PROD ou isolamento Windows entre contas. | Exercitar em fixture QA o limite descrito; método e restrições nas categorias acima |
| BUSL | PARTIALLY_TESTED | Idempotência, replay, recuperação e restore QA. Sem campanha destrutiva financeira. | Exercitar em fixture QA o limite descrito; método e restrições nas categorias acima |
| CLNT | PARTIALLY_TESTED | Fonte de templates/JS/pywebview; runtime gráfico completo não testado. | Exercitar em fixture QA o limite descrito; método e restrições nas categorias acima |

## Técnicas não executadas ou limitadas

- Procmon: sem captura nativa; alternativa trace Python/ACL de fixture/TCPView por PID. Resta validar handles e WebView em VM.
- mitmproxy/tshark/Burp: não usados para duplicar proxy local; ZAP e TLS loopback cobrem somente parte. Sem captura TLS de instalação real.
- CodeQL: extração Python, não JS/TS. Semgrep/Deno cobrem amostra JS/TS; falta typecheck Nexa e rastreamento DOM.
- Nuclei: dois templates falharam na rodada anterior; cobertura desses casos NOT_TESTED. Revisão de headers e ZAP foram alternativas.
- CVEs: sem PoCs de corrupção/DoS; inventário, fornecedor e imports. 254 ocorrências aguardam alcançabilidade individual.
- Secrets: sem validação de tokens contra provedores, decrypt de perfil ou rotação. Fingerprints/proveniência são as alternativas.
- Sem nova varredura ativa/agressiva, brute force, bypass, DoS ou carga. Testes fixos de entradas inválidas e permissões usam somente fixtures.
- Stack PROD, logs reais e dados pessoais não acessados; nenhuma conclusão sobre configuração efetiva do serviço publicado.

Critério de encerramento desta rodada: classificar e documentar esses limites, não
transformá-los em aprovação fictícia. Remediação e validações futuras são tarefas separadas.
