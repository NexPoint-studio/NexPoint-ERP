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
