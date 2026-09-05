# ERP local — carcaça genérica

Template autocontido para iniciar novos sistemas de gestão sem dados ou regras
de uma empresa específica. A carcaça combina FastAPI, SQLAlchemy, SQLite,
Jinja2 e pywebview e funciona integralmente no computador, inclusive sem
internet.

Ela oferece login local, perfis e permissões, auditoria, feature flags, sidebar
responsiva, abas contextuais e componentes visuais. Os módulos Clientes,
Serviços e Caixa são funcionais e reutilizáveis. Administração contém apenas
infraestrutura genérica.

## Preparar o ambiente próprio

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
Copy-Item .env.example .env.local
```

No ambiente de teste atual, o acesso rápido usa usuário `adm` e senha `adm`.
Antes de distribuir uma cópia real, troque a chave de sessão e a senha no
arquivo `.env.local`. Esse arquivo é ignorado pelo Git.

## Execução

O projeto de trabalho está em `D:\NexStudio\sistema ERP`. Abra essa pasta no
VS Code para que o painel `TODO.md` acompanhe a tarefa atual. O ambiente Python
existente foi preservado e ajustado; não é necessário reinstalar dependências
para continuar usando este computador. Detalhes em
[Transferência do projeto](Docs/TRANSFERENCIA_PROJETO.md).

```powershell
Set-Location 'D:\NexStudio\sistema ERP'
.\.venv\Scripts\python.exe run_desktop.py
```

Para desenvolvimento local no navegador, sempre restrito a `127.0.0.1`:

```powershell
.\.venv\Scripts\python.exe run_dev.py
```

Consulte [a documentação da carcaça](Docs/CARCACA.md), as documentações dos
módulos [Clientes](Docs/CLIENTES.md), [Serviços](Docs/SERVICOS.md) e
[Caixa](Docs/CAIXA.md), e o guia
[Como criar um novo ERP](Docs/COMO_CRIAR_NOVO_ERP.md) antes de reutilizar.
