# Carcaça reutilizável de ERP

## Escopo congelado

- aplicação Python/FastAPI executada apenas em `127.0.0.1`;
- janela nativa com pywebview;
- persistência local SQLAlchemy/SQLite criada somente na primeira execução;
- templates Jinja e componentes visuais reutilizáveis;
- design system centralizado em `static/css/tokens.css`;
- sidebar responsiva e abas contextuais definidas por módulo;
- autenticação local com hash scrypt;
- papéis genéricos `admin`, `user`, `delivery` e `support`;
- permissões por ação e proteção de rotas;
- feature flags locais por módulo;
- eventos de login, logout e navegação administrativa auditados.
- invalidação de sessão por usuário e por geração global;
- backup SQLite consistente e restauração offline com rollback;
- suporte temporário sem conta oculta ou permissão permanente.

## Estado inicial

A carcaça não traz banco SQLite versionado. Na primeira execução são criadas as
tabelas estruturais e a conta local configurada em `.env.local`. Os módulos
Clientes, Serviços e Caixa são funcionais, mas não incluem dados empresariais.
O Caixa começa em `R$ 0,00`, sem categorias empresariais e sem movimentações;
somente as formas de pagamento genéricas são disponibilizadas.

## Como reutilizar

1. Copie a pasta para um novo projeto.
2. Altere os metadados centralizados em `.env.local`; renomeie o pacote e o
   cookie apenas se a nova aplicação precisar conviver com outro ERP local.
3. Substitua o logo SVG e o nome configurado da empresa.
4. Revise o catálogo de módulos, permissões, papéis e feature flags.
5. Crie um `.env.local` próprio; nunca reutilize credenciais.
6. Desenvolva módulos em migrations e testes separados.
7. Antes de distribuir, revise logs, caches, banco local e arquivos temporários.

## Limites

Este template contém apenas regras genéricas dos módulos já documentados. Não
contém integrações de nuvem, migração de dados reais, seed empresarial ou
mecanismo de implantação. Não há chamada HTTP externa: o único endereço usado
pela interface é o FastAPI em `127.0.0.1`. Regras específicas de uma empresa
pertencem a cada ERP criado a partir da carcaça.
