# Ambiente de demonstração com dois anos

## Finalidade

O ambiente demo representa uma empresa fictícia de serviços entre 01/09/2024
e 09/09/2026. Ele serve para inspeção visual, treinamento e avaliação de volume
sem copiar ou alterar dados da instalação operacional.

O banco persistente fica exclusivamente em:

```text
D:\NexStudio\sistema ERP\data\demo_2_anos.sqlite3
```

Esse arquivo e os backups locais estão protegidos pelo `.gitignore`. O banco
operacional continua em `data/erp.sqlite3` e não é aberto pelo gerador nem pelo
launcher demo.

## Gerar o dataset

Execute a partir da raiz do projeto:

```powershell
.\.venv\Scripts\python.exe scripts\generate_demo_2_years.py --demo --confirm CRIAR-DEMO-2-ANOS
```

O gerador usa a seed fixa `20260909`, cria um SQLite novo, aplica as migrations
oficiais, grava o marcador `demo.profile=nexpoint-2-years-20260909`, popula os
módulos e executa validações de integridade e consistência. A identidade opaca
usada pelo Control Center também é derivada desse perfil fixo, de modo que a
mesma geração continue reproduzível e não se misture com a instalação real. A
confirmação exata é obrigatória e o caminho do banco operacional é recusado
mesmo se informado indiretamente por link.

O banco existente não é substituído silenciosamente. Uma nova geração exige a
opção explícita `--replace-existing`; antes da troca atômica, o script cria uma
cópia local do demo anterior.

## Abrir o ERP em modo demo

```powershell
.\.venv\Scripts\python.exe run_demo.py
```

O launcher valida schema, integridade, chaves estrangeiras, marcador demo e
Proprietário fictício antes de abrir, e informa o SHA-256 no modo `--check`. Ele usa somente
`127.0.0.1:8766`, cria um segredo de sessão novo a cada processo e não lê senha
ou segredo da instalação operacional.

Credencial principal, exclusivamente demonstrativa:

```text
Login: proprietario@demo.local
Senha: Demo#2026!ERP
```

Os demais usuários fictícios são exibidos na Administração e possuem papéis
diferentes. Essa senha pública de demonstração nunca deve ser reutilizada em
uma instalação real.

Para validar o arquivo sem abrir servidor ou janela:

```powershell
.\.venv\Scripts\python.exe run_demo.py --check
```

## Backup e restauração

O modo demo permite criar e exportar backup normalmente. A restauração direta
sobre o dataset persistente é bloqueada pelo launcher para preservar o cenário
de referência. Ensaios de restauração devem usar uma cópia isolada, como ocorre
na validação desta tarefa.

Os exemplos de suporte do dataset são somente históricos, expirados ou
revogados. Não existe concessão de suporte ativa nem acesso permanente.

## Variante financeira A–D

Para demonstrar os fluxos novos sem reescrever o perfil histórico de 3.000
Notas, crie uma cópia financeira em um caminho fora do projeto. O arquivo de
origem é aberto somente para leitura e seu hash não muda:

```powershell
.\.venv\Scripts\python.exe scripts\seed_financial_demo.py `
  --demo --confirm CRIAR-DEMO-FINANCEIRO `
  --database "$env:TEMP\demo_2_anos_financeiro.sqlite3"
```

A variante acrescenta quatro registros claramente fictícios:

- A: Nota de R$ 300,00 com R$ 150,00 pagos;
- B: Nota de R$ 500,00 integralmente paga;
- C: Nota de R$ 200,00 fechada com saldo de R$ 100,00;
- D: nova Nota de R$ 200,00 que referencia o saldo de C, sem duplicá-lo.

O destino precisa ser novo, conter `demo_2_anos_financeiro` no nome e ficar
fora do checkout oficial. O script recusa o banco operacional, o demo oficial,
arquivos existentes, links e junctions. Antes da troca final ele valida as
quatro situações, `integrity_check` e `foreign_key_check`.
