# Final PROD Security Smoke + Release Acceptance

**Resultado: PASS_WITH_KNOWN_LIMITATIONS. Aceite técnico do Control Center publicado.**

Em 30/09/2026, o proprietário confirmou `Deploy succeeded | Live`, startup
completo e Uvicorn ativo no Render para o SHA
`da653f9be3101f67cc50de2df2497295bfd774b2`. Essa confirmação liberou o smoke,
conforme o gate anterior. Nenhum novo deploy foi executado pelo agente.

| Identificação | Valor / origem |
| --- | --- |
| Serviço | `nexpoint-erp-control-center` |
| URL | `https://nexpoint-erp-control-center.onrender.com` |
| Supabase PROD | `scfncgaiovztrbgrcvkt` |
| RC | `1.0.1rc1` |
| Fonte local inicial | `main`, SHA igual ao informado como Live, working tree limpo |
| SHA remoto implantado | Confirmação do proprietário no Render; a API pública não expõe atestado de build |
| Janela principal | Painel: 30/09/2026, 20:07:11–20:08:36 UTC; retry/ACK: 20:09:15–20:09:17 UTC |
| Resultado anterior | QA 1.018/1.018; PASS_WITH_KNOWN_LIMITATIONS, sem regressão confirmada |

## Verificações em PROD

O roteiro principal passou **74/74 verificações**, em 32 requisições HTTPS ao
Control Center, mais um GET HTTP sem credenciais para redirect e um handshake
TLS. Consultas pontuais server-side ao Supabase confirmaram persistência e
preservação; suas credenciais ficaram apenas em memória. A contagem de checks
inclui asserts de headers, preservação e ausência de reflexão de secrets, não
74 cenários de ataque independentes.

| Controle | Resultado observado |
| --- | --- |
| TLS | TLS 1.3 com cadeia e hostname validados; certificado válido até 20/12/2026 |
| HTTP externo | 301 para a mesma URL em HTTPS, sem envio de cookie ou senha no HTTP |
| Liveness | `/health`: 200; `status=ok`, `environment=production`, `storage=supabase` |
| Readiness | `/health/dependencies`: 200; repositório/tabela de sessões/RPC acessíveis |
| Host | Host reservado `.invalid` rejeitado com 403 pelo ingress; não se atribui essa resposta ao middleware interno |
| Headers | CSP, HSTS, nosniff, X-Frame-Options DENY, Referrer-Policy, Permissions-Policy, COOP e no-store conferidos |
| Cookie | `Secure`, `HttpOnly`, `SameSite=Strict`, path `/`, sem Domain ampliado |
| Acesso anônimo | Dashboard redireciona 303 para `/login` |
| CSRF/Origin | Ausente e Unicode: 403; token longo: 422; Origin externo: 403; nenhum 500 |
| Login real | Duas autenticações válidas de `nexpoint-admin`, sem reset ou tentativa de adivinhar senha |
| Sessões | Dois hashes distintos persistidos em `np_platform_sessions` e conferidos via consulta server-side |
| CSRF autenticado | Logout com Unicode rejeitado em 403, mantendo a sessão ativa |
| Logout e replay | Cada logout válido retorna 303, remove sua sessão persistida e o cookie antigo passa a receber 303 para login |
| Independência | Logout A preservou B, comprovado pelo acesso à página e pela sessão no Supabase |
| Limpeza | As duas sessões criadas pelo smoke foram encerradas; sessões preexistentes ainda válidas preservadas |
| Páginas | Dashboard, Empresas, Chamados, Saúde, Diagnóstico, Riscos, Incidentes, Versões, Nexa e Sistema responderam 200; stylesheet servido |
| Dados sensíveis | Nenhuma reflexão dos valores secretos conhecidos nas respostas verificadas; HTML, cookies, senhas e tokens não foram gravados como evidência |

As páginas foram verificadas por HTTP autenticado. Isso comprova as rotas e a
renderização server-side, não substitui um teste visual completo em navegador
desktop/mobile. As respostas administrativas consultam dados reais em memória;
o relatório não publica seu conteúdo.

## Supabase, dados e integração

Uma consulta em transação **read only** confirmou:

- migrations `20260920010000` e `20260929010000` registradas;
- 22 tabelas privadas com RLS e FORCE RLS ativos;
- tabela de sessões sem SELECT/INSERT/UPDATE/DELETE para anon/authenticated;
- service_role com SELECT/INSERT/DELETE e sem UPDATE nessa tabela;
- zero sessões órfãs e zero violações do limite de duração.

A comparação antes/depois do roteiro confirmou preservação do hash de senha,
papel, estado ativo e escopo do administrador; tenants; identidades/estados das
instalações; registros de credenciais de instalação; arquivos locais DPAPI e
arquivo de acesso previamente fornecido pelo proprietário. Não houve rotação.

O SQLite operacional foi aberto somente em `mode=ro`/`query_only`:
`integrity_check=ok`, `foreign_key_check` vazio. A Outbox apresentava 140 itens
synced, sem pendências. O último heartbeat já confirmado localmente era de
27/09/2026; não foi apresentado como heartbeat novo da RC.

Para validar transporte real e idempotência sem criar evento fictício, foi
retransmitido **uma única vez** esse heartbeat já ACKed, usando o cliente oficial,
credencial DPAPI da própria instalação, nonce novo e HMAC. Antes do envio foram
conferidos o envelope e o ACK já existentes no mesmo tenant/instalação.

Resultado: **10/10 checks**, ACK `duplicate=true`, mesmo remote_id e mesma chave
de idempotência. Envelope, linha de ACK, instalação e item da Outbox permaneceram
idênticos. Não houve nova linha de heartbeat ou duplicação de ACK.

Essa prova valida agora Edge/Supabase/assinatura/ACK para um retry real. Não
substitui a execução de um novo heartbeat pelo processo Desktop nem uma nova
reconexão do executável RC; esses cenários permanecem apoiados no QA anterior.

## Efeitos esperados e preservação

- Login atualiza `last_login_at` e o timestamp relacionado do usuário, cria sua
  sessão e pode limpar sessões já expiradas pelo contrato normal do produto.
- Logout remove somente as sessões do smoke; demais sessões ainda válidas foram
  comparadas e preservadas.
- O retry autenticado registra nonce/telemetria técnica normal de requisição.
- GETs, autenticação e negativas esperadas podem produzir logs normais do
  provedor; não se tentou apagar ou alterar esses logs.
- Nenhuma senha, role, tenant, instalação, chamado, incidente, registro financeiro
  ou dado de cliente foi alterado para o teste.
- Nenhuma migration adicional, deploy, indisponibilidade induzida ou rollback.
- Os nove arquivos cifrados do backup pré-release mantêm os hashes registrados.

## Limitações e critérios de aceite

O smoke não confirmou falha crítica/alta explorável, retorno de finding
remediado, sessão revogada reutilizável, quebra de integridade ou exposição de
secret. O gate técnico da release permanece **PASS_WITH_KNOWN_LIMITATIONS**.

Os resíduos SD-003 histórico, SD-004 e SD-005 continuam nos estados anteriores.
SD-006 permanece NOT_APPLICABLE. DPAPI/ACL entre contas Windows, memória/clipboard,
capacidade/failover e provider/modelo Nexa real não ganharam aprovação presumida.
Não foram repetidos fuzzing, brute force, DAST, carga, troca de senha/role ou
campanha de isolamento entre tenants em PROD. O platform_admin tem escopo global;
o acesso dele não comprova a matriz de acesso do papel restrito, validada em QA.

A página Nexa abriu em 200. Não houve consulta ao modelo real nem desligamento
proposital da integração em PROD. A falha isolada/offline da Nexa foi exercitada
na etapa QA/RC; o gate pós-deploy genérico não autoriza provocar indisponibilidade
real. A consulta a readiness também não prova disponibilidade do provider Nexa.

O SHA Live foi confirmado pelo proprietário. TLS/rotas/sessões provam o
comportamento publicado observado, mas não atestam independentemente o digest
de imagem do Render. Tempos individuais constam na evidência; não são SLA nem
ensaio de capacidade. A RC Windows continua gerada/testada, sem instalação sobre
o Desktop real nem publicação automática de atualização nesta etapa.

## Evidência e rastreabilidade

Artefatos locais ignorados, somente resultados sanitizados e hashes publicados:

| Artefato em `artifacts/security/prod-smoke/` | Resultado |
| --- | --- |
| `run-b6c5f72c62/result.json` | 74/74 checks; sessões próprias encerradas |
| `metadata-454c21e3c7/result.json` | Schema/RLS/migration, integridade SQLite e nove backups preservados |
| `sync-9adf352477/result.json` | Retry real, 10/10 checks, sem duplicação |
| `run-9148df61b7/result.json` | Primeira passagem interrompida antes do login: roteiro esperava 400/404/421 para Host, ingress respondeu 403; ajustada a expectativa, produto inalterado |

O [manifesto de evidências](PROD_SMOKE_EVIDENCE_MANIFEST.csv) fixa hashes e tamanho.
Os 134 hashes registrados nos quatro manifestos anteriores foram reconferidos
sem divergência. Secret scan `run-1876c13412`: 399 candidatos, zero violações do
guard, Gitleaks 12/TruffleHog 1 iguais aos candidatos já triados. Bandit do novo
roteiro: somente três LOW B404/B603/B607 relativos a `git rev-parse HEAD`, com
argv fixo e sem shell; depende do PATH local confiável e não recebe entrada HTTP.
Não são novos alertas do runtime. Sintaxe, links e `git diff --check` conferidos.
Não se repetiu a suíte 1.018/1.018: o código do produto e seus contratos não mudaram;
a evidência QA anterior foi preservada e o roteiro novo foi executado em PROD.

Roteiro principal: `scripts/security/prod_security_smoke.py`, com autorização
explícita, alvo/SHA fixos, orçamento de requisições e limpeza das próprias sessões.
Reexecução futura exige nova revisão de baseline; não executar por rotina.

O setor independente de revisão não conseguiu iniciar por limite de uso da
ferramenta. A coordenação executou a revisão local; não se declara segunda revisão
independente concluída. O histórico e os artefatos anteriores foram preservados.

Somente documentação e roteiro de validação são adicionados ao Git nesta etapa.
Runtime, Blueprint, migrations e secrets do produto permanecem inalterados;
**não é necessário outro deploy** para registrar este aceite. Nenhum rollback
foi necessário. A referência anterior continua
`37f18e531488590b369a9ab30b9c7be321ec33b7`; manter a migration aditiva em eventual
rollback, conforme [release notes](../RELEASE_1.0.1rc1.md).
