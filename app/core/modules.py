from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TabDefinition:
    id: str
    name: str
    path: str
    permission: str
    description: str


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


def tab(id_: str, name: str, path: str, permission: str, description: str) -> TabDefinition:
    return TabDefinition(id_, name, path, permission, description)


MODULES = (
    ModuleDefinition("cash", "Caixa", "$", "/caixa/resumo", "cash.view", "cash", "functional", (
        tab("resumo", "Resumo", "/caixa/resumo", "cash.view", "Saldo e movimento financeiro local."),
        tab("novo", "Novo lançamento", "/caixa/novo-lancamento", "cash.create", "Registro manual de entradas e saídas."),
        tab("historico", "Histórico", "/caixa/historico", "cash.view", "Consulta completa dos lançamentos."),
        tab("relatorios", "Relatórios", "/caixa/relatorios", "cash.reports.view", "Análise financeira por período."),
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
        tab("novo", "Novo serviço", "/servicos/novo", "services.create", "Cadastro de um serviço e seu preço inicial."),
        tab("categorias", "Categorias", "/servicos/categorias", "services.categories.manage", "Organização das categorias do catálogo."),
        tab("precos", "Preços", "/servicos/precos", "services.prices.manage", "Gestão individual e histórico de preços."),
    )),
    ModuleDefinition("admin", "Administração", "⚙", "/admin/usuarios", "admin.users", None, "infrastructure", (
        tab("usuarios", "Usuários", "/admin/usuarios", "admin.users", "Estrutura local de usuários."),
        tab("permissoes", "Permissões", "/admin/permissoes", "admin.permissions", "Estrutura local de papéis e permissões."),
        tab("configuracoes", "Configurações", "/admin/configuracoes", "admin.settings", "Configurações genéricas da aplicação."),
        tab("sistema", "Sistema", "/admin/sistema", "admin.settings", "Informações da execução local."),
    )),
)

MODULE_BY_ID = {module.id: module for module in MODULES}
ROUTE_INDEX = {item.path: (module, item) for module in MODULES for item in module.tabs}

# O módulo Serviços reúne catálogo e Notas; telas administrativas do catálogo
# coexistem temporariamente até a reorganização autorizada para a Fase 3.
