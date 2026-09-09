# ERP local integrado

Sistema de gestão autocontido para operação local. O projeto combina FastAPI,
SQLAlchemy, SQLite, Jinja2 e pywebview e funciona integralmente no computador,
inclusive sem internet.

O ERP possui módulos funcionais de Clientes, Serviços, Notas de Serviço, Caixa
e Administração. O fluxo principal integrado é:

```text
Cliente -> Nota de Serviço -> Pagamento -> Caixa
```

Uma Nota alimenta o histórico do Cliente. Quando um pagamento integral é
confirmado, o sistema registra a quitação e o impacto líquido no Caixa na mesma
transação. Notas de total zero ficam pagas sem criar Pagamento ou movimentação.

A área Administração oferece ao Proprietário:

- visão geral com alertas de produção e relacionamento;
- gestão do catálogo, categorias, preços e unidades de cobrança;
- visão financeira global, histórico e relatórios;
- formas de pagamento, terminais e regras de taxa;
- usuários, papéis e permissões;
- dados institucionais da empresa.

O Caixa operacional mostra ao operador apenas os lançamentos que ele próprio
registrou nos últimos sete dias. Saldos, totais, relatórios e agregações globais
ficam protegidos pelas permissões financeiras administrativas.

## Preparar o ambiente próprio

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
Copy-Item .env.example .env.local
```

No ambiente de teste atual, o acesso rápido usa usuário `adm` e senha `adm`.
Antes de distribuir uma cópia real, troque a chave de sessão e a senha no
arquivo `.env.local`. Esse arquivo é ignorado pelo Git.

Na primeira inicialização, os valores do ambiente preenchem configurações ainda
ausentes. Depois disso, nome da empresa, nome do aplicativo, versão, logo e fuso
salvos no SQLite prevalecem na interface. Segredo de sessão, host, porta e caminho
do banco continuam sendo configurações técnicas locais do ambiente.

## Execução

O projeto de trabalho está em `D:\NexStudio\sistema ERP`. Abra essa pasta no
VS Code para que o painel `TODO.md` acompanhe a tarefa atual. O ambiente Python
existente foi preservado e ajustado; não é necessário reinstalar dependências
para continuar usando este computador.

```powershell
Set-Location 'D:\NexStudio\sistema ERP'
.\.venv\Scripts\python.exe run_desktop.py
```

Para desenvolvimento local no navegador, sempre restrito a `127.0.0.1`:

```powershell
.\.venv\Scripts\python.exe run_dev.py
```

Consulte a [arquitetura](Docs/ARQUITETURA.md), os módulos
[Clientes](Docs/CLIENTES.md), [Serviços](Docs/SERVICOS.md),
[Notas de Serviço](Docs/NOTAS_SERVICO.md) e [Caixa](Docs/CAIXA.md), além do guia
de [Administração e Pagamentos](Docs/PAGAMENTOS_ADMINISTRACAO.md). Para reutilizar
a base em outro produto, leia também [Como criar um novo ERP](Docs/COMO_CRIAR_NOVO_ERP.md).
