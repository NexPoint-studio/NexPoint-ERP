# NexPoint ERP — guia de uso

Este guia descreve a versão em preparação. A liberação do instalador depende dos
testes de entrega registrados em `SIMPLIFICACAO_ENTREGA.md`.

## Primeiro uso

1. Execute o instalador oficial fornecido pela NexPoint.
2. Abra NexPoint ERP pelo Menu Iniciar.
3. Se aparecer **Ativar este computador**, conecte à internet e informe o código
   fornecido para esse computador. A configuração inicial pode levar alguns instantes.
4. Abra **Administração → Empresa**, confira os dados e clique em
   **Salvar dados da Empresa**.
5. Cadastre os serviços, unidades e preços em **Administração → Serviços**.
6. Comece a atender. Nas próximas aberturas, o sistema entra diretamente.

Se o Windows bloquear o instalador, peça orientação à NexPoint. Não desative as
proteções do computador. Se já houver dados, combine um backup antes da atualização.

## Empresa

O cadastro identifica a empresa que utiliza este ERP. Informe os dados verdadeiros;
campos desconhecidos podem ficar vazios quando não forem obrigatórios.

O nome fantasia aparece no sistema. Quando estiver vazio, aparece o nome cadastral.
Salvar funciona sem internet. O nome será atualizado no ambiente de suporte da
NexPoint quando a conexão voltar. Alterar o nome não cria outra empresa.

## Atendimento

Cadastre ou localize o cliente. Crie uma Nota de Serviço e escolha os serviços,
unidades e quantidades. Confira os valores antes de salvar.

Registre os pagamentos efetivamente recebidos. Cada recebimento gera a entrada
correspondente no Caixa. Um pagamento parcial mantém o restante em aberto.
Ao fechar uma Nota com valor a receber, acompanhe o saldo devedor no cliente.
Uma quitação posterior registra somente o valor recebido naquele momento.

No Caixa, consulte os lançamentos recentes e registre entradas ou saídas manuais.
Use Financeiro para consultar o histórico completo, saldos e relatórios. Movimentos
gerados automaticamente por pagamentos seguem o fluxo de origem para evitar duplicação.

## Proteger a Administração

A Administração começa livre. Para protegê-la, abra **Administração → Segurança →
Criar senha administrativa**. Guarde a senha em local seguro.

Depois disso, a senha será solicitada para abrir as configurações. O desbloqueio
é temporário e termina ao sair da área, fechar/reiniciar o ERP ou atingir o prazo.
Caixa, Clientes, Notas e Financeiro continuam disponíveis.

Trocar ou remover a proteção exige a senha administrativa atual. Se esquecê-la,
clique em **Esqueci minha senha → Solicitar recuperação à NexPoint**. A solicitação
pode ser registrada sem conexão, mas a autorização depende de comunicação com a
NexPoint. Quando autorizada, a tela permite definir uma nova senha.

## Backup e atualização

Em **Administração → Sistema**, crie backups regularmente e mantenha uma cópia em
local separado. Sincronização com a NexPoint não substitui o backup do computador.

Restauração substitui os dados atuais pelo conteúdo do backup selecionado. Confira
a origem e a data, mantenha uma cópia anterior e siga a confirmação exibida.
Para transferir dados para outra instalação, solicite orientação à NexPoint.

Quando uma atualização oficial for oferecida no Sistema, siga as instruções na
tela. Não desligue o computador durante a instalação. O programa será reiniciado;
os dados e a ativação devem permanecer preservados. Instalações antigas sem esse
recurso podem precisar de uma última atualização manual assistida.

## Suporte e conferência inicial

Use **Administração → Suporte** para abrir um chamado. Descreva o que estava
fazendo e a mensagem exibida. Não envie senhas ou códigos de ativação no chamado.

Antes do primeiro atendimento real, confira o nome da empresa, o catálogo e os
preços, teste a reabertura, combine a rotina de backup e confirme com a NexPoint
que a instalação está identificada corretamente no painel de suporte.
