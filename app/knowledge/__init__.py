"""Versioned, non-sensitive ERP help separate from NexPoint public knowledge."""

ERP_HELP_VERSION = "3"

ERP_HELP = (
    ("clientes", "Clientes", "Cadastre, consulte e acompanhe o histórico em Clientes.", "customers.view"),
    ("notas", "Notas de Serviço", "A Nota registra itens e acompanha recebimento, execução e entrega.", "notes.view"),
    ("pagamentos", "Pagamentos", "Na Nota, use Registrar pagamento para receber o total ou parte do saldo. Cada confirmação gera uma entrada no Caixa. O principal quita a dívida; o acréscimo combinado com o cliente soma ao bruto; o custo da operadora é descontado para obter o líquido. Confira a prévia antes de confirmar. Não repita um pagamento já confirmado.", "notes.view"),
    ("catalogo categorias servicos precos", "Catálogo e preços por categoria", "Em Administração > Serviços, crie ou edite um serviço e marque suas categorias. Busque pelo nome da categoria. Cada associação pode ter preço próprio; quando vazio, usa o preço padrão do serviço. O preço padrão é opcional somente se todas as categorias selecionadas têm preço próprio. Na Nota, escolha o serviço e a categoria. Alterar o catálogo não recalcula itens históricos.", "services.view"),
    ("taxas percentual fixo operadora", "Configurar taxas", "Em Administração > Pagamentos e taxas, escolha a forma de recebimento, informe percentual e custo fixo e salve. Para cartão, informe modalidade e, quando necessário, terminal e parcelas. A taxa é custo da empresa, não acréscimo automático ao cliente. Cada recebimento parcial usa seu próprio bruto; configurações futuras não alteram taxas históricas. No pagamento da Nota, expanda o ajuste de taxa para informar uma exceção e seu motivo.", "finance.config.manage"),
    ("saldo devedor debitos fechamento", "Saldos devedores", "Fechar uma Nota com saldo gera um débito, não uma entrada fictícia no Caixa. O débito pode ser quitado diretamente pelas formas disponíveis ou vinculado a uma nova Nota do mesmo cliente. No recebimento dessa Nota, os saldos anteriores são quitados primeiro. A taxa da operadora não reduz o principal quitado.", "notes.view"),
    ("caixa", "Caixa", "Consulte os lançamentos da empresa em Caixa e os totais e relatórios em Financeiro.", "cash.operations.view"),
    ("suporte", "Suporte", "Abra e acompanhe chamados para a NexPoint em Administração > Suporte.", "admin.support.manage"),
    ("empresa", "Empresa", "Identifique sua empresa em Administração > Empresa. O nome fantasia aparece no ERP e é sincronizado com a NexPoint.", "admin.settings"),
    ("seguranca", "Segurança", "O ERP abre diretamente. Para proteger configurações, crie uma senha em Administração > Segurança.", "admin.overview.view"),
    ("offline sincronizacao suporte internet", "Uso sem internet", "As operações locais continuam disponíveis sem internet. Sincronização, envio de chamados e respostas da Nexa dependem de conexão. Um chamado pendente de sincronização ainda não comprova recebimento pela NexPoint. Não afirme que uma ação remota foi concluída sem confirmação do sistema.", "notes.view"),
)
