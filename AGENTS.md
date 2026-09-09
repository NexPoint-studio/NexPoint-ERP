# ERP — Equipe de Setores

## Projeto oficial

Caminho local:

D:\NexStudio\sistema ERP

Repositório:

https://github.com/SmuriNex/sistema-ERP

Branch principal:

main

Este é o projeto ativo.

Não utilizar `D:\carcaça erp` como projeto de desenvolvimento.
Essa pasta pertence à versão/origem anterior.

---

# Objetivo

Este projeto utiliza setores especializados.

O SETOR DE COORDENAÇÃO deve analisar cada tarefa e utilizar somente os setores necessários.

Nem toda tarefa precisa envolver todos os setores.

Uma alteração pequena deve usar somente o setor responsável.

Uma alteração grande pode ser dividida entre vários setores.

---

# Setores oficiais

## SETOR DE COORDENAÇÃO

Responsável principal:

Astra

Responsabilidades:

- entender a solicitação do usuário;
- analisar o impacto da mudança;
- dividir trabalhos grandes em tarefas menores;
- selecionar quais setores participarão;
- delegar trabalho aos agentes de cada setor;
- controlar o TODO.md;
- evitar conflitos entre setores;
- integrar mudanças feitas pelos setores;
- revisar o resultado global;
- organizar Git e commits;
- garantir que o ERP continue funcional;
- impedir alterações fora do escopo solicitado.

O SETOR DE COORDENAÇÃO possui visão global do projeto.

---

## SETOR DE BACKEND

Responsável por:

- Python;
- FastAPI;
- regras de negócio;
- services;
- repositories;
- routes;
- schemas;
- validações de negócio;
- estados;
- cálculos;
- contratos entre módulos;
- integrações internas;
- processamento de dados;
- fluxos operacionais.

O SETOR DE BACKEND não deve alterar banco ou interface sem necessidade técnica justificada.

---

## SETOR DE BANCO DE DADOS

Responsável por:

- SQLite;
- SQLAlchemy;
- models;
- relacionamentos;
- migrations;
- constraints;
- índices;
- consultas;
- integridade referencial;
- transações;
- consistência dos dados;
- desempenho de consultas.
- persistência monetária.

Regras obrigatórias:

- nunca apagar banco real para facilitar desenvolvimento;
- nunca recriar banco real sem autorização;
- mudanças de schema devem preservar dados existentes;
- testes destrutivos usam banco isolado;
- migrations devem ser testadas;
- valores financeiros não devem utilizar float.

---

## SETOR DE INTERFACE / UX

Corresponde à área que outros projetos podem chamar de HUD/UI. Neste ERP, a
nomenclatura oficial é sempre Interface / UX.

Responsável por:

- Jinja2;
- HTML;
- CSS;
- JavaScript;
- UX;
- responsividade;
- acessibilidade;
- organização visual;
- formulários;
- tabelas;
- modais;
- navegação;
- feedback visual ao usuário.

O SETOR DE INTERFACE / UX não deve alterar regras de negócio para resolver problemas exclusivamente visuais.

---

## SETOR DE QA / TESTES

Responsável por:

- pytest;
- testes unitários;
- testes de integração;
- testes de regressão;
- reprodução de bugs;
- validação de correções;
- testes de fluxos completos;
- testes de edge cases;
- testes de concorrência;
- testes de migrations;
- validação antes de considerar uma tarefa concluída.

O SETOR DE QA / TESTES deve preferencialmente identificar e documentar problemas.

O SETOR DE QA / TESTES não deve realizar grandes refatorações fora de seu escopo sem autorização do SETOR DE COORDENAÇÃO.

---

## SETOR DE SEGURANÇA

Responsável por:

- autenticação;
- autorização;
- permissões;
- sessões;
- controle de acesso;
- auditoria;
- validação de entrada;
- proteção contra POST direto e manipulação de payload;
- proteção de dados;
- exposição indevida de informações;
- funções administrativas;
- acesso de suporte;
- integridade de operações sensíveis.
- cancelamentos.

O SETOR DE SEGURANÇA deve revisar especialmente alterações relacionadas a:

- usuários;
- administração;
- Caixa;
- pagamentos;
- suporte remoto;
- assinatura/licenciamento;
- permissões.

---

# Regra de propriedade

Cada setor deve trabalhar preferencialmente em sua própria área.

Dois agentes não devem editar simultaneamente o mesmo arquivo sem coordenação explícita.

Quando uma alteração atingir mais de um setor, o SETOR DE COORDENAÇÃO deve organizar a ordem de trabalho.

Exemplo:

SETOR DE BANCO DE DADOS
↓
SETOR DE BACKEND
↓
SETOR DE INTERFACE / UX
↓
SETOR DE QA / TESTES
↓
SETOR DE SEGURANÇA
↓
SETOR DE COORDENAÇÃO

A ordem pode mudar conforme a tarefa.

---

# Regra contra alterações desnecessárias

Não alterar código fora da tarefa atual.

Não realizar refatoração ampla apenas por preferência técnica.

Não alterar arquitetura existente sem necessidade.

Não renomear arquivos, funções, models ou rotas sem motivo relacionado à tarefa.

Não remover funcionalidades existentes sem autorização.

---

# Proteção de dados

Nunca utilizar dados reais em testes destrutivos.

Nunca:

- apagar banco real;
- resetar banco real;
- substituir banco real por seed;
- remover histórico real;
- alterar registros reais apenas para facilitar testes.

Sempre preservar dados existentes.

---

# Segurança do Git

O repositório pode estar público durante desenvolvimento.

Nunca enviar:

- `.env`;
- `.env.local`;
- tokens;
- senhas reais;
- chaves privadas;
- banco SQLite real;
- backups;
- arquivos contendo dados de clientes;
- credenciais;
- logs contendo informações sensíveis.

Antes de commit/push, conferir o que será enviado.

---

# TODO.md

O arquivo:

TODO.md

é o quadro operacional da tarefa atual.

Ele NÃO é um histórico permanente do projeto.

O histórico fica em:

- Git;
- commits;
- documentação;
- relatórios técnicos.

Quando uma nova tarefa substituir a anterior, o conteúdo operacional antigo do TODO deve ser removido.

O TODO deve possuir somente:

## A Fazer
## Em andamento
## Concluídas

Não criar outras seções operacionais.

---

# Pesos

Tarefas podem possuir:

{weight:N}

Exemplo:

- [ ] Criar tela de Nota de Serviço {weight:5}

Escala recomendada:

1 = muito simples
2-3 = simples
4-5 = moderada
6-8 = complexa
9-12 = grande
13-20 = muito grande

Tarefas maiores que isso devem preferencialmente ser divididas.

---

# Subtarefas e peso

Quando uma tarefa principal possui subtarefas com peso:

- o peso efetivo da tarefa principal é a soma das subtarefas;
- não duplicar o peso do pai;
- não informar percentual manualmente.

Exemplo correto:

- [ ] Criar Nota de Serviço {sector:SETOR_COORDENACAO}
  - [ ] Criar models {weight:3} {sector:SETOR_BANCO_DADOS}
  - [ ] Criar regras {weight:4} {sector:SETOR_BACKEND}
  - [ ] Criar interface {weight:3} {sector:SETOR_INTERFACE_UX}
  - [ ] Criar testes {weight:2} {sector:SETOR_QA_TESTES}

Peso total:

12 pontos.

---

# Setores no TODO

Toda tarefa relevante pode possuir:

{sector:NOME}

Setores válidos:

{sector:SETOR_COORDENACAO}
{sector:SETOR_BACKEND}
{sector:SETOR_BANCO_DADOS}
{sector:SETOR_INTERFACE_UX}
{sector:SETOR_QA_TESTES}
{sector:SETOR_SEGURANCA}

Não inventar novos nomes sem atualizar este arquivo e a ferramenta de TODO.

---

# Estados

O estado é determinado pela seção onde a tarefa está.

## A Fazer

Ainda não iniciada.

## Em andamento

Está sendo executada agora.

## Concluídas

Terminada e validada.

Preferencialmente manter somente uma tarefa principal em `Em andamento`.

---

# Atualização do TODO

Antes de iniciar:

1. registrar tarefas em `A Fazer`;
2. definir pesos;
3. definir setores;
4. dividir subtarefas quando necessário.

Ao começar:

mover a tarefa para:

`Em andamento`

Conforme concluir subtarefas:

`[ ]` → `[x]`

Quando toda a tarefa estiver validada:

mover para:

`Concluídas`

---

# Kanban / Todo Sidebar

O Todo Sidebar utiliza TODO.md como fonte.

O formato deve continuar compatível com:

- checkboxes Markdown;
- pesos `{weight:N}`;
- setores `{sector:NOME}`;
- subtarefas indentadas.

O painel deve futuramente exibir:

- progresso global;
- pontos concluídos / pontos totais;
- A Fazer;
- Em andamento;
- Concluídas;
- contador por seção;
- progresso individual;
- subtarefas expansíveis;
- setor responsável;
- spinner em tarefa ativa.

Não alterar o formato do TODO sem considerar a compatibilidade com essa ferramenta.

---

# Processo de uma tarefa

Fluxo recomendado:

USUÁRIO
↓
SETOR DE COORDENAÇÃO
↓
planejamento
↓
setores necessários
↓
implementação
↓
SETOR DE QA / TESTES
↓
SETOR DE SEGURANÇA quando aplicável
↓
SETOR DE COORDENAÇÃO
↓
validação final
↓
Git

---

# Antes de modificar o projeto

Sempre conferir:

- tarefa atual;
- arquivos envolvidos;
- impacto nos outros módulos;
- estado do Git;
- banco utilizado;
- testes existentes.

Para trabalhos relevantes:

git status

deve ser conferido antes e depois.

---

# Critério de conclusão

Uma tarefa só pode ser considerada concluída quando:

- implementação estiver pronta;
- dados existentes estiverem preservados;
- testes relevantes passarem;
- regressões conhecidas forem verificadas;
- TODO estiver atualizado;
- SETOR DE COORDENAÇÃO revisar o resultado.

Se algo permanecer pendente, registrar claramente no TODO.
