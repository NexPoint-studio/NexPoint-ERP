# Cobertura ASVS / WSTG

> Atualiza??o de 30/09/2026: o [reteste em andamento](SECURITY_RETEST_REPORT.md) e a
> [matriz de cobertura atual](RETEST_COVERAGE_MATRIX.md) complementam esta fotografia
> hist?rica. As evid?ncias anteriores s?o preservadas; n?o representam o gate da release.

Referências: [OWASP ASVS 5.0.0](https://owasp.org/projects/asvs) e
[WSTG 4.2](https://wstg.owasp.org/v4.2/). IDs ASVS conferidos no JSON oficial
da versão 5.0.0, preservado em `artifacts/security/asvs5.json`.
Esta é uma matriz por família e por controles selecionados, **não certificação de
nível ASVS** nem checklist completo de todos os requisitos individuais.

`tested` significa que o caso descrito foi executado, inclusive quando encontrou
falha. `partially tested` indica amostra ou combinação de teste e revisão estática.
`not tested` explicita falta de evidência; `not applicable` depende da ausência
da funcionalidade no escopo atual, não de scanner silencioso.

| ASVS 5 | Estado | Evidência / limite |
| --- | --- | --- |
| V1.1–V1.3 | partially tested | Bandit/CodeQL, escapes Jinja, 135 verificações HTTP, sanitização Nexa. SD-002; nem todos os contextos de saída exercitados. |
| V1.4 | partially tested | Inventário de bibliotecas nativas; nenhuma exploração de memória. |
| V1.5 | partially tested | JSON malformado/validações Edge, backups SQLite; não todos os parsers. |
| V2.1 | partially tested | Contratos e testes existentes lidos, sem revisão formal de toda regra financeira. |
| V2.2 | partially tested | 258 exemplos/rodada API, limites de corpo, Unicode, SD-007; gerador HTML possui schemas pouco restritivos. |
| V2.3 | partially tested | Recovery, ACK/idempotência e restore; carga financeira/conflitos longos não simulados. |
| V2.4 | partially tested | Limite de 10 tentativas de login QA e headers de proxy; sem brute force, DoS ou distribuição. |
| V3.1–V3.2 | partially tested | Templates/configuração/MIME estáticos e HTTP local; DOM real não percorrido por browser nesta rodada. |
| V3.3.2 | tested | SameSite=Strict no cookie da sessão do painel em QA HTTPS simulado. |
| V3.3.4 | partially tested | HttpOnly observado; não inventariado todo canal de saída de todos os cookies do desktop. |
| V3.3.1, V3.3.3, V3.3.5 | partially tested | Secure observado no painel; nome atual sem prefixo __Host-/__Secure-. Limite máximo de cookie não exercitado. Não declarar conformidade integral. |
| V3.4 | partially tested | CSP, HSTS simulado, XFO, no-store/nosniff; ausência de headers adicionais não prova exploit. |
| V3.5 | partially tested | Origin/Referer/Host/CSRF negativos e CORS Nexa allowlist; sem DNS rebinding real. |
| V3.6–V3.7 | partially tested | Static/JS e redirecionamentos revisados, sem navegador end-to-end/extensões reais. |
| V4.1–V4.2 | partially tested | Métodos, tipos/corpos, tamanho e exposição de OpenAPI em QA; sem request smuggling em proxy real. |
| V4.3–V4.4 | not applicable | Nenhuma API GraphQL/WebSocket no escopo ERP/Control Center examinado. |
| V5.1–V5.4 | partially tested | Restore/download, traversal, arquivo truncado, views/triggers, schema/FK. Restauração entre instalações reais não testada. |
| V6.1–V6.3 | partially tested | Hash scrypt, política, Unicode, inativo e resposta genérica; final_auth 149 checks e timing local SD-008. Supabase remoto/estatística de enumeração não medidos. |
| V6.4 | partially tested | Recovery: vínculo incorreto, expiração/revogação, uso único e 2 consumidores locais; PostgREST/Edge completo não exercitado. |
| V6.5–V6.8 | not applicable | MFA/OOB/passkeys/IdP não integram o fluxo de login interno auditado. Não significa que MFA seja dispensável. |
| V7.1–V7.3 | partially tested | Sessão assinada, credencial/geração, remembered session, expiração em testes; não todos os limites de tempo. |
| V7.4.1 | tested | **Falhou no logout do Control Center: SD-001.** Não confundir com autorização de senha nova. |
| V7.4.2–V7.4.5 | partially tested | final_auth confirma inativação/senha, demotion/grant e chave diferente; não inventariada toda UI de encerramento de sessões. |
| V7.5 | partially tested | CSRF e elevação Admin Lock; comportamento sob sessão concorrente prolongada não testado. |
| V7.6 | not applicable | Sem autenticação federada no fluxo interno auditado. |
| V8.1–V8.4 | partially tested | platform_admin/control_admin, A/B/vazios, 404, POST direto e campos extras; 90 casos PostgREST/RPC/RLS. Matriz não cobre todos os módulos financeiros. |
| V9.1–V9.2 | partially tested | Integridade da sessão e HMAC de pontes; JWT Supabase apenas contrato QA, emissor remoto não exercitado. |
| V10.1–V10.7 | not applicable | Control Center usa credencial interna, não servidor/cliente OAuth/OIDC; infraestrutura Supabase Auth remota fora do alvo. |
| V11.1–V11.2 | partially tested | Revisão scrypt/HMAC/DPAPI e material fictício. Não auditoria criptográfica independente. |
| V11.3–V11.4 | partially tested | DPAPI usuário atual, hashes e assinaturas/replay nos contratos. Separação entre contas Windows não testada. |
| V11.5 | partially tested | Fontes de aleatoriedade revisadas; B311 em jitter/QA, sem teste estatístico de RNG. |
| V11.6–V11.7 | not tested | Gestão de certificados e proteção contra leitura de memória do processo não verificadas. |
| V12.1–V12.3 | partially tested | Fonte exige HTTPS/TLS>=1.2; 5 checks TLS reais loopback do Sync (certificado, hostname e redirect). Sem ingress Render nem TLS de todos os outros clientes. |
| V13.1–V13.4 | partially tested | Docker/Render estáticos, scans árvore/histórico, flags de produção. SD-003/004; sem consulta de secrets/cloud reais. |
| V14.1–V14.3 | partially tested | Sanitização e campos proibidos em snapshots/Nexa, ACL de fixture e no-store. Retenção/acesso Windows real não auditados. |
| V15.1–V15.3 | partially tested | Baseline/scans; SD-005 UNRESOLVED, SD-006 NOT_APPLICABLE no fluxo atual, 254 advisories ainda sem alcançabilidade individual. Não revisão linha a linha de todo o produto. |
| V15.4 | partially tested | 2 consumidores Recovery; pares idempotência/nonce no PostgREST real, um envelope/um consumo. Não prova ausência de todas as races. |
| V16.1–V16.4 | partially tested | Auditoria e sanitização com dados fictícios, falhas de telemetria separadas da transação. Destino/logs reais não lidos. |
| V16.5 | partially tested | Erros genéricos, falha de dependência 503 e SD-007; não todos os caminhos de exceção. |
| V17.1–V17.3 | not applicable | Sem WebRTC/TURN/media/signaling no escopo. |

## WSTG 4.2 — procedimentos relacionados

| Família/caso | Estado | Evidência e fronteira |
| --- | --- | --- |
| INFO | partially tested | Inventário por fonte/rotas/OpenAPI local; nenhuma enumeração externa. |
| CONF | partially tested | Docker/Render, defaults, método HTTP e arquivos estáticos; deploy e rede cloud não verificados. |
| IDNT | partially tested | Usuários fictícios/papéis/inativação; ciclo organizacional real fora do escopo. |
| ATHN | partially tested | Login/hash/limite/Recovery e SD-008 timing SQLite em sete pares fixos; sem backend cloud, senha real ou enumeração remota. |
| ATHZ-01/02/03/04 | partially tested | Traversal, bypass, papel e IDOR com dois tenants, POST direto e RLS local. |
| SESS-01/02/03 | partially tested | Integridade/flags/fixação via regressões, apenas topologias locais. |
| SESS-05 | tested | Origin e CSRF da bateria negativa; SD-007 é erro de validação, não bypass. |
| SESS-06 | tested | Replay após logout reproduzido, SD-001. |
| SESS-07 | partially tested | Expiração nos testes de contrato; sem espera pelo prazo máximo real. |
| INPV | partially tested | SQLi/reflexo/XSS/SSTI/poluição/Unicode/corpos limitados. Nenhuma exploração externa. |
| ERRH | partially tested | Respostas sem traceback, exceção CSRF confirmada, fixtures de falha Nexa/Sync. |
| CRYP | partially tested | Hashes/DPAPI e handshake TLS Sync loopback com CA fictícia; sem certificados PROD ou isolamento Windows entre contas. |
| BUSL | partially tested | Idempotência, replay, recuperação e restore QA. Sem campanha destrutiva financeira. |
| CLNT | partially tested | Fonte de templates/JS/pywebview; runtime gráfico completo não testado. |

## Limites que exigem outra rodada de validação

PostgREST e Edge Functions juntos em stack Supabase QA completa; TLS/headers no
ingress equivalente ao Render; pacote Windows/WebView2 com instrumentação nativa;
ACL/DPAPI entre dois usuários Windows; identidade cruzada de backup; backend Supabase
de login; revisão individual de 254 ocorrências SCA. PostgREST isolado e TLS Sync
loopback foram acrescentados, sem fechar os gaps de integração. Todos os controles
parciais/não testados estão em [SECURITY_COVERAGE_GAPS.md](SECURITY_COVERAGE_GAPS.md).
Esses limites não impedem
registrar os achados desta rodada e não autorizam testes em PROD.
