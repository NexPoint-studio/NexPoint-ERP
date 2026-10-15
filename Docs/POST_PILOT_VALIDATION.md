# Validação pós-piloto — 14/10/2026

Atualização 15/10: o Windows QA foi concluído e aceito, com 1402/1402 PASS e
sync completo. As pendências Windows abaixo são histórico anterior, não tarefas
atuais. Consulte QA_WINDOWS_20261015.md e PROD_RELEASE_20261015.md para o estado
de promoção e as ações externas restantes.

## Resultado local

- Suíte Python: 1389 casos; primeira execução 1384 PASS e 5 FAIL em 1207s.
- As cinco falhas foram corrigidas e reexecutadas: 5 PASS. Não é uma execução
  única integralmente verde. Evidências `final-suite.xml` e `final-corrections.xml`
  em `artifacts/post-pilot-20261014/`.
- Correções de teste: reabertura autorizada, ausência de diagnóstico sem
  consentimento, allowlist dos novos adapters, composição do transporte sync
  preservando credencial provisionada e fixture de datas independente do dia atual.
- Edge suporte/anexos: 21 PASS em nova execução.
- PostgreSQL isolado: conversa/anexos, RLS, replay, idempotência e paginação PASS;
  updater/rollout: 75 checks PASS, conforme relatórios dos respectivos harnesses.
- Backup/restore atual: 16 PASS em cópias. Script histórico cross-restore bloqueado
  pela proteção de identidade atual; não constitui PASS desse roteiro antigo.
- Regressão de interrupção anexo/outbox: PASS; recuperação após restart e recibo
  impedindo reenfileiramento após purge, com bytes preservados.
- Secret scan e diff check passaram antes do fechamento local.

## Gates externos

Código registrado no commit local `be0742475703651b2244c41cf7e91832aa99a0a9`.
Builds exclusivos QA A 1.1.0 e B 1.1.2 gerados desse commit. Validação de
boundary QA e secret scan dos bundles: PASS. Compilação dos dois instaladores:
exit 0. Artefatos, hashes e `installer-build.json` em:

- `artifacts/windows-qa-installer/1.1.0-be0742475703/`
- `artifacts/windows-qa-installer/1.1.2-be0742475703/`

Os executáveis permanecem `NotSigned` / `CODE_SIGNING_PENDING`. Não são entrega
stable nem evidência de instalação. O launcher de build registrou exit 1 ao
capturar stderr informativo do PyInstaller; o script chegou aos relatórios finais
e a validação independente dos dois bundles passou. Instaladores tiveram exit 0.

Ação externa pendente: abrir Gerenciador do Hyper-V → NexPoint-Windows-QA →
Conectar e entrar na sessão QA. Não enviar senha no chat nem alterar credenciais
PROD. A credencial salva foi rejeitada; nenhum reset de conta foi executado.

Windows A→B deste código: PENDENTE. A VM inicia, porém a credencial QA salva foi
rejeitada; acesso ao guest e validade da licença precisam ser confirmados.
Origem HTTPS QA passou nos checks de isolamento na preparação anterior.

PROD, push, deploy e entrega Nil lav: NÃO EXECUTADOS neste ciclo. Não promover
antes da prova Windows, backup/restore PROD, baseline, auditoria das migrations e
revisão de identidade/canal da instalação. GO final ainda não concedido.

Consulte `POST_PILOT_SUPPORT.md`, `POST_PILOT_UPDATER.md` e
`POST_PILOT_SECURITY.md` para contratos e limitações.
