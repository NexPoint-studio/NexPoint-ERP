"""Versioned, non-sensitive ERP help separate from NexPoint public knowledge."""

ERP_HELP_VERSION = "1"

ERP_HELP = (
    ("clientes", "Clientes", "Cadastre, consulte e acompanhe o histórico em Clientes.", "customers.view"),
    ("notas", "Notas de Serviço", "A Nota registra itens e acompanha recebimento, execução e entrega.", "notes.view"),
    ("pagamentos", "Pagamentos", "A quitação integral de uma Nota positiva gera a entrada líquida no Caixa.", "notes.view"),
    ("caixa", "Caixa", "O operador vê seus lançamentos recentes; relatórios globais exigem permissão administrativa.", "cash.operations.view"),
    ("suporte", "Suporte", "A concessão temporária de suporte é controlada pelo Proprietário.", "admin.support.manage"),
)
