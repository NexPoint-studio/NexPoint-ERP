# Como criar um novo ERP a partir da carcaça

1. Copie toda a pasta para um novo diretório, sem levar `.venv`, `.env.local`,
   `data`, caches ou artefatos.
2. Crie um ambiente virtual próprio e instale `.[dev]`.
3. Copie `.env.example` para `.env.local` e altere nome, empresa, chave de
   sessão e senhas locais.
4. Substitua `static/img/logo-placeholder.svg` por um ativo local.
5. Revise `app/core/modules.py` e `app/core/permissions.py`.
6. Crie modelos, serviços e repositórios por módulo; mantenha a UI sem acesso
   direto ao SQLAlchemy.
7. Adicione testes de autorização, banco e interface para cada regra criada.
8. Mantenha o Kanban e a linha do tempo atualizados com estados reais.
9. Antes de distribuir, apague o SQLite de desenvolvimento e confira que nenhum
   segredo, log, cache ou dado empresarial entrou no Git.

## Checklist mínimo de uma nova cópia

- [ ] nome, empresa e logo próprios;
- [ ] chave de sessão e senhas exclusivas;
- [ ] apenas dependências necessárias;
- [ ] host preservado como `127.0.0.1` enquanto a aplicação for local;
- [ ] migrations e regras de negócio cobertas por testes;
- [ ] banco e arquivos temporários ignorados pelo Git;
- [ ] nenhuma integração externa criada sem uma decisão arquitetural explícita.
