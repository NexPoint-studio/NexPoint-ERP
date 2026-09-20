from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TabDefinition:
    id: str
    name: str
    path: str
    permission: str
    description: str
    visible: bool = True


@dataclass(frozen=True, slots=True)
class ModuleDefinition:
    id: str
    name: str
    icon: str
    route: str
    permission: str
    feature_flag: str | None
    status: str
    tabs: tuple[TabDefinition, ...]


def tab(
    id_: str,
    name: str,
    path: str,
    permission: str,
    description: str,
    *,
    visible: bool = True,
) -> TabDefinition:
    return TabDefinition(id_, name, path, permission, description, visible)


MODULES = (
    ModuleDefinition("cash", "Caixa", "$", "/caixa/operacoes", "cash.operations.view", "cash", "functional", (
        tab("operacoes", "Operações", "/caixa/operacoes", "cash.operations.view", "Lançamentos recentes do operador."),
        tab("novo", "Novo lançamento", "/caixa/novo-lancamento", "cash.create", "Registro manual de entradas e saídas."),
    )),
    ModuleDefinition("customers", "Clientes", "♙", "/clientes/lista", "customers.view", "customers", "functional", (
        tab("lista", "Lista", "/clientes/lista", "customers.view", "Consulta e gestão dos clientes locais."),
        tab("novo", "Novo cliente", "/clientes/novo", "customers.create", "Cadastro local de pessoa ou empresa."),
        tab("historico", "Histórico", "/clientes/historico", "customers.view", "Linha do tempo das atividades dos clientes."),
    )),
    ModuleDefinition("services", "Serviços", "◇", "/servicos/catalogo", "services.view", "services", "functional", (
        tab("catalogo", "Catálogo", "/servicos/catalogo", "services.view", "Consulta dos serviços, preços e unidades disponíveis."),
        tab("nova-nota", "Nova Nota", "/servicos/nova-nota", "notes.create", "Registro operacional de uma Nota de Serviço."),
        tab("notas", "Notas de Serviço", "/servicos/notas", "notes.view", "Pesquisa e acompanhamento das Notas de Serviço."),
    )),
    ModuleDefinition("admin", "Administração", "⚙", "/admin/visao-geral", "admin.overview.view", None, "functional", (
        tab("visao-geral", "Visão geral", "/admin/visao-geral", "admin.overview.view", "Indicadores operacionais e alertas administrativos."),
        tab("servicos", "Serviços", "/admin/servicos", "admin.services.view", "Gestão do catálogo, categorias, unidades e preços."),
        tab("financeiro", "Financeiro", "/admin/financeiro", "finance.overview.view", "Saldo, histórico e relatórios globais."),
        tab("pagamentos", "Pagamentos e taxas", "/admin/pagamentos", "finance.config.manage", "Formas, terminais e regras de taxa."),
        tab("usuarios", "Usuários e permissões", "/admin/usuarios", "admin.users", "Estrutura interna de identidade e autorização.", visible=False),
        tab("empresa", "Empresa", "/admin/empresa", "admin.settings", "Dados institucionais preservados para o onboarding.", visible=False),
        tab("suporte", "Suporte", "/admin/suporte", "admin.support.manage", "Abertura e acompanhamento de chamados para a NexPoint."),
        tab("auditoria", "Auditoria", "/admin/auditoria", "admin.audit.view", "Consulta protegida dos eventos administrativos."),
        tab("sistema", "Sistema", "/admin/sistema", "admin.system.view", "Versão, banco, backups e restauração local."),
    )),
)

MODULE_BY_ID = {module.id: module for module in MODULES}
ROUTE_INDEX = {item.path: (module, item) for module in MODULES for item in module.tabs}

# O módulo Serviços é operacional. Toda mutação de catálogo fica na
# Administração e exige permissão administrativa própria.
