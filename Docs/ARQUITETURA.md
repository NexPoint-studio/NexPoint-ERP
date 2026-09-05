# Arquitetura da carcaça ERP local

## Fluxo das camadas

```text
HTML/CSS/JS + Jinja2
        ↓
Rotas FastAPI
        ↓
Serviços
        ↓
Repositórios
        ↓
SQLAlchemy
        ↓
SQLite local
```

As telas não acessam o banco diretamente. As rotas cuidam de HTTP e sessão,
os serviços concentram decisões da aplicação e os repositórios isolam a
persistência. Essa separação permite uma troca futura do mecanismo SQL sem
acoplar a interface, mas nenhum adaptador remoto é incluído nesta versão.

## Execução local

- FastAPI e pywebview usam exclusivamente `127.0.0.1`.
- O banco é criado em `data/erp.sqlite3` e não é versionado.
- A configuração sensível fica em `.env.local`, também não versionado.
- HTML, CSS, JavaScript, fontes e imagens são locais.
- Não há SDK, URL, token, autenticação, storage ou sincronização de nuvem.

## Núcleo reutilizável

- registry declarativo de módulos e abas;
- autenticação com hash local de senha;
- papéis `admin`, `user` e `delivery` e permissões por ação;
- feature flags locais por módulo;
- eventos de auditoria;
- design tokens neutros e componentes reutilizáveis;
- tratamento de acesso negado e sessão.

## Módulos funcionais atuais

- Clientes: cadastro, endereços e atividades locais;
- Serviços: catálogo, categorias e histórico de preços;
- Caixa: livro-caixa, categorias, formas de pagamento, histórico e relatórios.

Os módulos continuam independentes. Cadastrar Cliente ou Serviço não gera uma
movimentação financeira. A integração futura com Atendimento/OS e Pagamentos
deve respeitar as mesmas camadas e o contrato idempotente documentado em
`Docs/CAIXA.md`.
