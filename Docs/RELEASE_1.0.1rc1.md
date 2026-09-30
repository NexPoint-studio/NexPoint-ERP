# NexPoint ERP 1.0.1rc1

Release candidate da remediação de segurança, com versão Python PEP 440 e
recurso Windows 1.0.1.1 marcado como prerelease. O manifesto mantém channel
PROD e identifica o SHA exato; isso não indica que o artefato esteja publicado.

- Sessões internas agora possuem revogação persistente individual e validade;
  logout não deixa o cookie anterior reutilizável. Migração cloud aditiva
  `20260929010000_platform_sessions.sql` já foi aplicada antes da atualização do painel.
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

Backup pré-release concluído em `%LOCALAPPDATA%/NexPoint/ERP/backups/pre-release-b6324b8c10ea`:
três SQLite consistentes com integrity_check/FK válidos, configuração/cofres,
manifesto anterior, schema/dados cloud e migration state. Nove arquivos cifrados
DPAPI, roundtrip verificado e ACL conta/SYSTEM; fora do Git. Dados cloud públicos
comparados antes/depois em memória: todas as 21 tabelas preexistentes idênticas.

Os arquivos `.dpapi` desse backup são gzip protegido por `WindowsDpapiProtector`
da conta Windows atual. Em recuperação autorizada, conferir SHA-256 no manifesto,
desproteger com essa mesma classe/conta e descomprimir gzip para um destino de
recuperação com ACL restrita. Validar SQLite/dump em cópia antes de qualquer
substituição. Não publicar plaintext nem restaurar automaticamente em PROD.

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

## Artefatos e validação

Build imutável do commit `df7cdc5fb37df207202cc3bde0072d9858dade48`; commits
posteriores de encerramento alteram somente documentação/evidências.

Diretório local: `artifacts/security/retest/builds/final-df7cdc5fb37d-1614e2/`.
ZIP: `source/artifacts/windows-build/NexPointERP-1.0.1rc1-PROD-df7cdc5fb37d.zip`.
SHA-256: `b6fd82db65d4e0235aa95f3955e803cc7b05149662f3b04db3bc74d7dad6c10c`.
Executável: `ad0fe6aba51aa19aa36333a5acc504b260243fe17e0903a3c64fccbb01f509bf`.
Imagem: `nexpoint-remediation:df7cdc5fb37d`, digest
`753b899a4295e0a580251a738d4b53c05281e3c73d3ed1c12932bad64de9a548`.

Windows iniciado diretamente pelo executável, sem Python externo: login,
remember após reinício, Admin Lock, cliente/serviço/nota/pagamento/caixa,
fila de suporte, Nexa offline, logout/replay e integridade passaram. Foi usado
somente appdata descartável e proxy que bloqueia tráfego externo. Reconexão/ACK
validados separadamente na integração QA. O pacote não foi instalado sobre a
instalação real nem publicado em canal de atualização. Assinatura/SmartScreen
e interação completa de cada tela não foram certificados.

Docker executado como UID 10001, filesystem read-only, sem rede/portas/mounts:
startup/health/login/logout/CSRF/replay aprovados. Readiness, headers/cookies,
TLS e repositório Supabase foram verificados na stack integrada de QA.
Locks/runtime Docker idênticos à remediação; nenhuma atualização indiscriminada.
Suíte 1.018/1.018; scanners e limites no relatório de reteste.
