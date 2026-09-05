# Transferência do ERP — 05/09/2026

## Local de trabalho

O ERP completo está em **`D:\NexStudio\sistema ERP`**.

Foram transferidos código, templates, componentes, estilos, scripts, testes,
documentação, TODO, configurações do VS Code, ambiente Python, configuração
local, banco SQLite, backups e artefatos. A transferência foi local, sem
instalação de dependências pela internet ou acesso a serviços externos.

Por orientação do usuário, o `.git` do destino foi mantido. O `.git` antigo
permanece em `D:\carcaça erp\.git`; é a única entrada que ficou na origem.
Nenhum dos dois históricos foi apagado ou mesclado. Nenhum commit ou push foi
executado durante a transferência. O `.gitattributes` existente no destino
também foi preservado.

## Integridade e preservação

| Verificação | Resultado |
|---|---|
| Arquivos do ERP conferidos no ato da transferência | 4.115 |
| Diretórios conferidos | 466 |
| Total de bytes desses arquivos | 78.684.847 |
| Arquivos faltando, adicionados ou alterados na comparação inicial | 0 |
| Git existente no destino | 1.725 arquivos conferidos, idênticos |
| SQLite | Integridade `ok`; todas as tabelas idênticas ao inventário prévio |
| Configuração `.env.local` | Preservada byte a byte; conteúdo não exibido |
| Usuários e permissões | Preservados no mesmo banco local |
| Dados atuais | 1 cliente, 2 serviços, 0 movimentações de caixa, 2 usuários |
| Backups anteriores | Transferidos sem alteração |

O inventário de transferência exclui somente `.git`, `.gitattributes` e as
próprias evidências geradas em `artifacts/relocation`. Essa pasta de evidências
também foi movida. As alterações posteriores deliberadas são os ajustes de
caminhos/launchers, documentação, TODO, configuração do VS Code e novas
evidências de teste.

SHA-256 do banco preservado:

```text
d4ae28b2c3c05b5c8f52b92e1bafdf567a8519b0a8a65c96a717503467f8e309
```

Backup adicional anterior à transferência:
`backups/pre_testes/20260905_150224_221042/`.

## Ajustes para o novo caminho

- Caminhos de ativação da `.venv`, metadados da instalação editável e 12
  launchers locais foram ajustados, sem baixar ou atualizar pacotes.
- Os arquivos anteriores a esses ajustes foram guardados em
  `backups/relocation/python_before_repair_20260905/`.
- O VS Code usa o interpretador relativo `.venv/Scripts/python.exe` e o
  Todo Sidebar continua lendo `./TODO.md`.
- O banco, templates e arquivos estáticos já eram relativos à raiz do projeto.
  Nenhuma regra de negócio precisou mudar.
- Referências antigas em relatórios históricos foram mantidas como registro
  das verificações realizadas naquele local.
- A instalação base do Python e o WebView2 continuam sendo os runtimes deste
  computador. Não é necessário manter a pasta antiga para executar o ERP.
- A configuração de sessão e o perfil normal do pywebview não foram alterados.

## Validação após a mudança

- `pip check`: nenhuma dependência quebrada.
- `pip`, `pytest` e `uvicorn`: executáveis respondendo no novo caminho.
- Importação de `app` feita fora da pasta do projeto: aponta para o novo local.
- Suíte completa: **299 testes passaram**, em 102,21 segundos.
- Um aviso de depreciação já conhecido de Starlette/TestClient sobre `httpx`;
  nenhuma falha de teste. Dependências não foram trocadas nesta tarefa.
- Desktop real em cópia descartável: login, três ciclos de abertura,
  persistência de sessão após reabrir e consulta ao resumo passaram.
- Recuperação de encerramento abrupto na cópia descartável: integridade
  preservada, sem servidor órfão. A cópia de teste foi removida ao terminar.
- O SQLite operacional não recebeu os dados dos testes; as tabelas e o hash
  foram reconferidos depois da suíte.
- Host configurado: somente `127.0.0.1`, porta `8765`.
- Janela normal **ERP** aberta a partir da nova `.venv`; endpoint local
  `/health` respondeu HTTP 200. Banco e configuração permaneceram idênticos
  também após essa abertura. Não foi feito login automático no banco normal
  nem alterado o perfil de sessão para esta verificação.

Evidências em `artifacts/relocation/`: inventários antes/depois, comparação
do Git do destino, `pytest_after_move.xml` e `desktop/desktop_results.json`.

## Como continuar

Abra `D:\NexStudio\sistema ERP` como pasta no VS Code. Para iniciar:

```powershell
Set-Location 'D:\NexStudio\sistema ERP'
.\.venv\Scripts\python.exe run_desktop.py
```

O tutorial do operador está também em `Docs/TUTORIAL_DO_OPERADOR_ERP.txt`.
O original solicitado anteriormente continua em `D:\tutorial`.

Os arquivos sensíveis, banco, ambiente virtual, backups e artefatos continuam
ignorados pelo Git. O novo repositório já continha entradas ausentes de um
perfil WebView/browser no caminho `carcaça/` antes desta tarefa; elas não foram
restauradas, apagadas do histórico ou incluídas em commit por esta transferência.
