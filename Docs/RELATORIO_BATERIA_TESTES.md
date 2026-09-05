# Bateria completa de testes

## Ambiente

| Item | Resultado |
|---|---|
| Projeto testado | `D:\carcaça erp` |
| Data da conclusão técnica | 05/09/2026 11:24 (America/Sao_Paulo) |
| Sistema | Windows 10 22H2 (build 19045) |
| Python | 3.13.3 |
| SQLite | 3.49.1 |
| FastAPI | 0.141.1 |
| SQLAlchemy | 2.0.52 |
| Jinja2 | 3.1.6 |
| Uvicorn | 0.52.4 |
| pywebview | série 6.x |
| Commit anterior à bateria | `ce31f777976e30458dd9a4bbe016bc5f6947f020` |
| Banco normal | `D:\carcaça erp\data\erp.sqlite3` |
| SHA-256 antes e depois | `afb53e08fb18b648c2bb3ad830846c6d807aa08597a01eebdbe5fe245e59483a` |

## Resumo executivo

O escopo técnico existente foi aprovado. Clientes, Serviços, Caixa, Administração,
autenticação, roles, permissões, auditoria, migrations, persistência, interface
responsiva e execução nativa foram exercitados. Nenhum módulo futuro foi criado.

| Métrica | Resultado |
|---|---:|
| Testes existentes antes da bateria | 84 |
| Testes novos, incluindo casos parametrizados | 215 |
| Testes finais | 299 |
| Aprovados | 299 |
| Falhos | 0 |
| Ignorados | 0 |
| Duração da regressão final | 160,16 s |
| Verificações visuais de rota/resolução | 76 |
| Erros JavaScript/console | 0 |
| Requisições externas tentadas | 0 |

O XML da regressão está em `artifacts/regression_resumed.xml`; os resultados
visuais estão em `artifacts/visual/results.json` e os ciclos nativos em
`artifacts/desktop_results.json`. Esses artefatos são locais e ignorados pelo Git.

## Proteção e integridade dos dados

Antes da bateria foi criado e verificado o backup:

`backups/pre_testes/20260905_091258_483207/erp.sqlite3`

Também foram tirados snapshots de reconciliação durante a retomada. Todos tiveram
o mesmo SHA-256 do banco normal, `PRAGMA integrity_check = ok` e nenhuma falha
em `PRAGMA foreign_key_check`.

Inventário final do banco normal:

| Tabela/entidade | Registros |
|---|---:|
| Usuários | 2 |
| Papéis | 3 |
| Vínculos usuário/papel | 2 |
| Permissões | 22 |
| Vínculos papel/permissão | 30 |
| Feature flags | 3 |
| Configurações | 9 |
| Migrations | 6 |
| Formas de pagamento | 5 |
| Eventos de auditoria | 25 |
| Clientes, atividades e endereços | 0 |
| Serviços, categorias e preços | 0 |
| Categorias e movimentos do Caixa | 0 |

O banco normal permaneceu byte a byte igual pelo SHA-256. Todos os lançamentos,
cancelamentos, falhas, concorrência e carga foram executados em bancos temporários.
Nenhum registro legítimo foi perdido ou alterado.

Os projetos protegidos permaneceram fora do escopo de escrita:

- `D:\NexStudio\caixa`: HEAD `230c4b6c3755c71480cfc31e78a19fc34fad28e1`
  e os 64 itens sujos preexistentes foram preservados.
- `D:\NexStudio\nil_lav_erp_v2`: HEAD
  `03a84a6706d9a93ce7cd94439c6db79302d61732`, árvore limpa.

## Bugs encontrados e correções

A contagem abaixo considera causas-raiz que exigiram alteração no código do ERP.
Erros encontrados somente nos roteiros de teste foram corrigidos nos roteiros e
não foram contabilizados como defeitos do produto.

| ID | Severidade | Área | Problema encontrado | Estado |
|---|---|---|---|---|
| BT-01 | CRÍTICO | Persistência | Falha na auditoria/commit podia deixar uma mutação parcial sem rollback garantido. | Corrigido, retestado e aprovado |
| BT-02 | CRÍTICO | Financeiro | Entradas monetárias ambíguas, não finitas ou fora do limite não eram rejeitadas de forma uniforme. | Corrigido, retestado e aprovado |
| BT-03 | CRÍTICO | Bootstrap | Reinicialização podia sobrescrever hash, status, papéis, permissões e configurações legítimas. | Corrigido, retestado e aprovado |
| BT-04 | ALTO | Serviços | Duas alterações concorrentes de preço podiam disputar a mesma vigência ativa. | Corrigido, retestado e aprovado |
| BT-05 | ALTO | Clientes/Serviços | Corridas de unicidade podiam terminar em erro 500 em vez de erro de domínio. | Corrigido, retestado e aprovado |
| BT-06 | ALTO | Desktop | Porta ocupada e encerramento incompleto não tinham ciclo robusto e mensagem clara. | Corrigido, retestado e aprovado |
| BT-07 | ALTO | Segurança local | Host, origem de escrita e identificador de sessão precisavam de limites explícitos. | Corrigido, retestado e aprovado |
| BT-08 | ALTO | Autenticação | Hash scrypt malformado ou com parâmetros extremos podia provocar exceção/custo indevido. | Corrigido, retestado e aprovado |
| BT-09 | ALTO | Windows | Registro MIME do Windows podia servir JS como texto e o `nosniff` bloqueava formulários/modais. | Corrigido, retestado e aprovado |
| BT-10 | ALTO | Erros/logs | Falhas internas não possuíam resposta e log sanitizados de forma central. | Corrigido, retestado e aprovado |
| BT-11 | MÉDIO | Clientes | Formatar e editar telefone podia descartar o DDI. | Corrigido, retestado e aprovado |
| BT-12 | MÉDIO | Busca | Unicode e caracteres `%`, `_` e barra eram tratados de forma inconsistente ou como curingas. | Corrigido, retestado e aprovado |
| BT-13 | MÉDIO | Rotas/filtros | IDs e números extremos podiam causar overflow/erro 500. | Corrigido, retestado e aprovado |
| BT-14 | MÉDIO | Clientes | Data de visita inválida era silenciosamente substituída pela data atual. | Corrigido, retestado e aprovado |
| BT-15 | MÉDIO | Datas | Limites exatos de inatividade e conversão UTC/local divergiam em casos de fronteira. | Corrigido, retestado e aprovado |
| BT-16 | MÉDIO | Paginação | Página fora do intervalo, empate de ordenação e tela de preços não eram totalmente determinísticos. | Corrigido, retestado e aprovado |
| BT-17 | MÉDIO | UI/Persistência | Duplo clique e confirmação reutilizável podiam repetir uma ação de escrita. | Corrigido, retestado e aprovado |
| BT-18 | BAIXO | Responsividade | A tabela do Resumo expandia o grid no viewport de 390 px. | Corrigido, retestado e aprovado |
| BT-19 | BAIXO | Responsividade | Radios absolutos do período ampliavam a página do Histórico móvel. | Corrigido, retestado e aprovado |
| BT-20 | BAIXO | Responsividade | Cabeçalhos divididos ficavam comprimidos no mobile. | Corrigido, retestado e aprovado |

Totais: **20 bugs**, sendo **3 críticos**, **7 altos**, **7 médios** e
**3 baixos**. Todos foram corrigidos e retestados. Não há bug crítico ou alto
conhecido em aberto.

## Cobertura funcional

| Grupo | Evidência/resultado |
|---|---|
| Inicialização | FastAPI e pywebview iniciaram e encerraram corretamente em 3 ciclos |
| Porta ocupada | Rejeitada antes do bootstrap com mensagem explícita |
| Autenticação | Admin, User e Delivery; senha errada; sessão alterada/expirada; logout e usuário inativo |
| Sessão | Preservada em duas reaberturas nativas; removida no logout |
| Permissões | GET e POST negados diretamente quando o papel não possui permissão |
| Feature flags | Módulo oculto e rota bloqueada quando desativado |
| Clientes | CRUD existente, duplicidade, DDI, busca, filtros, paginação, visita, status e auditoria |
| Serviços | Catálogo, categorias, preços 30/35/40, histórico, concorrência, filtros, status e auditoria |
| Caixa | Entrada, saída, taxa, boleto, edição, cancelamento, histórico, filtros, paginação e relatórios |
| Migrations | Criação, upgrade, repetição idempotente e preservação das tabelas existentes |
| Persistência | Commit preservado após crash e escrita sem commit revertida |
| Auditoria | Ações, usuário, antes/depois e campos alterados; senha nunca registrada |
| Casos extremos | Unicode, XSS, SQL-like, limites numéricos, datas e concorrência |

## Reconciliação financeira

A sequência descartável usada na validação visual foi:

- entrada de R$ 1.000,00;
- entrada bruta de R$ 500,00 com taxa de R$ 15,00;
- saída de R$ 200,00;
- saída de R$ 75,50;
- entrada de R$ 100,00 posteriormente cancelada.

Banco, serviço, Resumo, Histórico e Relatórios apresentaram o mesmo resultado:
**R$ 1.209,50**. Movimentos cancelados e suas taxas foram excluídos das
agregações sem apagar o histórico. Todos os valores foram calculados com
`Decimal`/centavos, sem ponto flutuante financeiro.

## Stress e performance

O stress foi feito em banco temporário com **27.050 registros de domínio**:

- 5.000 clientes;
- 1.000 serviços;
- 25 categorias de serviço e 25 categorias do Caixa;
- 1.000 vigências de preço;
- 20.000 movimentos financeiros.

Reconciliação independente da carga:

- entradas: 129.704.343 centavos;
- saídas: 64.861.629 centavos;
- taxas: 38.897 centavos;
- correspondência financeira: aprovada.

Medianas observadas em três amostras:

| Operação | Mediana |
|---|---:|
| Inicialização com a carga | 3.379,02 ms |
| Lista de clientes | 12,47 ms |
| Busca de clientes | 47,20 ms |
| Filtro de clientes | 15,33 ms |
| Outra página de clientes | 10,85 ms |
| Lista de serviços | 10,43 ms |
| Histórico do Caixa (20 mil) | 258,80 ms |
| Relatório mensal | 155,81 ms |
| Relatório anual | 404,10 ms |

Os planos de consulta confirmaram uso de índice no período do Caixa e na busca
por documento. A base de stress foi removida ao final.

## Interface e execução Windows

Foram validadas 19 rotas em 4 resoluções: 1920×1080, 1366×768, 1280×720 e
390×844. Total: **76 verificações**, sem overflow final, sem erro de console e
sem recurso externo.

Fluxos cobertos: login, criação/edição de cliente, visita, status, serviço,
três vigências de preço, categorias, entradas, taxa, saídas, cancelamento,
Resumo, Histórico, Relatórios, refresh, proteção de duplo submit e menu mobile.

No teste real de pywebview:

- ciclo 1: login exibido, login concluído e saldo R$ 123,45;
- ciclos 2 e 3: sessão reaproveitada e saldo R$ 123,45;
- tempos até o Resumo: 5.062,72 ms, 1.420,73 ms e 1.222,46 ms;
- encerramento forçado preservou o commit e reverteu a escrita sem commit;
- não restou servidor na porta 8877;
- a cópia portátil não continha banco nem `.env` operacional;
- a cópia e o perfil de teste foram removidos.

## Segurança e operação offline

- O FastAPI aceita apenas `127.0.0.1`; `0.0.0.0` e hosts não locais são recusados.
- Não existe pacote Supabase nem Firebase no ambiente.
- Não existe SDK ou configuração Cloudflare, banco remoto, CDN, fonte remota,
  imagem remota, JS/CSS remoto ou chamada HTTP externa no produto.
- A única URL de runtime é o loopback usado pelo pywebview.
- O `xmlns` do SVG é apenas o namespace do formato, não uma requisição.
- Toda tentativa de rede externa foi bloqueada durante a validação; o contador
  observado foi zero.
- CSP, SameSite estrito, TrustedHost, validação de origem, headers de proteção,
  hash scrypt limitado e páginas 403/404/500 sanitizadas foram aprovados.
- `pip check`, compilação de imports e a suíte completa passaram.
- Não há segredo cloud versionado. Arquivos `.env`, bancos, logs, artefatos,
  backups e caches permanecem ignorados pelo Git.

## Pendências e riscos residuais

Não existe bloqueio técnico e não há vulnerabilidade importante conhecida em
aberto. Restam duas ressalvas não funcionais:

1. a aprovação visual final do usuário no aplicativo real aberto;
2. um aviso de depreciação do adaptador `TestClient` entre Starlette e httpx.
   Ele não causou falha nem afeta o aplicativo, mas deve ser eliminado numa
   manutenção de dependências controlada, sem atualização indiscriminada.

## Respostas objetivas

| # | Resposta |
|---:|---|
| 1 | Sim, as escritas da bateria ficaram somente em `D:\carcaça erp`. |
| 2 | Sim, os dois projetos NIL LAV permaneceram intactos. |
| 3 | Sim, backup pré-teste criado e verificado. |
| 4 | Sim, banco principal preservado com SHA-256 idêntico. |
| 5 | 84 testes existentes. |
| 6 | 215 casos novos, considerando parametrizações coletadas pelo pytest. |
| 7 | 299 testes no total. |
| 8 | 299 passaram. |
| 9 | 0 falharam. |
| 10 | 160,16 segundos. |
| 11 | 20 bugs de produto encontrados. |
| 12 | 3 críticos. |
| 13 | 7 altos. |
| 14 | 7 médios. |
| 15 | 3 baixos. |
| 16 | Sim, todos os críticos foram corrigidos. |
| 17 | Sim, todos os altos foram corrigidos. |
| 18 | Clientes aprovado. |
| 19 | Serviços aprovado. |
| 20 | Caixa aprovado. |
| 21 | Permissões aprovadas. |
| 22 | Autenticação aprovada. |
| 23 | Auditoria aprovada. |
| 24 | Persistência aprovada. |
| 25 | Migrations aprovadas. |
| 26 | Decimal financeiro aprovado. |
| 27 | Cancelamento financeiro aprovado. |
| 28 | Sim, saldo e relatórios reconciliaram em R$ 1.209,50. |
| 29 | Sim, stress executado em banco temporário. |
| 30 | 27.050 registros de domínio; 20.000 eram movimentos do Caixa. |
| 31 | Principais medianas: 12,47 ms clientes, 10,43 ms serviços, 258,80 ms histórico, 155,81 ms relatório mensal e 404,10 ms anual. |
| 32 | Sim, funciona completamente offline. |
| 33 | Não existe chamada externa no produto; zero tentativa observada. |
| 34 | Não existe Supabase. |
| 35 | Não existe Firebase. |
| 36 | Sim, somente `127.0.0.1`. |
| 37 | Sim; as vulnerabilidades importantes encontradas foram corrigidas. |
| 38 | Não permanece vulnerabilidade importante conhecida. |
| 39 | Não houve perda nem alteração indevida de dado legítimo. |
| 40 | O hash do commit de conclusão é informado na entrega, após versionar este relatório. |
| 41 | Painel mostra a conclusão técnica e somente a aprovação visual do usuário a fazer. |
| 42 | Não existe item bloqueado. |
| 43 | Não existe item técnico em revisão; resta a conferência humana. |
| 44 | `D:\carcaça erp\Docs\RELATORIO_BATERIA_TESTES.md`. |
| 45 | **APROVADO COM RESSALVAS**, apenas pela conferência humana e pelo aviso de depreciação. |
| 46 | Antes de nova funcionalidade, eliminar o aviso Starlette/httpx com uma atualização compatível, isolada e novamente testada. |

## Conclusão

**APROVADO COM RESSALVAS.**

Não existe falha conhecida, bug crítico/alto aberto, perda de dados ou dependência
cloud. A ressalva funcional é somente a aprovação visual humana após a abertura
do aplicativo; o aviso de depreciação deve ser tratado como manutenção técnica.
