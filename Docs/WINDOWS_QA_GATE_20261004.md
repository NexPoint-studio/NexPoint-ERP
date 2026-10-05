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
`qa-win-clean-02` e dados em `%LOCALAPPDATA%\NexPoint\ERP-QA`. Os scripts de
build copiam somente arquivos versionados, exigem perfil publico QA, verificam
o destino compilado e inspecionam os executaveis por referencias PROD, dados
locais e secrets. O Setup QA usa AppId e pasta diferentes do Setup PROD. O
backend QA conserva os marcadores legados `prod/PROD` no contrato; o cliente
traduz uma copia dos eventos apenas no transporte, enquanto o SQLite local
continua identificado como `qa/QA`.

O smoke preliminar consumiu o codigo one-time de `qa-win-clean-01` e rejeitou
reutilizacao. Essa evidencia permanece no backend QA. A VM limpa usara uma
identidade nova `qa-win-clean-02`; um codigo novo sera emitido apenas no QA.
Nenhum codigo de ativacao ou credencial foi colocado em Git.

Validacoes nesta retomada: suite Python completa PASS; testes focados do perfil
QA PASS; Edge 65/65 PASS; secret scan de 491 candidatos PASS; `git diff
--check` PASS; preflight da origem QA PASS. Permanecem sem evidencia de guest:
Build A/B, instalacao, ativacao, Empresa, Admin Lock, Recovery, heartbeat/sync,
update A para B e preservacao SQLite/DPAPI. Nenhum gate pendente vira PASS por
ter passado teste unitario ou smoke do host.
