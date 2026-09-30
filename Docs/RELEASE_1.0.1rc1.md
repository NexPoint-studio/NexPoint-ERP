# NexPoint ERP 1.0.1rc1

Release candidate da remediação de segurança, com versão Python PEP 440 e
recurso Windows 1.0.1.1 marcado como prerelease. O manifesto mantém channel
PROD e identifica o SHA exato; isso não indica que o artefato esteja publicado.

- Sessões internas agora possuem revogação persistente individual e validade;
  logout não deixa o cookie anterior reutilizável. Migração cloud aditiva
  `20260929010000_platform_sessions.sql` deve preceder a atualização do painel.
- Sanitização do painel e observabilidade local limitam entrada e trabalho.
- CSRF malformado/não ASCII recebe rejeição controlada sem HTTP 500.
- Login SQLite mantém trabalho criptográfico equivalente nos casos negativos.
- Perfis de navegador são bloqueados nos candidatos Git e na distribuição.

Não houve mudança de contrato de secrets, tenant, installation, dados financeiros,
RLS de domínio ou Edge Functions. Não é necessário recadastrar Environment no
Render por causa desta release. `autoDeploy: false` deve permanecer preservado.

Validação e limites: [reteste](Security/SECURITY_RETEST_REPORT.md),
[gate](Security/SECURITY_RELEASE_GATE.md),
[cobertura](Security/RETEST_COVERAGE_MATRIX.md).
SD-003 histórico continua aberto, SD-004/005 inconclusivos e SD-006 não aplicável.
O conjunto não constitui certificação integral ASVS/WSTG.

## Backup e rollback

Antes da preparação PROD, preservar backup SQLite consistente, configuração,
cofres cifrados, manifestos anteriores, schema/dados cloud e migration state.
Backups desta tarefa ficam fora do Git, cifrados com DPAPI da conta Windows e
ACL da conta/SYSTEM. Não registrar material secreto no changelog.

Desktop: manter a instalação/build anterior e escolher novamente seu executável
se a RC falhar. Não sobrescrever o banco com backup para simplesmente trocar
binário. Se for necessária restauração, parar todos os processos primeiro,
preservar uma cópia do estado atual e usar o fluxo validado de restore.
A instalação anterior encontrada tem manifesto `PROD-12a80463bbb1`/1.0.0.
O pacote anterior validado localmente da remediação é `PROD-209d5eaa41fd`.

Supabase: a nova tabela não altera tabelas, políticas ou funções de domínio;
código anterior pode coexistir com ela. Em rollback do binário, manter a tabela
e o registro da migration; não executar DROP/TRUNCATE nem apagar sessões/dados.
Falha antes do COMMIT deve reverter a transação aditiva. Divergência de schema
deve interromper a preparação, sem tentativa de reparo destrutivo.

Edge: fontes preservadas, nenhum redeploy necessário. Se uma alteração futura
exigir rollback, usar a revisão previamente registrada e os mesmos contratos
de secrets, após QA específico; não redeployar agora por rotina.

Render: somente o proprietário fará o deploy manual do SHA final entregue.
Rollback funcional documentado na baseline:
`37f18e531488590b369a9ab30b9c7be321ec33b7` (não inferido como SHA LIVE atual).
Usar Manual Deploy → Deploy a specific commit → SHA → Deploy Commit → Live.
Não fazer smoke PROD até o proprietário confirmar Live.
