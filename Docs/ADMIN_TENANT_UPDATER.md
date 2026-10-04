# Administração, Nil lav e atualização do ERP

## Auditoria inicial — 03/10/2026

Base `7fd497e`, branch main, Git limpo antes desta tarefa. Remote confirmado:
`NexPoint-studio/NexPoint-ERP`. O endereço antigo em AGENTS é histórico; não foi
alterado remote nem utilizado outro checkout. O Ascent não pertence a esta tarefa.

| Superfície | Estado | Evidência e decisão |
| --- | --- | --- |
| Administração | PRECISA CORRIGIR | `navigation_context` filtra por `admin.overview.view`; usuário operacional não recebe essa permissão. Não é ausência do módulo. |
| Notas e Financeiro | EXISTE / acesso parcial | Notas estão sob Serviços; Financeiro está sob Administração. Empresa/Usuários têm visibilidade desativada no catálogo de navegação. Restaurar destinos existentes, sem duplicar módulos. |
| Admin Lock | PARCIAL | Hash scrypt, versão, sessão temporária, expiração, rate limit e geração por processo existem. Ausência de senha exige configuração pelo papel admin; regra nova exige estado UNPROTECTED. Remoção de proteção ainda não existe. |
| Admin Recovery | EXISTE / ajustar elegibilidade | Solicitação via suporte, autorização remota com escopo/expiração/consumo único e auditoria existem. Restrição ao proprietário impede o usuário operacional de solicitar reset. Não criar outra recuperação ou senha mestre. |
| Tenants/instalações | EXISTE / reassociar | FKs compostas e RLS existem. PROD tem NexPoint com duas instalações. Nil lav ainda pertence a nexpoint. |
| Versão instalada Nil lav | EXISTE / confirmada na nuvem | 1.1.0, build PROD-eef7592645a7, heartbeat 03/10/2026 22:10 UTC. Ativação consumida em 03/10/2026 21:13:39 UTC. Isso não comprova inspeção do SQLite físico. |
| Publicação de versões | NÃO EXISTE | Versões agrupa telemetria recebida; `np_installation_versions` não é catálogo de releases. |
| Update client/download | NÃO EXISTE | Implementar sobre instalação e identidade existentes; sem secrets privilegiados no cliente. |
| Hash/assinatura | PARCIAL | Build/entrega registram hashes, mas não há verificador de atualização. Certificado oficial ainda não confirmado; não anunciar assinatura sem evidência. |
| Rollback | PARCIAL | Backup/restauração SQLite existem. Rollback de programa após update não existe. |
| Programa/dados | EXISTE | Inno instala por usuário, com AppId fixo e privilégios mínimos. Dados e DPAPI ficam fora da pasta do programa. Preservar essa fronteira. |

A leitura PROD confirmou somente tenant `nexpoint`, instalações `nexpoint-pc-01`
e `nil-lav-pc-01`, e grant consumido de `nillavlavanderia` no tenant antigo.
Contagens sanitizadas iniciais da Nil lav: 1 credencial, 25 envelopes/25 ACKs,
7 heartbeats/7 snapshots/7 versões, 11 eventos de observabilidade/11 diagnósticos,
3 fingerprints e nenhum chamado, recovery, risco ou incidente nas tabelas consultadas.
Evidência completa, sem secrets: `artifacts/admin-tenant-updater-20261003/prod-audit-sanitized.json`.
Contagens devem ser atualizadas imediatamente antes de qualquer mudança PROD.

## Fronteiras obrigatórias

O notebook já está ativado. Não basta mudar `np_installations`: há tabelas filhas
com FKs compostas, grant, tenant na DPAPI, derivação de sessão e payloads pendentes
na outbox. A reassociação precisa preservar o segredo, identidade da instalação,
dados locais e contexto de filas, com handoff autenticado e plano de rollback.
Nenhuma alteração PROD foi realizada na auditoria inicial. Não declarar migração
concluída sem confirmar o novo contexto de heartbeat/sync do notebook.

`channel` existente identifica ambiente LOCAL/QA/PROD e tem constraints rígidas.
O canal de distribuição stable/beta/internal terá campo separado `update_channel`.
Não reinterpretar registros históricos nem enfraquecer isolamento de ambiente.

Administração será visível aos usuários normais; operações continuarão verificando
Admin Lock no servidor. Suporte temporário permanece limitado e não vira usuário
normal com acesso completo. Senha administrativa e senha de login são separadas.
Sem proteção, Administração abre; com proteção, autorizações expiram e são
invalidadas em saída, troca de usuário, logout, reinício e mudança de senha.

## Gates de entrega

Testar em QA antes de PROD; backup verificado antes de schema/reassociação;
migrações aditivas; integridade/FKs; publicação de release somente com artefato,
hash/tamanho e origem aprovados. Nunca substituir banco/DPAPI no update nem
fazer downgrade de schema silencioso. Teste no PC do desenvolvedor não comprova
aceite no notebook. Commit/push/deploy só após testes e revisão de secrets.

## Implementação local em andamento

Administração agora é visível para usuários locais normais. O Admin Lock possui
estado sem proteção, configuração, mudança e remoção com verificação da senha
atual; recuperação usa o contrato one-time existente. Escopo de suporte,
atribuição de papéis superiores e restauração permanecem limitados. O Caixa
operacional mantém a consulta dos próprios lançamentos recentes quando a
Administração está bloqueada; visão financeira global continua protegida.
Ver [CADEADO_ADMINISTRACAO.md](CADEADO_ADMINISTRACAO.md).

A migration `20261004010000_official_releases.sql` adiciona catálogo de releases
e `update_channel`, sem reinterpretar `channel`. Há rascunho, verificação do
arquivo e publicação monotônica por canal. RPCs conferem administrador ativo;
consulta pelo ERP verifica credencial da instalação, escopo, nonce, expiração e
limite de frequência. Tabelas têm RLS forçada e nenhum acesso anon/authenticated.
Ela passou em 51 verificações de PostgreSQL descartável com `--network none`.
Ainda não foi aplicada em PROD.

`erp-update` e `app/updates/remote.py` usam domínios HMAC próprios, diferentes do
Admin Recovery, e vinculam resposta à requisição, instalação e tenant. Download
aceita somente a origem oficial e objeto correspondente ao SHA-256, sem
redirecionamentos, com URL de curta duração. Tamanho, hash, canal, versão e
sequência são validados. Foram aprovados 25 testes Edge e 67 testes de cliente e
download. Os testes usam transporte sintético e nunca executam os bytes de QA.

O verificador Authenticode consulta o Windows sem executar o arquivo. Releases
assinadas precisam ter assinatura válida e certificado esperado; assinatura
inválida não vira CODE_SIGNING_PENDING. Sem certificado oficial, a cadeia usada
é a origem HTTPS, consulta autenticada/assinada e hash do arquivo verificado.
Não foi emitido nem inventado certificado de code signing.

Control Center ganhou catálogo persistente (schema local 8) e publicação apenas
de arquivos verificados. O upload server-only usa objeto privado identificado
pelo hash, sem sobrescrita, e baixa novamente o objeto para verificar os bytes
armazenados antes de marcá-lo como verificado. Catálogo/UI/upload ainda estão
integrados localmente. A CLI `scripts/prepare_official_release.py` valida
instalador, relatório de build com updater incluído, tamanho, hash e assinatura.
Sem `--upload`, executa apenas prévia local; não lê o cofre e não acessa a nuvem.
Com `--upload`, usa o cofre DPAPI existente do responsável, exige platform_admin
ativo e prepara o arquivo verificado. Publicar continua sendo uma ação explícita
em Versões. Nenhuma publicação real foi executada nesta etapa.

O bucket privado e a preparação limitam cada arquivo a 50 MiB para compatibilidade
com o limite do [Supabase Free](https://supabase.com/docs/guides/storage/uploads/file-limits).
O teto defensivo do protocolo de download não aumenta a quota do provedor.
Conferir tamanho do Setup final, espaço total, tráfego e políticas do bucket antes
de publicar. A build do helper de QA tem cerca de 20 MB; isso não mede o Setup final.

O helper Windows separado é copiado para fora da pasta do programa e recebe um
job protegido por DPAPI, ligado à instalação, paths, release e prazo. A mesma
autorização não inicia dois helpers nem dois processos de health. Mutex impede
concorrência de processos para a instalação. Antes de executar o instalador há
verificação de tamanho/hash/Authenticode, encerramento do ERP, espaço disponível,
backup SQLite consistente e backup binário verificado. Restore pendente bloqueia
o update. O health inicia o programa novo com restore desativado, confere versão,
commit, banco, identidade, credencial e login loopback, e devolve recibo HMAC.

Falha com schema/credencial intactos permite recuperar somente os binários por
rename no mesmo volume. Schema alterado exige recuperação assistida; o helper
nunca sobrescreve SQLite usando o backup. Reinício recusado ou processo encerrado
cedo registra falha. A próxima abertura registra o resultado na outbox antes da
próxima consulta. Sem aplicação aberta ou sem conectividade, o painel recebe o
resultado quando o fluxo normal de sync puder continuar.

O worker consulta após 15 segundos de abertura e depois a cada seis horas;
download e instalação dependem da pessoa. Avisos aparecem na navegação e em
Administração → Sistema. As rotas mutáveis continuam protegidas pelo Admin Lock,
CSRF e validação de campos. O heartbeat existente transmite somente estado/canal
allowlisted e horário da mudança; a nuvem não aceita troca de canal pelo cliente,
horário futuro excessivo ou estado mais antigo. Falha de conexão não para o ERP.

## Evidência local consolidada em 04/10/2026

- Suíte completa: 1182 testes aprovados; verificações focadas posteriores cobrem
  reforço do scan comprimido e limite de tamanho do Storage.
- PostgreSQL 17 descartável, sem rede: 63 verificações, incluindo ingestão real
  via RPC, escopo, ordenação de estados e definição do bucket privado. A tabela
  storage.buckets do harness é mínima; não substitui smoke da API Storage real.
- Edge update + sync: 29 testes aprovados.
- Transação/helper/UI: 31 verificações focadas aprovadas, incluindo DPAPI real
  com material sintético e exclusão entre dois processos pelo mutex do Windows.
- Secret scan de candidatos Git aprovado; scan do helper inspecionou também
  CArchive/PYZ comprimidos sem executar conteúdo. Nenhuma credencial foi embutida.
- Helper QA compilado com PyInstaller: `--verify-runtime` retornou 0 após carregar
  as dependências, sem rede ou instalação. Authenticode retornou NotSigned,
  consistente com CODE_SIGNING_PENDING. O artefato QA não é uma release oficial.
- Gitleaks: 13 alertas em conteúdo já presente em HEAD; fixtures/exemplos e três
  hashes confirmados como objetos Git. TruffleHog: uma URI de fixture já triada,
  sem verificação em provedor. Nenhum candidato novo de segredo nesta mudança.
- Bandit executado: no updater, sete alertas LOW relacionados a import/subprocess;
  comandos são arrays sem shell, executáveis locais fixos ou verificados e job
  autenticado. SQL dinâmico preexistente e harness sintético permanecem separados
  dessa análise. Execução de scanner não significa zero alertas no projeto.

Ainda faltam instalação completa em Windows QA descartável, validação do Setup
final e publicação/ativação remota após todos os gates. Compilar o helper, testar
DPAPI/mutex reais e simular callbacks não comprova atualização completa instalada.
Nenhum pacote foi enviado à Nil lav e nenhum deploy/commit/push ocorreu nesta etapa.

A reassociação da Nil lav está detalhada em
[TENANT_REASSOCIATION_PLAN.md](TENANT_REASSOCIATION_PLAN.md), incluindo 16 tabelas,
18 FKs, preservação de ACKs, contexto de sessão/recovery e handoff recuperável.
O plano ainda precisa ser implementado e validado; acesso e backup do notebook
continuam pendentes. Não declarar a Nil lav reassociada somente pela nuvem.

Referências de implementação: [Storage privado](https://supabase.com/docs/guides/storage/buckets/fundamentals),
[Get-AuthenticodeSignature](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.security/get-authenticodesignature),
[parâmetros do Inno Setup](https://jrsoftware.org/ishelp/topic_setupcmdline.htm).
