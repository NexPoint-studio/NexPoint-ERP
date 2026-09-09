# Backup, restauração e atualização local

## Escopo

O ERP mantém dados operacionais, configurações da empresa, usuários,
permissões e auditoria no SQLite configurado pela instalação. O backup
administrativo cria uma cópia local consistente desse banco. Arquivos
`.env.local`, segredos de sessão, senhas em texto, código-fonte, logs e caches
não fazem parte do backup.

Backups são dados sensíveis: contêm os registros comerciais e os hashes das
senhas necessários para restaurar os usuários. Eles ficam em `backups/erp/`,
diretório local ignorado pelo Git, e só podem ser baixados por rota autenticada.

## Criação e exportação

A criação exige a permissão `admin.backups.manage` e a senha atual do ator. O
service também repete essa autorização no backend.

O banco em uso é copiado com `sqlite3.Connection.backup()`. A saída começa em
um arquivo exclusivo com sufixo `.partial`. Depois da cópia, o sistema exige:

- cabeçalho SQLite;
- `PRAGMA integrity_check = ok`;
- `PRAGMA foreign_key_check` sem linhas;
- estrutura mínima do ERP;
- cadeia conhecida de migrations;
- ao menos um Proprietário ativo.

Somente depois dessas verificações o arquivo recebe um nome final com data UTC,
schema e identificador aleatório. Um manifesto registra formato, versão, data,
tamanho e SHA-256, sem incluir caminho do banco, connection string, variáveis de
ambiente ou dados comerciais.

A exportação usa uma resposta de download autenticada com
`Cache-Control: no-store`. O cliente informa apenas o ID gerado pelo ERP; não
é possível fornecer caminhos de arquivos.

## Restauração preparada

Restauração exige simultaneamente:

- papel Proprietário ativo;
- permissão `admin.restore`;
- senha atual;
- confirmação textual `RESTAURAR`.

O nome enviado pelo navegador não define nenhum caminho local. O upload possui
limite de tamanho, é salvo com nome gerado pelo servidor e validado em uma área
isolada. O arquivo original não é migrado. Uma segunda cópia recebe as
migrations e o bootstrap aditivo atuais, com credenciais de seed vazias, e
passa novamente por todas as verificações.

Uma requisição HTTP nunca troca o banco em uso. Ela grava atomicamente um plano
`pending.json` e informa que a restauração será aplicada ao reiniciar. Enquanto
o plano estiver apenas preparado, o Proprietário pode cancelá-lo.

## Aplicação no início e rollback

`apply_pending_restore()` deve ser chamado antes de criar o engine SQLAlchemy.
Nesse ponto não há conexão aberta com o SQLite, condição necessária para uma
troca previsível no Windows.

O início executa esta sequência:

1. relê o plano e confere nomes, estados e hashes;
2. revalida o candidato preparado;
3. cria naquele momento um backup consistente do banco corrente;
4. valida o backup de rollback;
5. copia o candidato fechado para um temporário no mesmo diretório de
   `erp.sqlite3`;
6. persiste o estado `APPLYING`;
7. usa `os.replace()` no mesmo volume;
8. executa bootstrap/migrations e valida o banco ativo;
9. registra a conclusão e cria uma nova geração de sessão;
10. arquiva o plano concluído.

Os estados do plano permitem distinguir interrupções anteriores e posteriores
à troca. Se a validação ou inicialização falhar depois de substituir o arquivo,
o sistema restaura o backup de rollback por outra troca atômica. Caso não seja
possível comprovar o rollback, o estado passa a `RECOVERY_REQUIRED` e o ERP não
deve abrir automaticamente o banco duvidoso.

O backup de rollback permanece em `backups/erp/`. Arquivos de staging são
removidos após conclusão ou cancelamento.

Após uma restauração concluída, sessões emitidas antes dela devem ser
invalidadas pela configuração `security.session_generation`. Isso impede que um
cookie com ID antigo seja associado a outra identidade presente no snapshot.

## Auditoria

Os eventos usados são:

- `system.backup_created`;
- `system.backup_failed`;
- `system.backup_downloaded`;
- `system.restore_scheduled`;
- `system.restore_rejected`;
- `system.restore_cancelled`;
- `system.restore_completed`;
- `system.restore_rolled_back`.

Detalhes registram somente IDs da operação, schema, tamanho, hash e código seguro
de resultado. Conteúdo do arquivo, senha, token, secret, SQL e nome original do
upload não são auditados.

## Atualização

A instalação atual não possui servidor nem canal confiável de distribuição. O
provider local retorna `NOT_CONFIGURED` e não acessa a rede. Não há endpoint
para baixar ou executar atualização.

Uma implementação futura só poderá anunciar ou aplicar uma versão depois de
definir:

- origem confiável e manifesto assinado;
- chave pública incorporada à distribuição;
- hash e assinatura do pacote;
- compatibilidade entre aplicativo e schema;
- backup verificado antes da mudança;
- instalação em staging;
- migrations testadas;
- troca atômica e rollback;
- auditoria sem secrets.

Até esse contrato existir, a Administração mostra versão instalada, build
quando informado, ambiente local, Python, SQLite, schema e o estado
“Não configurado”.

## Integração esperada

O aplicativo fornece em `app.state`:

- `database_path: pathlib.Path`;
- `backup_root: pathlib.Path`;
- `settings`;
- `session_factory`.

`app.migrations` fornece a tupla ordenada `SUPPORTED_SCHEMA_VERSIONS` e
`LATEST_SCHEMA_VERSION`. A aplicação chama `apply_pending_restore()` antes de
`build_engine()`, registra `app.routes.system.router` e compara
`security.session_generation` no login e no carregamento de cada sessão.
