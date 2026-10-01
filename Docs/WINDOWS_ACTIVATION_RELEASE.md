# Release Windows 1.1.0 - ativação automática

Código e build: `eef7592645a74cb27a79eeb0b0c2c4602d9e71ca`,
`PROD-eef7592645a7`. O fluxo e suas fronteiras estão em
[Instalação e ativação Windows](AUTOMATIC_ACTIVATION.md).

O instalador permite primeiro acesso por login, gera a chave no Windows final,
autoriza uma instalação nova e cria SQLite limpo com migrations oficiais. A
instalação anterior, seu banco e credencial não são transferidos nem rotacionados.

## Artefatos e evidência

- `NexPointERP-Setup.exe`, versão 1.1.0, SHA-256
  `4c1ed48a383d0acdd73c82d7b030aba16b6531ef89d820a52dcf003184045175`.
- Bundle ZIP de build, SHA-256
  `a5a19b469571b53ee46a43ed4f320fab8635a489ebb681bbdb0541fbed00f22d`.
- 914 arquivos do payload extraído conferidos contra a build; instalação,
  reinstalação e desinstalação aprovadas. Banco, configuração, credenciais e
  atalhos de Área de Trabalho existentes permaneceram iguais por hash.
- Seis PDFs, 36 páginas renderizadas e revisadas, 24 screenshots novos do
  executável, com dados sintéticos e tráfego externo bloqueado. A tela de primeiro
  acesso foi aberta em perfil vazio sem enviar senha ou consumir autorização.
- A entrega antiga foi arquivada integralmente fora da nova pasta, incluindo
  arquivo compactado preexistente. Os hashes dos arquivos foram comparados.

## Validações em 01/10/2026

| Superfície | Resultado |
| --- | --- |
| Python completo | 1041 aprovados; 25 warnings; 601,47 s |
| Cliente/gateway de ativação | 23 testes incluídos na suíte completa |
| Edge de ativação | 21 aprovados |
| PostgreSQL isolado | 35 checks, incluindo RLS, concorrência, replay e ACK/sync |
| Setup | 12 checks de instalação, reinstalação, extração e desinstalação |
| UI operacional do executável | 7 checks, integridade/FKs válidas, zero tráfego encaminhado |
| Git | release scan e diff check sem problemas |
| Secrets da entrega | zero achados; conteúdo do bundle, PKG/PYZ e ZIP expandido verificado |

Gitleaks no código: 12 ocorrências triadas (três hashes Git, dois placeholders,
quatro valores sintéticos de testes e três delimitadores PEM sem chave real).
TruffleHog: uma URL de exemplo em teste, sem verificação externa. Não houve achado
novo nas implementações da entrega. A varredura da distribuição também comparou
valores privados locais em memória, sem imprimi-los ou armazená-los no relatório.

A migration aditiva e `erp-activate` foram publicadas no projeto Supabase PROD
existente após backup protegido por DPAPI. Uma leitura final confirmou autorização
ativa ainda não consumida e preservação do tenant, instalação e credencial
anteriores. Nenhum deploy Render foi realizado; `autoDeploy: false` permanece.

## Limites e aceite pendente

O Setup ainda não tem assinatura Authenticode própria. O bootstrapper WebView2
incluído tem assinatura Microsoft validada. O Windows pode exigir confirmação
ou bloquear o instalador sem reputação; não se recomenda desativar proteções.

O ensaio usou este Windows com WebView2 presente, destino descartável, AppId e
atalho inicialmente ausentes. Não equivale a uma VM limpa ou ao notebook final.
O primeiro ensaio esperou um grupo alternativo do Menu Iniciar; a receita com
página de grupo desativada usa o grupo padrão. A expectativa do harness foi
corrigida; o ensaio completo seguinte aprovou também a remoção do atalho e AppId.

A ativação no notebook ainda não foi executada. Só no destino podem ser aceitos
DPAPI própria, primeiro/segundo start, heartbeat, health e ACK da instalação real.
O responsável confirmou que cadastrará manualmente serviços, unidades e preços
reais em Administração > Serviços após instalar no notebook e antes da primeira
Nota. Essa preparação exige acesso administrativo; a conta operacional mantém
suas permissões atuais. Não é necessário enviar o catálogo para preparar o pacote.
Nenhum catálogo fictício foi enviado à produção.

Os artefatos privados ficam em `Entrega/` e as evidências em `artifacts/`, ambos
ignorados pelo Git. O pacote não contém bancos, credenciais, dotenv, logs, scripts
de teste ou ferramentas de desenvolvimento.
