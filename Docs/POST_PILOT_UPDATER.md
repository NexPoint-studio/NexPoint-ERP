# Updater pós-piloto

Implementação local, pendente de promoção e de nova validação Windows A→B.

## Distribuição

O Control Center permite criar rascunho com os metadados oficiais, publicar somente
após verificação do instalador, ajustar percentual, pausar, retomar e retirar uma
release. Retirada é terminal. Um percentual pode ser configurado ainda no estado
verificado, antes de publicar, evitando uma janela involuntária de distribuição
para 100% do canal.

O upload continua pela ferramenta oficial `scripts/prepare_official_release.py`:
valida build, bytes, tamanho, origem e assinatura quando disponível; envia ao
Storage privado e verifica novamente os bytes recebidos. Não existe upload de
executável pelo formulário web. Informar um hash no rascunho não verifica o arquivo.

O percentual usa coorte determinística por UUID da instalação e da release.
Aumentar o percentual inclui novas instalações sem retirar as já elegíveis.
Somente o servidor determina canal e elegibilidade. A migration
`20261014180000_release_rollout.sql` mantém o manifest de cliente no schema 1,
sem campos novos que quebrariam clientes já instalados. RLS, assinatura, nonces,
controle de versão e autenticação de instalação permanecem ativos.

Pausar impede novas ofertas; não remove pacotes já baixados. Uma instalação
offline com pacote previamente autorizado pode instalá-lo após confirmação local.
Não há promessa de revogação remota instantânea de um pacote já entregue.

## Cliente e telemetria

A tarefa periódica verifica e baixa em segundo plano. A ação manual Verificar
continua apenas consultando. Instalar ainda exige confirmação humana e entrega
ao helper desktop; não ocorre instalação automática pela rotina de download.

O heartbeat inclui somente códigos de erro enumerados, versão-alvo e instante da
última tentativa, além de canal, estado e instante da mudança. Não envia URL de
download, token, resposta de transporte ou exceção bruta. O servidor ignora estado
fora de ordem, canal forjado e instantes futuros. O journal autenticado aceita o
formato anterior sem perder o histórico de versões.

Downloads interrompidos reiniciam integralmente: não há retomada HTTP Range.
Parciais são descartados e nenhum arquivo é executado antes das verificações.
O limite atual do Storage é 50 MiB. Assinatura Authenticode oficial ausente deve
continuar registrada como `CODE_SIGNING_PENDING`; SHA-256 não é autenticação.

## Validação

`scripts/security/test_releases_database.py` cria PostgreSQL descartável sem rede,
aplica as migrations, valida ciclo de publicação, canais, RLS, replay, limitação
de requisições, coorte Python/PostgreSQL, pausa, retirada e heartbeat sanitizado.
Os testes Python cobrem o journal, transporte, integridade, download, worker e
controles web. Esses testes não substituem instalação A→B no Windows atual.

A prova Windows deve incluir SQLite, dados fictícios, Empresa, Admin Lock,
credencial DPAPI, chamados/anexos e sync. Nenhuma instalação ou dado da Nil lav
foi alterado por esta implementação.
