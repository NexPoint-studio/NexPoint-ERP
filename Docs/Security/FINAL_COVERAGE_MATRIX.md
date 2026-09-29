# Matriz final de cobertura por fronteira

29/09/2026. Continuação de 277a5b2, mesma fonte exportada b09c48e.
C = código/configuração; B = comportamento em processo ou doubles explicitados;
E = protocolo real exclusivamente QA/local. TESTED descreve o caso delimitado,
inclusive se falhou. Não é certificação de segurança nem percentual ASVS.

Evidências completas: SECURITY_DISCOVERY_REPORT.md, SECURITY_ASVS_MATRIX.md e
FINAL_EVIDENCE_MANIFEST.csv. Todas as lacunas abaixo constam em SECURITY_COVERAGE_GAPS.md.

| CONTROL | Estado | Evidência e alcance | Risco/limite residual | Validação futura |
| --- | --- | --- | --- | --- |
| AUTHENTICATION | PARTIALLY_TESTED | C/B: final_auth 149 checks; hash, política, Unicode, inativo; SD-008 SQLite | Backend Supabase autenticação e latência não medidos no mesmo harness | Fixture PostgREST + adaptador Supabase, contas sintéticas; sem enumeração externa |
| AUTHORIZATION | PARTIALLY_TESTED | C/B/E: papéis/grants A/B/vazios, objetos e POST direto; Data API 90 casos | Matriz amostral não inclui cada ação de todos os módulos financeiros | Inventariar rota × papel × propriedade por módulo em QA |
| SESSION | PARTIALLY_TESTED | C/B: fixação, rotação, expiração com relógio, tamper, logout, senha e revogação; SD-001 | Sem fluxo gráfico de logout ou longa duração em múltiplos workers | Browser QA, relógio controlado e dois workers isolados |
| CSRF | PARTIALLY_TESTED | C/B: tokens/Origin válidos e inválidos; SD-007; nenhum efeito da operação inválida | Nem todo formulário/Content-Type foi verificado em navegador | Matriz de formulários e fetch em browser QA, inclusive não ASCII |
| CORS | PARTIALLY_TESTED | C/B: allowlist Nexa revisada e testes Deno; Semgrep triado | Comportamento do browser e gateway integrado não observado | Preflight e credentials em browser contra gateway QA |
| ORIGIN | PARTIALLY_TESTED | C/B/E: HTTP local + 500 propriedades origin/Unicode | Topologia de proxy real não reproduzida | Proxy reverso exclusivamente local com origens configuradas |
| HOST | PARTIALLY_TESTED | C/B/E: TrustedHost e URL local negativos | DNS rebinding e resolução do WebView não exercitados | Cenários DNS sintéticos em VM isolada, sem reconfigurar host real |
| PROXY | PARTIALLY_TESTED | C/B: regressão de headers e rate limit; configuração trust revisada | Ingress e cadeia de proxies não equivalem ao ASGI simulado | Proxy QA com whitelist e conexões diretas/indiretas documentadas |
| INPUT_VALIDATION | PARTIALLY_TESTED | C/B/E: vazio, longo, Unicode, tipos/null/datas/IDs, extras e MIME; 258 exemplos anteriores | Schemas HTML do gerador são pouco restritivos; amostra não exaustiva | Casos determinísticos por schema e limite, sem carga ou payload destrutivo |
| INJECTION | PARTIALLY_TESTED | C/B/E: SQL parametrizado, escapes Jinja e entradas inertes; SAST | Nem todo sink DOM/SQL/template recebeu fluxo completo | Rastrear origem até sink e testar reflexão inerte no browser QA |
| FILESYSTEM | PARTIALLY_TESTED | C/B: fixture ACL, traversal, cópias SQLite e DPAPI atual | ACL real, junctions e outra conta Windows fora do teste | VM descartável com dois usuários e diretórios fictícios |
| DATABASE | PARTIALLY_TESTED | C/B/E: integridade SQLite em cópias; PostgreSQL 17.11 e migração original | Nem todo constraint/estado financeiro/concorrência foi exercitado | Matriz de constraints e transações em bancos QA novos |
| SUPABASE | PARTIALLY_TESTED | C/B/E: SQL/pgTAP/Deno prévios e PostgREST 16.2 real interno | Não é stack completa Auth/gateway/Edge nem configuração efetiva PROD | Stack Supabase inteiramente QA, migração original e dados fictícios |
| RLS | PARTIALLY_TESTED | C/B/E: 21 tabelas FORCE/ENABLE; anon/auth negados; grants temporários com rollback A/B | Claims reais de emissor e todas as policies em todas operações não percorridos | pgTAP por policy/operação e JWT emitido por Auth QA |
| RPC | PARTIALLY_TESTED | C/B/E: privilégios, SECURITY DEFINER/search_path; 11 RPCs × 2 roles negativas | Falhas de gateway e combinações completas de parâmetros não verificadas | Casos limites por assinatura + Edge/gateway local |
| SYNC | PARTIALLY_TESTED | C/B/E: 36 regressões Sync/Recovery, 8 Edge; contrato SQL e RPC HTTP | Fluxo de instalação empacotada até Edge e ACK não integrado | ERP QA offline/online ligado à stack Supabase QA |
| OUTBOX | PARTIALLY_TESTED | C/B: retry, dead-letter, restart, retention e identity nos contratos existentes | Execução prolongada, falha abrupta e espaço em disco não testados | VM QA com volume limitado e interrupções controladas sem dados reais |
| ACK | PARTIALLY_TESTED | C/B/E: duplicata/idempotência/nonce via PostgREST; duas chamadas, um registro | Todas as combinações de reordenação/perda/restart não simuladas | Transport sintético com falhas determinísticas e assert de event identity |
| OFFLINE | PARTIALLY_TESTED | C/B: regressões de fila/retry e separação disponibilidade/transação | Longa indisponibilidade e reconexão real do pacote ausentes | Ciclos de rede em VM local com dados fictícios |
| RECOVERY | PARTIALLY_TESTED | C/B: 10 cenários SQL, validade/uso único/revogação/bindings/versão e dois consumidores | Cadeia UI/DPAPI/Edge/PostgREST completa não exercitada | Duas instalações sintéticas, dois solicitantes e trilha em stack QA |
| NEXA | PARTIALLY_TESTED | C/B: HMAC, replay, tool policy, tenant/context, limites/erros/fallback; 166 testes Deno | Provider e comportamento de modelo reais não avaliados; --no-check não é typecheck | Provider QA controlado, typecheck e cenários sintéticos sem dados pessoais |
| DESKTOP | PARTIALLY_TESTED | C/B: 138/139 regressões; auth/Admin Lock/remember; launcher sem js_api, debug off | WebView2 empacotado e origem do handle no restore não instrumentados | VM Windows com build QA, browser e captura por PID |
| NETWORK | PARTIALLY_TESTED | C/B/E: TCPView próprio; 5 testes TLS reais loopback do Sync | Recovery/Nexa/ingress/WebView e env proxy não cobertos por esse teste TLS | Servidores TLS QA por cliente e inspeção restrita ao processo QA |
| BACKUP | PARTIALLY_TESTED | C/B: cópias, schema/integridade/FK, downloads e autorização | Dados não cifrados na cópia dependem de ACL; cenário entre contas ausente | Fixture de permissões/retention e acesso entre contas em VM |
| RESTORE | PARTIALLY_TESTED | C/B: truncamento/view/trigger/future/FK negados; offline separado íntegro | Teste ativo falha WinError 5; binding tenant/installation/DPAPI entre instalações não provado | Dois fixtures completos; matriz startup/worker/parada, rastrear handle por PID |
| LOGS | PARTIALLY_TESTED | C/B: campos sanitizados nos testes e saída genérica de erros | Sem inspeção de destinos/retention reais; não todos os caminhos de exceção | Canários fictícios por erro e validação de saída em sink QA |
| OBSERVABILITY | PARTIALLY_TESTED | C/B: snapshots, campos proibidos, auditoria e retenção nos contratos | Código preserva pending além do orçamento; operação offline longa não medida | Fila sintética pequena + limite/ACK; capacidade em volume descartável, sem DoS |
| SECRETS | PARTIALLY_TESTED | C: árvore/história e final_triage; candidato UUID no blob, sem valor no relatório | Realidade/atividade dos históricos desconhecidas; SecretParts em artefato local antigo | Proveniência pelo proprietário e metadados de emissão; não usar candidato em provedor |
| SUPPLY_CHAIN | PARTIALLY_TESTED | C/E local: inventário, fontes oficiais e módulos do digest; 5 N/A, 254 unresolved | Backports e alcançabilidade individual ainda sem prova | Revisão por advisory/função/import/build, sem PoC destrutiva |
| DOCKER | PARTIALLY_TESTED | C/E local: Dockerfile, Trivy, non-root 10001 e permissões de fonte | Digest LIVE, capabilities/mounts efetivos e build provenance não inspecionados | Rebuild QA por digest e inventário de imagem/configuração local |
| BUILD | PARTIALLY_TESTED | C: PyInstaller/ZIP, lock, regras de distribuição e secrets | Nesta rodada não foi gerado/assinado/executado novo pacote Windows | Build QA reprodutível e revisão SBOM/assinatura, sem publicação |
| GIT | PARTIALLY_TESTED | C: árvore/história alcançável, 1093 caminhos de perfil e scans | Refs remotas inacessíveis/reflogs/forks/cópias externas não inventariados | Inventário de refs autorizado separado; sem reescrever história nesta fase |
| PRIVACY | PARTIALLY_TESTED | C/B: minimização/sanitização tickets/context/telemetria e fixtures | Não acesso a registros pessoais históricos nem aos destinos reais | Avaliar proveniência/retention por metadados e dados artificiais |
| AVAILABILITY | PARTIALLY_TESTED | C/B: /health separado de readiness; erros de dependência e limites locais | Failover e capacidade real fora de escopo | Simulação de falha QA limitada; capacidade em tarefa autorizada separada |
| RESOURCE_EXHAUSTION | PARTIALLY_TESTED | C/B anterior: SD-002 com timeout; limites de corpo e tamanho revisados | Não executar DoS/carga; outros padrões CodeQL unresolved | Análise estática de complexidade e limites em microtestes de orçamento fixo |
| CONCURRENCY | PARTIALLY_TESTED | B/E: dois consumidores recovery, nonce único e idempotência RPC | Interleavings e locks financeiros/sessão completos não verificados | Dois atores sintéticos por transição em banco QA, sem carga |
| ERROR_HANDLING | PARTIALLY_TESTED | C/B/E: 500 CSRF conhecido, 503 Nexa esperado; sem traceback na amostra | Nem todos os erros de I/O/cloud/cancelamento possuem fixture | Fault injection local em adapters, checando saída e estado persistido |
| CONFIGURATION | PARTIALLY_TESTED | C: Docker/render/Supabase/runtime/health/secrets/debug/ports revisados | Não foi lida configuração cloud ou cofre real | Validar modelo de configuração QA contra contratos, sem PROD |
| DEPLOYMENT | PARTIALLY_TESTED | C: render.yaml free, Supabase, sync:false, QA/seed off; sem alterações | Segurança efetiva do ingress e plataforma não é inferível do YAML | Ambiente equivalente QA isolado, sem deploy nesta tarefa |
| WINDOWS_CROSS_USER | NOT_TESTED | Sem execução: DPAPI/ACL entre dois usuários Windows | Não criar contas ou mudar host; fixture atual testa só o próprio usuário | VM com duas contas, chaves e arquivos sintéticos |
| RESTORE_CROSS_INSTALLATION | NOT_TESTED | Sem execução: Restore cruzado com tenant/installation/DPAPI completos | Schema aceita metadados diferentes; isso não prova comprometimento do binding de instalação | Dois bancos/cofres QA; startup e observação de identidade antes/depois |
| WEBVIEW_RUNTIME | NOT_TESTED | Sem execução: WebView2/pywebview empacotado, navegação/bridge/clipboard | Revisão de launcher não equivale a execução gráfica instrumentada | Build QA em VM e captura apenas do próprio PID |
| SUPABASE_FULL_STACK | NOT_TESTED | Sem execução: Auth + gateway + Edge + PostgREST juntos | SQL/Deno/PostgREST foram testados em fronteiras separadas | Stack QA completa sem secrets/URLs de PROD |
| INGRESS_TLS | NOT_TESTED | Sem execução: Certificados, headers e proxy equivalente ao Render | TLS loopback testou cliente Sync, não terminação de entrada | Proxy QA local com certificados próprios e casos de confiança explícitos |
| PROCESS_MEMORY | NOT_TESTED | Sem execução: Leitura de memória/clipboard por outro processo | Fora da coleta defensiva atual; evitar dados de processos reais | VM dedicada com canários fictícios e instrumentação por PID |
| NEXA_LIVE_MODEL | NOT_TESTED | Sem execução: Comportamento de provider/LLM diante de instruções adversas | Nenhum provider externo foi chamado; testes usam doubles | Ambiente do proprietário autorizado, prompts/dados artificiais e política de tools |
| REAL_FAILOVER_CAPACITY | NOT_TESTED | Sem execução: Capacidade, falha de disco e failover sustentados | DoS/carga não autorizados; disponibilidade não inferida de limites unitários | Plano de capacidade separado em recursos descartáveis com orçamento e parada |
| COOKIE_FLAGS | TESTED | B final_auth: Secure/HttpOnly/Strict/Path=/ host-only e max-age em HTTPS simulado | Somente caso delimitado | Demais casos nas categorias acima |
| LOGOUT_REPLAY | TESTED | B final_auth + reprodução anterior: Falha conhecida SD-001 reproduzida; TESTED não significa aprovado | Somente caso delimitado | Demais casos nas categorias acima |
| SESSION_REVOCATION | TESTED | B final_auth: Nova senha/inativação nega sessão; role/grant alterado restringe tenant imediatamente | Somente caso delimitado | Demais casos nas categorias acima |
| CSRF_UNICODE_FAILURE | TESTED | B unicode_csrf anterior: SD-007 reproduzido sem operação nem traceback; não repetido nesta rodada | Somente caso delimitado | Demais casos nas categorias acima |
| BROWSER_TABLE_GRANTS | TESTED | E final_database: 21 tabelas negadas para anon/authenticated via PostgREST local | Somente caso delimitado | Demais casos nas categorias acima |
| PRIVILEGED_RPC_GRANTS | TESTED | E final_database: 11 RPCs negadas para anon/authenticated no fixture | Somente caso delimitado | Demais casos nas categorias acima |
| SYNC_IDEMPOTENCY_PAIR | TESTED | E final_database: Duas chamadas idempotentes armazenam um envelope e mesmo ACK | Somente caso delimitado | Demais casos nas categorias acima |
| SYNC_NONCE_PAIR | TESTED | E final_database: Mesmo nonce em dois clientes: um sucesso e uma rejeição | Somente caso delimitado | Demais casos nas categorias acima |
| SYNC_TLS_CERTIFICATES | TESTED | E final_tls: Não confiável e hostname errado negados; certificado QA explícito aceito | Somente caso delimitado | Demais casos nas categorias acima |
| SYNC_REDIRECT_REFUSAL | TESTED | E final_tls: 302 recusado, destino não recebe request | Somente caso delimitado | Demais casos nas categorias acima |
| GRAPHQL_WEBSOCKET | NOT_APPLICABLE | Nenhuma API GraphQL/WebSocket no escopo ERP/Control Center revisado; ASVS V4.3–V4.4 | Reavaliar se escopo mudar | Inventário em próxima baseline |
| FEDERATED_LOGIN | NOT_APPLICABLE | Login interno não usa OAuth/OIDC/IdP; ASVS V7.6/V10; Supabase Auth externo não certificado | Reavaliar se escopo mudar | Inventário em próxima baseline |
| MFA_PASSKEY | NOT_APPLICABLE | Fluxo atual não implementa MFA/passkey/OOB; V6.5–V6.8; ausência não é aprovação do design | Reavaliar se escopo mudar | Inventário em próxima baseline |
| WEBRTC_MEDIA | NOT_APPLICABLE | Sem WebRTC/TURN/media/signaling neste escopo; ASVS V17 | Reavaliar se escopo mudar | Inventário em próxima baseline |

Total: 39 categorias PARTIALLY_TESTED; 8 superfícies NOT_TESTED;
10 casos delimitados TESTED; 4 grupos NOT_APPLICABLE.
Categorias e subcasos se sobrepõem. Não somar como testes independentes ou requisitos ASVS completos.
