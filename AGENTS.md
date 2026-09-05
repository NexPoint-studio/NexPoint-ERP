# ERP — Equipe de Agentes

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

Este projeto utiliza agentes especializados por setor.

O agente principal/coordenador deve analisar cada tarefa e utilizar somente os setores necessários.

Nem toda tarefa precisa envolver todos os agentes.

Uma alteração pequena deve usar somente o setor responsável.

Uma alteração grande pode ser dividida entre vários setores.

---

# Setores oficiais

## COORDENACAO

Responsável principal:

Astra

Responsabilidades:

- entender a solicitação do usuário;
- analisar o impacto da mudança;
- dividir trabalhos grandes em tarefas menores;
- selecionar quais setores participarão;
- delegar trabalho para subagentes;
- controlar o TODO.md;
- evitar conflitos entre agentes;
- integrar mudanças feitas pelos setores;
- revisar o resultado global;
- organizar Git e commits;
- garantir que o ERP continue funcional;
- impedir alterações fora do escopo solicitado.

COORDENACAO possui visão global do projeto.

---

## BACKEND

Responsável por:

- Python;
- FastAPI;
- regras de negócio;
- services;
- routes;
- schemas;
- validações de negócio;
- contratos entre módulos;
- integrações internas;
- processamento de dados;
- fluxos operacionais.

BACKEND não deve alterar banco ou frontend sem necessidade técnica justificada.

---

## BANCO

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

Regras obrigatórias:

- nunca apagar banco real para facilitar desenvolvimento;
- nunca recriar banco real sem autorização;
- mudanças de schema devem preservar dados existentes;
- testes destrutivos usam banco isolado;
- migrations devem ser testadas;
- valores financeiros não devem utilizar float.

---

## FRONTEND

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

FRONTEND não deve alterar regras de negócio para resolver problemas exclusivamente visuais.

---

## QA

Responsável por:

- pytest;
- testes unitários;
- testes de integração;
- testes de regressão;
- reprodução de bugs;
- validação de correções;
- testes de fluxos completos;
- testes de edge cases;
- validação antes de considerar uma tarefa concluída.

QA deve preferencialmente identificar e documentar problemas.

QA não deve realizar grandes refatorações fora de seu escopo sem autorização da COORDENACAO.

---

## SEGURANCA

Responsável por:

- autenticação;
- autorização;
- permissões;
- sessões;
- controle de acesso;
- auditoria;
- validação de entrada;
- proteção de dados;
- exposição indevida de informações;
- funções administrativas;
- acesso de suporte;
- integridade de operações sensíveis.

SEGURANCA deve revisar especialmente alterações relacionadas a:

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

Quando uma alteração atingir mais de um setor, COORDENACAO deve organizar a ordem de trabalho.

Exemplo:

BANCO
↓
BACKEND
↓
FRONTEND
↓
QA
↓
SEGURANCA
↓
COORDENACAO

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

- [ ] Criar Nota de Serviço {sector:COORDENACAO}
  - [ ] Criar models {weight:3} {sector:BANCO}
  - [ ] Criar regras {weight:4} {sector:BACKEND}
  - [ ] Criar interface {weight:3} {sector:FRONTEND}
  - [ ] Criar testes {weight:2} {sector:QA}

Peso total:

12 pontos.

---

# Setores no TODO

Toda tarefa relevante pode possuir:

{sector:NOME}

Setores válidos:

{sector:COORDENACAO}
{sector:BACKEND}
{sector:BANCO}
{sector:FRONTEND}
{sector:QA}
{sector:SEGURANCA}

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
COORDENACAO
↓
planejamento
↓
setores necessários
↓
implementação
↓
QA
↓
SEGURANCA quando aplicável
↓
COORDENACAO
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
- COORDENACAO revisar o resultado.

Se algo permanecer pendente, registrar claramente no TODO.