# Gate Windows QA - retomada apos Hyper-V

Estado: **NO-GO** para instalar no notebook real. Este registro substitui o
estado ambiental antigo de `WINDOWS_QA_ENTREGA.md`, sem alterar a entrega Nil
lav ou qualquer servico PROD.

A VM descartavel `NexPoint-Windows-QA` iniciou Windows 11 Enterprise Evaluation
com um usuario Windows local. A interface de rede usa somente o switch interno
`NexPoint QA HostOnly`, IP `10.254.241.2/28` e nenhuma rota padrao. O WebView2
Runtime esta presente no guest. O host QA usa `10.254.241.1/28`.

O Supabase local `nexpoint-erp-handoff-qa` tem banco/volume, rede e chaves QA
exclusivos. Kong e PostgreSQL publicam apenas no loopback do host. O Caddy
escuta apenas em `10.254.241.1:56430` com TLS e CA local QA. O preflight da
origem passou 18 verificacoes, inclusive separacao de banco, assinatura de
chaves e rejeicao de CA/SNI errados. A VM recebeu HTTP 200 sobre HTTPS da
origem QA. PROD nao foi acessado.

Uma regra automatica do Windows bloqueava o executavel Caddy no perfil Publico.
`tools/qa/allow-qa-caddy.ps1` desativou somente essa regra TCP, apos validar
que o bloqueio padrao Publico permanece ativo e que a liberacao do gateway
vale apenas para a VM/porta 56430. A regra UDP continuou bloqueada. O script
tem `-Restore`; o nome da regra anterior fica apenas em artefato QA ignorado
pelo Git.

O perfil de build QA fixa origem HTTPS QA, canal `internal`, instalacao
`qa-win-clean-04` e dados em `%LOCALAPPDATA%\NexPoint\ERP-QA`. Os scripts de
build copiam somente arquivos versionados, exigem perfil publico QA, verificam
o destino compilado e inspecionam os executaveis por referencias PROD, dados
locais e secrets. O Setup QA usa AppId e pasta diferentes do Setup PROD. O
backend QA conserva os marcadores legados `prod/PROD` no contrato; o cliente
traduz uma copia dos eventos apenas no transporte, enquanto o SQLite local
continua identificado como `qa/QA`.

O smoke preliminar consumiu o codigo one-time de `qa-win-clean-01` e rejeitou
reutilizacao. A primeira instalacao Windows com `qa-win-clean-02` tambem passou
instalacao, ativacao, Empresa e Admin Lock. A sincronizacao revelou que o
sanitizador confundia parte do SHA do commit com um numero de cartao; o
heartbeat era recusado. A correcao preserva somente SHA de 40 hexadecimais no
envelope. A instalacao `-02` e sua evidencia permanecem no QA.

A instalacao limpa `qa-win-clean-03` passou ativacao, Empresa, ausencia de
login operacional, Admin Lock, Recovery e heartbeat/sync na Build A 1.1.0.
A atualizacao pelo aplicativo para a Build B 1.1.1 detectou, baixou, conferiu
hash, instalou e reiniciou. SQLite, dados QA, Empresa, Admin Lock, credencial
DPAPI e sync sobreviveram. Entretanto, o heartbeat ainda relatou `app_version`
1.1.0 apos a atualizacao, embora o executavel ja fosse 1.1.1. A inicializacao
persistia `app.version` somente no primeiro uso. A correcao sincroniza apenas
essa configuracao de versao do pacote na reinicializacao de uma instalacao
cloud, sem substituir configuracoes ou dados da empresa.

O release QA 1.1.1 ja publicado e imutavel no catalogo local. O reteste final
usara instalacao limpa `qa-win-clean-04`, Build A 1.1.0 e Build B 1.1.2, com
novo codigo one-time emitido apenas no QA. A evidencia `-03` continua
preservada; nenhum codigo ou credencial foi colocado em Git. O gate permanece
**NO-GO** ate a verificacao da telemetria da Build B e dos cenarios de falha
na VM limpa. Nenhum gate pendente vira PASS apenas por teste unitario.

## Reteste da instalacao `qa-win-clean-04`

Commit local `020842696c62076d975982290f907961c36c41db`, sem push.
Build A 1.1.0 e Build B 1.1.2 passaram pelos verificadores de distribuicao e
secret scan. A VM foi restaurada ao checkpoint anterior ao ERP; sem executavel
ou SQLite, sem rota padrao, HTTPS QA valido. O instalador A saiu com codigo 0.

- Ativacao: PASS, codigo one-time consumido; reuso via Edge QA retornou 409.
- Empresa: PASS, nome `NexPoint QA Lab 04` gravado localmente e no QA cloud.
- Login operacional: ausente, `/login` retornou 404.
- Admin Lock: PASS para bloqueio, senha incorreta e desbloqueio.
- Recovery: PASS para chamado sincronizado, autorizacao web QA com rejeicao
  CSRF, consumo da autorizacao e rejeicao da senha anterior.
- Heartbeat/sync A: PASS, versao 1.1.0, build e SHA completos, fila zerada.
- Update A para B: PASS para deteccao internal, download, SHA-256 do Setup B
  `0693882a45c6091f07343b9dae215f72d9ed3244f948eb610875b77991c07dd3`,
  instalacao pelo ERP, reinicio e executavel 1.1.2. O heartbeat cloud passou a
  informar 1.1.2, confirmando a correcao de `app.version`.
- Preservacao: PASS, SQLite `integrity_check=ok`, `foreign_key_check` vazio,
  Empresa e cliente ficticio mantidos, Admin Lock presente, credencial DPAPI
  com SHA-256 identico antes e depois. Sync voltou a online apos reinicio.
- Sem rede: origem QA ficou inacessivel enquanto o ERP local respondeu 200;
  apos restaurar a interface, o QA HTTPS respondeu 200 e pendencias retornaram
  a zero. A API de status exibiu `online` por cache durante a desconexao curta.
- Update repetido: verificacao e tentativa de novo download mantiveram
  `CURRENT`, sem reinstalacao.
- Hash invalido: PASS nos testes automatizados de download/estado, ainda nao
  exercitado por adulteracao de pacote na interface da VM.
- UAC cancelado: nao aplicavel ao instalador QA de escopo current-user.

**Bloqueio confirmado:** durante A para B, tres eventos `health` receberam
`HTTP 400 invalid_payload` e ficaram em `dead_letter` com uma tentativa. Outros
eventos e os heartbeats posteriores sincronizaram; o estado final observado foi
`online`, zero `waiting`, zero `sending`, tres `dead_letter`. Os payloads
rejeitados passaram depois pelas regras de formato e por replay transacional
no RPC do banco QA com rollback, portanto a causa original ainda nao foi
reproduzida. Nao houve exclusao, reenvio forcado ou alteracao manual desses
itens. A fila de telemetria incompleta impede GO.

A VM Windows Evaluation tambem desligou por `wlms.exe` (evento System 1074).
Foi reiniciada; a sessao e o ERP voltaram, e o logon automatico temporario
foi desativado. Essa limitacao do laboratorio precisa ser resolvida antes de
usar a VM para um ciclo de teste prolongado.

Gate final: **NO-GO**. Proximo passo: diagnosticar e corrigir a causa dos
`health` rejeitados, repetir A para B em uma instalacao QA limpa e confirmar
`dead_letter=0`; depois testar adulteracao do instalador pela interface da VM.
Nao usar o notebook real nem modificar a entrega Nil lav ate esse reteste.
