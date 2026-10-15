# NexPoint ERP 1.1.2

## Alterações

- Serviços associados a várias categorias, preços por associação e histórico preservado.
- Recebimentos, taxas e ajustes com valores auditáveis e preservação dos snapshots.
- Suporte integrado: conversa, respostas, anexos privados e diagnóstico autorizado.
- Nexa com orientação contextual e confirmação antes de criar chamados.
- Atualizador com download verificado, confirmação de instalação e rollout controlado.
- Correção do identificador da build em heartbeat e sincronização.
- Defesa adicional de escopo por empresa nos RPCs administrativos de suporte.

## Evidências

Windows QA encerrado: instalação limpa, ativação, Empresa, Admin Lock, Recovery,
update A→B, reinício do ERP e preservação de SQLite/DPAPI aprovados.
Suíte 1402/1402 PASS; fila final sem pending/sending/failed/dead_letter.
Este resultado não representa instalação já realizada no notebook da Nil lav.

## Cuidados operacionais

Antes de instalar no notebook: criar backup local e verificar integridade.
Não desinstalar, apagar SQLite nem substituir a credencial DPAPI.
Após atualizar: conferir Empresa, dados, Admin Lock, versão e sincronização.
Rollback deve preservar banco e credencial; não restaurar backup antigo sobre
novos dados operacionais sem uma decisão explícita de recuperação.

## Limitações conhecidas

Authenticode permanece CODE_SIGNING_PENDING. O manifest autenticado e SHA-256
não substituem certificado de assinatura Windows. Anexos possuem limites por
arquivo/chamado e rate limit, mas não quota acumulada por tenant ou retenção
automática. Não há garantia de backup resistente a ransomware quando cópia e
dados permanecem acessíveis pela mesma conta; manter cópia offline protegida.

O advisory setuptools GHSA-h35f-9h28-mq5c trata de sdist/normalização Unicode;
não foi demonstrado no empacotamento PyInstaller Windows. A fonte do mantenedor,
consultada em 15/10/2026, ainda não informa versão corrigida:
https://github.com/pypa/setuptools/security/advisories/GHSA-h35f-9h28-mq5c.
Não foi realizado upgrade major de build nem reescrita do histórico Git.
