"""Build the final bounded coverage/gap view from reviewed audit evidence.

This is documentation generation, not a scanner or a compliance certification.
"""
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / 'Docs/Security'
# Control, evidence/method, remaining risk/reason, future controlled validation.
partial = [
('AUTHENTICATION', 'C/B: final_auth 149 checks; hash, política, Unicode, inativo; SD-008 SQLite', 'Backend Supabase autenticação e latência não medidos no mesmo harness', 'Fixture PostgREST + adaptador Supabase, contas sintéticas; sem enumeração externa'),
('AUTHORIZATION', 'C/B/E: papéis/grants A/B/vazios, objetos e POST direto; Data API 90 casos', 'Matriz amostral não inclui cada ação de todos os módulos financeiros', 'Inventariar rota × papel × propriedade por módulo em QA'),
('SESSION', 'C/B: fixação, rotação, expiração com relógio, tamper, logout, senha e revogação; SD-001', 'Sem fluxo gráfico de logout ou longa duração em múltiplos workers', 'Browser QA, relógio controlado e dois workers isolados'),
('CSRF', 'C/B: tokens/Origin válidos e inválidos; SD-007; nenhum efeito da operação inválida', 'Nem todo formulário/Content-Type foi verificado em navegador', 'Matriz de formulários e fetch em browser QA, inclusive não ASCII'),
('CORS', 'C/B: allowlist Nexa revisada e testes Deno; Semgrep triado', 'Comportamento do browser e gateway integrado não observado', 'Preflight e credentials em browser contra gateway QA'),
('ORIGIN', 'C/B/E: HTTP local + 500 propriedades origin/Unicode', 'Topologia de proxy real não reproduzida', 'Proxy reverso exclusivamente local com origens configuradas'),
('HOST', 'C/B/E: TrustedHost e URL local negativos', 'DNS rebinding e resolução do WebView não exercitados', 'Cenários DNS sintéticos em VM isolada, sem reconfigurar host real'),
('PROXY', 'C/B: regressão de headers e rate limit; configuração trust revisada', 'Ingress e cadeia de proxies não equivalem ao ASGI simulado', 'Proxy QA com whitelist e conexões diretas/indiretas documentadas'),
('INPUT_VALIDATION', 'C/B/E: vazio, longo, Unicode, tipos/null/datas/IDs, extras e MIME; 258 exemplos anteriores', 'Schemas HTML do gerador são pouco restritivos; amostra não exaustiva', 'Casos determinísticos por schema e limite, sem carga ou payload destrutivo'),
('INJECTION', 'C/B/E: SQL parametrizado, escapes Jinja e entradas inertes; SAST', 'Nem todo sink DOM/SQL/template recebeu fluxo completo', 'Rastrear origem até sink e testar reflexão inerte no browser QA'),
('FILESYSTEM', 'C/B: fixture ACL, traversal, cópias SQLite e DPAPI atual', 'ACL real, junctions e outra conta Windows fora do teste', 'VM descartável com dois usuários e diretórios fictícios'),
('DATABASE', 'C/B/E: integridade SQLite em cópias; PostgreSQL 17.11 e migração original', 'Nem todo constraint/estado financeiro/concorrência foi exercitado', 'Matriz de constraints e transações em bancos QA novos'),
('SUPABASE', 'C/B/E: SQL/pgTAP/Deno prévios e PostgREST 16.2 real interno', 'Não é stack completa Auth/gateway/Edge nem configuração efetiva PROD', 'Stack Supabase inteiramente QA, migração original e dados fictícios'),
('RLS', 'C/B/E: 21 tabelas FORCE/ENABLE; anon/auth negados; grants temporários com rollback A/B', 'Claims reais de emissor e todas as policies em todas operações não percorridos', 'pgTAP por policy/operação e JWT emitido por Auth QA'),
('RPC', 'C/B/E: privilégios, SECURITY DEFINER/search_path; 11 RPCs × 2 roles negativas', 'Falhas de gateway e combinações completas de parâmetros não verificadas', 'Casos limites por assinatura + Edge/gateway local'),
('SYNC', 'C/B/E: 36 regressões Sync/Recovery, 8 Edge; contrato SQL e RPC HTTP', 'Fluxo de instalação empacotada até Edge e ACK não integrado', 'ERP QA offline/online ligado à stack Supabase QA'),
('OUTBOX', 'C/B: retry, dead-letter, restart, retention e identity nos contratos existentes', 'Execução prolongada, falha abrupta e espaço em disco não testados', 'VM QA com volume limitado e interrupções controladas sem dados reais'),
('ACK', 'C/B/E: duplicata/idempotência/nonce via PostgREST; duas chamadas, um registro', 'Todas as combinações de reordenação/perda/restart não simuladas', 'Transport sintético com falhas determinísticas e assert de event identity'),
('OFFLINE', 'C/B: regressões de fila/retry e separação disponibilidade/transação', 'Longa indisponibilidade e reconexão real do pacote ausentes', 'Ciclos de rede em VM local com dados fictícios'),
('RECOVERY', 'C/B: 10 cenários SQL, validade/uso único/revogação/bindings/versão e dois consumidores', 'Cadeia UI/DPAPI/Edge/PostgREST completa não exercitada', 'Duas instalações sintéticas, dois solicitantes e trilha em stack QA'),
('NEXA', 'C/B: HMAC, replay, tool policy, tenant/context, limites/erros/fallback; 166 testes Deno', 'Provider e comportamento de modelo reais não avaliados; --no-check não é typecheck', 'Provider QA controlado, typecheck e cenários sintéticos sem dados pessoais'),
('DESKTOP', 'C/B: 138/139 regressões; auth/Admin Lock/remember; launcher sem js_api, debug off', 'WebView2 empacotado e origem do handle no restore não instrumentados', 'VM Windows com build QA, browser e captura por PID'),
('NETWORK', 'C/B/E: TCPView próprio; 5 testes TLS reais loopback do Sync', 'Recovery/Nexa/ingress/WebView e env proxy não cobertos por esse teste TLS', 'Servidores TLS QA por cliente e inspeção restrita ao processo QA'),
('BACKUP', 'C/B: cópias, schema/integridade/FK, downloads e autorização', 'Dados não cifrados na cópia dependem de ACL; cenário entre contas ausente', 'Fixture de permissões/retention e acesso entre contas em VM'),
('RESTORE', 'C/B: truncamento/view/trigger/future/FK negados; offline separado íntegro', 'Teste ativo falha WinError 5; binding tenant/installation/DPAPI entre instalações não provado', 'Dois fixtures completos; matriz startup/worker/parada, rastrear handle por PID'),
('LOGS', 'C/B: campos sanitizados nos testes e saída genérica de erros', 'Sem inspeção de destinos/retention reais; não todos os caminhos de exceção', 'Canários fictícios por erro e validação de saída em sink QA'),
('OBSERVABILITY', 'C/B: snapshots, campos proibidos, auditoria e retenção nos contratos', 'Código preserva pending além do orçamento; operação offline longa não medida', 'Fila sintética pequena + limite/ACK; capacidade em volume descartável, sem DoS'),
('SECRETS', 'C: árvore/história e final_triage; candidato UUID no blob, sem valor no relatório', 'Realidade/atividade dos históricos desconhecidas; SecretParts em artefato local antigo', 'Proveniência pelo proprietário e metadados de emissão; não usar candidato em provedor'),
('SUPPLY_CHAIN', 'C/E local: inventário, fontes oficiais e módulos do digest; 5 N/A, 254 unresolved', 'Backports e alcançabilidade individual ainda sem prova', 'Revisão por advisory/função/import/build, sem PoC destrutiva'),
('DOCKER', 'C/E local: Dockerfile, Trivy, non-root 10001 e permissões de fonte', 'Digest LIVE, capabilities/mounts efetivos e build provenance não inspecionados', 'Rebuild QA por digest e inventário de imagem/configuração local'),
('BUILD', 'C: PyInstaller/ZIP, lock, regras de distribuição e secrets', 'Nesta rodada não foi gerado/assinado/executado novo pacote Windows', 'Build QA reprodutível e revisão SBOM/assinatura, sem publicação'),
('GIT', 'C: árvore/história alcançável, 1093 caminhos de perfil e scans', 'Refs remotas inacessíveis/reflogs/forks/cópias externas não inventariados', 'Inventário de refs autorizado separado; sem reescrever história nesta fase'),
('PRIVACY', 'C/B: minimização/sanitização tickets/context/telemetria e fixtures', 'Não acesso a registros pessoais históricos nem aos destinos reais', 'Avaliar proveniência/retention por metadados e dados artificiais'),
('AVAILABILITY', 'C/B: /health separado de readiness; erros de dependência e limites locais', 'Failover e capacidade real fora de escopo', 'Simulação de falha QA limitada; capacidade em tarefa autorizada separada'),
('RESOURCE_EXHAUSTION', 'C/B anterior: SD-002 com timeout; limites de corpo e tamanho revisados', 'Não executar DoS/carga; outros padrões CodeQL unresolved', 'Análise estática de complexidade e limites em microtestes de orçamento fixo'),
('CONCURRENCY', 'B/E: dois consumidores recovery, nonce único e idempotência RPC', 'Interleavings e locks financeiros/sessão completos não verificados', 'Dois atores sintéticos por transição em banco QA, sem carga'),
('ERROR_HANDLING', 'C/B/E: 500 CSRF conhecido, 503 Nexa esperado; sem traceback na amostra', 'Nem todos os erros de I/O/cloud/cancelamento possuem fixture', 'Fault injection local em adapters, checando saída e estado persistido'),
('CONFIGURATION', 'C: Docker/render/Supabase/runtime/health/secrets/debug/ports revisados', 'Não foi lida configuração cloud ou cofre real', 'Validar modelo de configuração QA contra contratos, sem PROD'),
('DEPLOYMENT', 'C: render.yaml free, Supabase, sync:false, QA/seed off; sem alterações', 'Segurança efetiva do ingress e plataforma não é inferível do YAML', 'Ambiente equivalente QA isolado, sem deploy nesta tarefa'),
]
untested = [
('WINDOWS_CROSS_USER', 'DPAPI/ACL entre dois usuários Windows', 'Não criar contas ou mudar host; fixture atual testa só o próprio usuário', 'VM com duas contas, chaves e arquivos sintéticos'),
('RESTORE_CROSS_INSTALLATION', 'Restore cruzado com tenant/installation/DPAPI completos', 'Schema aceita metadados diferentes; isso não prova comprometimento do binding de instalação', 'Dois bancos/cofres QA; startup e observação de identidade antes/depois'),
('WEBVIEW_RUNTIME', 'WebView2/pywebview empacotado, navegação/bridge/clipboard', 'Revisão de launcher não equivale a execução gráfica instrumentada', 'Build QA em VM e captura apenas do próprio PID'),
('SUPABASE_FULL_STACK', 'Auth + gateway + Edge + PostgREST juntos', 'SQL/Deno/PostgREST foram testados em fronteiras separadas', 'Stack QA completa sem secrets/URLs de PROD'),
('INGRESS_TLS', 'Certificados, headers e proxy equivalente ao Render', 'TLS loopback testou cliente Sync, não terminação de entrada', 'Proxy QA local com certificados próprios e casos de confiança explícitos'),
('PROCESS_MEMORY', 'Leitura de memória/clipboard por outro processo', 'Fora da coleta defensiva atual; evitar dados de processos reais', 'VM dedicada com canários fictícios e instrumentação por PID'),
('NEXA_LIVE_MODEL', 'Comportamento de provider/LLM diante de instruções adversas', 'Nenhum provider externo foi chamado; testes usam doubles', 'Ambiente do proprietário autorizado, prompts/dados artificiais e política de tools'),
('REAL_FAILOVER_CAPACITY', 'Capacidade, falha de disco e failover sustentados', 'DoS/carga não autorizados; disponibilidade não inferida de limites unitários', 'Plano de capacidade separado em recursos descartáveis com orçamento e parada'),
]
tested = [
('COOKIE_FLAGS', 'B final_auth', 'Secure/HttpOnly/Strict/Path=/ host-only e max-age em HTTPS simulado'),
('LOGOUT_REPLAY', 'B final_auth + reprodução anterior', 'Falha conhecida SD-001 reproduzida; TESTED não significa aprovado'),
('SESSION_REVOCATION', 'B final_auth', 'Nova senha/inativação nega sessão; role/grant alterado restringe tenant imediatamente'),
('CSRF_UNICODE_FAILURE', 'B unicode_csrf anterior', 'SD-007 reproduzido sem operação nem traceback; não repetido nesta rodada'),
('BROWSER_TABLE_GRANTS', 'E final_database', '21 tabelas negadas para anon/authenticated via PostgREST local'),
('PRIVILEGED_RPC_GRANTS', 'E final_database', '11 RPCs negadas para anon/authenticated no fixture'),
('SYNC_IDEMPOTENCY_PAIR', 'E final_database', 'Duas chamadas idempotentes armazenam um envelope e mesmo ACK'),
('SYNC_NONCE_PAIR', 'E final_database', 'Mesmo nonce em dois clientes: um sucesso e uma rejeição'),
('SYNC_TLS_CERTIFICATES', 'E final_tls', 'Não confiável e hostname errado negados; certificado QA explícito aceito'),
('SYNC_REDIRECT_REFUSAL', 'E final_tls', '302 recusado, destino não recebe request'),
]
na = [
('GRAPHQL_WEBSOCKET', 'Nenhuma API GraphQL/WebSocket no escopo ERP/Control Center revisado; ASVS V4.3–V4.4'),
('FEDERATED_LOGIN', 'Login interno não usa OAuth/OIDC/IdP; ASVS V7.6/V10; Supabase Auth externo não certificado'),
('MFA_PASSKEY', 'Fluxo atual não implementa MFA/passkey/OOB; V6.5–V6.8; ausência não é aprovação do design'),
('WEBRTC_MEDIA', 'Sem WebRTC/TURN/media/signaling neste escopo; ASVS V17'),
]

lines = ['# Matriz final de cobertura por fronteira', '',
         '29/09/2026. Continuação de 277a5b2, mesma fonte exportada b09c48e.',
         'C = código/configuração; B = comportamento em processo ou doubles explicitados;',
         'E = protocolo real exclusivamente QA/local. TESTED descreve o caso delimitado,',
         'inclusive se falhou. Não é certificação de segurança nem percentual ASVS.', '',
         'Evidências completas: SECURITY_DISCOVERY_REPORT.md, SECURITY_ASVS_MATRIX.md e',
         'FINAL_EVIDENCE_MANIFEST.csv. Todas as lacunas abaixo constam em SECURITY_COVERAGE_GAPS.md.', '',
         '| CONTROL | Estado | Evidência e alcance | Risco/limite residual | Validação futura |',
         '| --- | --- | --- | --- | --- |']
for control, evidence, reason, future in partial:
    lines.append(f'| {control} | PARTIALLY_TESTED | {evidence} | {reason} | {future} |')
for control, risk, reason, future in untested:
    lines.append(f'| {control} | NOT_TESTED | Sem execução: {risk} | {reason} | {future} |')
for control, method, result in tested:
    lines.append(f'| {control} | TESTED | {method}: {result} | Somente caso delimitado | Demais casos nas categorias acima |')
for control, reason in na:
    lines.append(f'| {control} | NOT_APPLICABLE | {reason} | Reavaliar se escopo mudar | Inventário em próxima baseline |')
lines += ['', f'Total: {len(partial)} categorias PARTIALLY_TESTED; {len(untested)} superfícies NOT_TESTED;',
          f'{len(tested)} casos delimitados TESTED; {len(na)} grupos NOT_APPLICABLE.',
          'Categorias e subcasos se sobrepõem. Não somar como testes independentes ou requisitos ASVS completos.', '']
(DOCS / 'FINAL_COVERAGE_MATRIX.md').write_text('\n'.join(lines), encoding='utf-8')

gaps = ['# Lacunas de cobertura — validação futura', '',
        'Registro final de 29/09/2026. Todas as categorias PARTIALLY_TESTED e superfícies',
        'NOT_TESTED da matriz estão abaixo. Pendência de cobertura não é vulnerabilidade',
        'confirmada nem autorização para remediação/PROD. Priorizar binding de restore,',
        'fronteiras de instalação/cloud e proveniência de secrets; severidade exige evidência.', '',
        '| Controle/superfície | Estado | Risco e motivo | Alternativa já usada | Validação futura |',
        '| --- | --- | --- | --- | --- |']
for control, evidence, reason, future in partial:
    gaps.append(f'| {control} | PARTIALLY_TESTED | {reason} | {evidence} | {future} |')
for control, risk, reason, future in untested:
    gaps.append(f'| {control} | NOT_TESTED | {risk}: {reason} | Ver categoria correspondente acima | {future} |')
gaps += ['', '## Controles ASVS/WSTG ainda parciais ou não executados', '',
         'Esta relação é extraída de todas as linhas parciais/não testadas da matriz ASVS/WSTG.',
         'A limitação em cada linha define o risco residual. A validação futura aplica-se',
         'somente a QA e deve resolver exatamente o limite descrito, usando a categoria',
         'correspondente acima. Não é um inventário de todos os requisitos individuais ASVS.', '',
         '| Referência | Estado | Evidência/limitação | Próxima validação |',
         '| --- | --- | --- | --- |']
for line in (DOCS / 'SECURITY_ASVS_MATRIX.md').read_text(encoding='utf-8').splitlines():
    if not line.startswith('|'):
        continue
    fields = [f.strip() for f in line.strip('|').split('|')]
    if len(fields) != 3 or fields[1].upper().replace(' ', '_') not in {'PARTIALLY_TESTED', 'NOT_TESTED'}:
        continue
    gaps.append(f'| {fields[0]} | {fields[1].upper().replace(" ", "_")} | {fields[2]} | Exercitar em fixture QA o limite descrito; método e restrições nas categorias acima |')
gaps += ['', '## Técnicas não executadas ou limitadas', '',
         '- Procmon: sem captura nativa; alternativa trace Python/ACL de fixture/TCPView por PID. Resta validar handles e WebView em VM.',
         '- mitmproxy/tshark/Burp: não usados para duplicar proxy local; ZAP e TLS loopback cobrem somente parte. Sem captura TLS de instalação real.',
         '- CodeQL: extração Python, não JS/TS. Semgrep/Deno cobrem amostra JS/TS; falta typecheck Nexa e rastreamento DOM.',
         '- Nuclei: dois templates falharam na rodada anterior; cobertura desses casos NOT_TESTED. Revisão de headers e ZAP foram alternativas.',
         '- CVEs: sem PoCs de corrupção/DoS; inventário, fornecedor e imports. 254 ocorrências aguardam alcançabilidade individual.',
         '- Secrets: sem validação de tokens contra provedores, decrypt de perfil ou rotação. Fingerprints/proveniência são as alternativas.',
         '- Sem nova varredura ativa/agressiva, brute force, bypass, DoS ou carga. Testes fixos de entradas inválidas e permissões usam somente fixtures.',
         '- Stack PROD, logs reais e dados pessoais não acessados; nenhuma conclusão sobre configuração efetiva do serviço publicado.', '',
         'Critério de encerramento desta rodada: classificar e documentar esses limites, não',
         'transformá-los em aprovação fictícia. Remediação e validações futuras são tarefas separadas.', '']
(DOCS / 'SECURITY_COVERAGE_GAPS.md').write_text('\n'.join(gaps), encoding='utf-8')
print({'partial_categories': len(partial), 'not_tested_surfaces': len(untested),
       'tested_cases': len(tested), 'not_applicable_groups': len(na)})
