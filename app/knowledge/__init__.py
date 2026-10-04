"""Versioned, non-sensitive ERP help separate from NexPoint public knowledge."""

ERP_HELP_VERSION = "2"

ERP_HELP = (
    ("clientes", "Clientes", "Cadastre, consulte e acompanhe o histórico em Clientes.", "customers.view"),
    ("notas", "Notas de Serviço", "A Nota registra itens e acompanha recebimento, execução e entrega.", "notes.view"),
    ("pagamentos", "Pagamentos", "A quitação integral de uma Nota positiva gera a entrada líquida no Caixa.", "notes.view"),
    ("caixa", "Caixa", "Consulte os lançamentos da empresa em Caixa e os totais e relatórios em Financeiro.", "cash.operations.view"),
    ("suporte", "Suporte", "Abra e acompanhe chamados para a NexPoint em Administração > Suporte.", "admin.support.manage"),
    ("empresa", "Empresa", "Identifique sua empresa em Administração > Empresa. O nome fantasia aparece no ERP e é sincronizado com a NexPoint.", "admin.settings"),
    ("seguranca", "Segurança", "O ERP abre diretamente. Para proteger configurações, crie uma senha em Administração > Segurança.", "admin.overview.view"),
)
