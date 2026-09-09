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
- Serviços: catálogo, unidades configuráveis, Notas operacionais e histórico de preços;
- Caixa: livro-caixa, categorias, formas de pagamento, histórico e relatórios.

Os módulos continuam independentes. Cadastrar Cliente, Serviço ou Nota não gera
movimentação financeira. A Nota de total zero fica quitada sem Payment e sem
Caixa. A integração de Pagamentos pertence à Fase 3 e deverá respeitar o contrato
idempotente documentado em `Docs/CAIXA.md`.

## Contratos da evolução

A primeira versão da Nota de Serviço segue o
[contrato oficial](CONTRATO_NOTA_SERVICO.md), consolidado na Fase 1/4, e sua
[implementação operacional](NOTAS_SERVICO.md) foi realizada na Fase 2/4. Esses
documentos prevalecem sobre os exemplos genéricos anteriores de Atendimento/OS.

Novos valores financeiros e migrations seguem a
[estratégia monetária e de migrations](ESTRATEGIA_MONETARIA_MIGRATIONS.md).
O utilitário de centavos é usado pelas Notas; o Caixa existente e seu schema
monetário continuam sem conversão.
