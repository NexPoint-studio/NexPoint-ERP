# ERP local integrado

Sistema de gestão autocontido para operação local. O projeto combina FastAPI,
SQLAlchemy, SQLite, Jinja2 e pywebview e funciona integralmente no computador,
inclusive sem internet.

O ERP possui módulos funcionais de Clientes, Serviços, Notas de Serviço, Caixa
e Administração. O fluxo principal integrado é:

```text
Cliente -> Nota de Serviço -> Pagamento -> Caixa
```

Uma Nota alimenta o histórico do Cliente. Cada valor recebido, integral ou
parcial, gera seu próprio registro auditável e a entrada correspondente no
Caixa, na mesma transação. O fechamento encerra a operação da Nota e cria um
saldo devedor vinculado à sua origem somente se ainda houver valor a receber.
Notas de total zero ficam pagas sem criar Pagamento ou movimentação.

A área Administração oferece ao Proprietário:

- visão geral com alertas de produção e relacionamento;
- gestão do catálogo, categorias, preços e unidades de cobrança;
- visão financeira global, histórico e relatórios;
- formas de pagamento, terminais e regras de taxa;
- abertura e histórico de chamados para a NexPoint;
- consulta de auditoria;
- informações do sistema, backup e restauração local controlada.

A experiência normal do cliente não expõe gestão manual de usuários nem edição
manual da empresa. Autenticação, proprietário, papéis, permissões, dados
institucionais já gravados, auditoria e a estrutura segura de acesso temporário
continuam preservados internamente. Essa base permite que um onboarding futuro
grave a identidade da empresa sem perder os dados existentes.

O Caixa operacional mostra ao operador apenas os lançamentos que ele próprio
registrou nos últimos sete dias. Saldos, totais, relatórios e agregações globais
ficam protegidos pelas permissões financeiras administrativas.

Pela interface normal é possível criar uma Nota sem pagamento ou registrar um
pagamento inicial, abrir o detalhe clicando na linha da listagem, editar enquanto
aberta, receber parcelas, acompanhar o histórico e fechar. Total recebido,
saldo e os estados **Não pago**, **Parcialmente pago**, **Pago** e **Saldo
devedor** vêm do ledger; não são escolhidos manualmente. Fechar não cria Caixa.
Se restar dívida, cada quitação posterior registra somente o dinheiro realmente
recebido e preserva a Nota de origem.

O cadeado da Administração possui um único fluxo de recuperação para o
Proprietário: **Solicitar recuperação à NexPoint**. A Outbox preserva a
solicitação offline e um `platform_admin` autoriza a redefinição no Control
Center. O ERP reconhece essa autorização automaticamente e mostra apenas os
campos da nova senha; o cliente não copia código, ID ou token. A Nexa apenas
orienta o uso desse botão. Ela não vê senhas e não redefine credenciais. Não
existe senha mestra NexPoint.

No login normal, **Manter conectado neste dispositivo** persiste somente uma
sessão opaca, expirável e revogável. Senhas nunca são salvas. A sessão funciona
offline no computador vinculado, mas não desbloqueia a Administração. Consulte
o [contrato da sessão persistente](Docs/SESSAO_PERSISTENTE.md).

## Preparar o ambiente próprio

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
Copy-Item .env.example .env.local
```

O projeto não distribui senhas ou segredos padrão. Antes da primeira execução,
substitua os marcadores `SUBSTITUA` necessários em `.env.local`. A senha do ERP
precisa ter pelo menos oito caracteres; os segredos de sessão, pelo menos 32;
a senha interna do Control Center, pelo menos 12. Use credenciais diferentes no
ERP e no Control Center. O arquivo `.env.local` é ignorado pelo Git.

Na primeira inicialização, os valores do ambiente preenchem configurações ainda
ausentes. Depois disso, nome da empresa, nome do aplicativo, versão e fuso salvos
no SQLite prevalecem na interface. Segredo de sessão, host, porta e caminho do
banco continuam sendo configurações técnicas locais do ambiente. A variável
interna `ERP_LOGO_PATH` continua aceita por compatibilidade com instalações
anteriores, mas a interface neutra não exibe o placeholder visual de logo e o
exemplo de ambiente não recomenda configurá-lo.

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

## Central de Suporte e Control Center

Em **Administração > Suporte**, o cliente pode abrir e consultar chamados. O ERP
anexa apenas contexto técnico sanitizado, como módulo, tela, versão, ambiente,
correlation ID, riscos e fingerprints disponíveis. O chamado continua sendo
criado quando a Nexa está indisponível. O mecanismo anterior de acesso temporário
permanece interno e separado do fluxo principal.

O link **Esqueci a senha** do cadeado também permite abrir um chamado
`admin_access_recovery` sem desbloquear a Administração. A identidade de
Proprietário ainda é obrigatória. Se o painel estiver indisponível, a solicitação
fica na Outbox local aguardando sincronização.

O **NexPoint ERP Control Center** é um aplicativo privado para a equipe NexPoint,
com autenticação própria e banco SQLite separado do banco operacional. Configure
os marcadores `CONTROL_CENTER_*` em `.env.local` e execute:

```powershell
.\.venv\Scripts\python.exe run_control_center.py
```

Ele abre somente em `http://127.0.0.1:8770`. O usuário proprietário do ERP não
recebe acesso ao painel interno. Defina `CONTROL_CENTER_SEED_DEMO=1` apenas para
carregar as empresas Alfa, Beta e Gama, todas marcadas como dados fictícios. O
V1 não usa cloud, não consulta SQL arbitrário e não permite operações financeiras
ou operacionais nos ERPs acompanhados. Consulte o
[guia do Control Center](Docs/CONTROL_CENTER.md).

## Observabilidade e QA

Para diagnosticar a instalação e executar os testes isolados da etapa sem criar
nem resetar o ambiente QA:

```powershell
.\.venv\Scripts\python.exe scripts\erp_doctor.py
.\.venv\Scripts\python.exe -m pytest -q tests\test_functional_ux_recovery.py tests\test_observability_qa.py tests\test_erp_doctor_observability.py tests\test_control_center_observability_ui.py tests\test_qa_fault_injection_e2e.py tests\test_qa_observability_nexa_integration.py tests\test_control_center_log_authorization.py tests\test_qa_environment_script.py tests\test_qa_launch_workflow.py
```

O Doctor é somente leitura. Além do preflight, ele confere os vínculos entre
Payment, Receivable e Caixa, a configuração do cadeado, sessões persistentes,
códigos legados indevidamente ativos, autorizações expiradas, chamados de
recuperação travados e indícios de credenciais em logs. Ele reporta a
inconsistência sem exibir o segredo e não repara o banco automaticamente.

Consulte o guia de [Observabilidade e QA](Docs/OBSERVABILIDADE_QA.md) para a
arquitetura, correlação, retenção, sync, Log Explorer, Nexa somente leitura,
ambiente TEST e limites de segurança.

Depois de criar deliberadamente o ambiente fictício conforme o guia, valide o
launcher isolado com:

```powershell
.\.venv\Scripts\python.exe run_qa.py --qa --confirm ABRIR-NEXPOINT-QA --check
```

## Demonstração persistente de dois anos

O projeto inclui um gerador determinístico e um launcher separado para navegar
em um cenário fictício volumoso sem alterar o banco operacional:

```powershell
.\.venv\Scripts\python.exe scripts\generate_demo_2_years.py --demo --confirm CRIAR-DEMO-2-ANOS
.\.venv\Scripts\python.exe run_demo.py
```

O banco demo permanece local em `data/demo_2_anos.sqlite3`, usa a porta `8766`
e é ignorado pelo Git. Consulte o guia de
[demonstração com dois anos](Docs/DEMO_2_ANOS.md) para isolamento, validação e
credenciais fictícias.

Consulte a [arquitetura](Docs/ARQUITETURA.md), a
[evolução offline e financeira](Docs/EVOLUCAO_OFFLINE_FINANCEIRA.md), o
[cadeado da Administração](Docs/CADEADO_ADMINISTRACAO.md), a
[sessão persistente do login](Docs/SESSAO_PERSISTENTE.md), a
[integração local com a Nexa](Docs/INTEGRACAO_NEXA.md), o
[Control Center](Docs/CONTROL_CENTER.md), a
[Observabilidade e QA](Docs/OBSERVABILIDADE_QA.md) e os guias de
[Clientes](Docs/CLIENTES.md), [Serviços](Docs/SERVICOS.md),
[Notas de Serviço](Docs/NOTAS_SERVICO.md) e [Caixa](Docs/CAIXA.md), além do guia
de [Administração e Pagamentos](Docs/PAGAMENTOS_ADMINISTRACAO.md) e do guia de
[Backup, restauração e atualização](Docs/BACKUP_RESTAURACAO_ATUALIZACAO.md). Para reutilizar
a base em outro produto, leia também [Como criar um novo ERP](Docs/COMO_CRIAR_NOVO_ERP.md).
