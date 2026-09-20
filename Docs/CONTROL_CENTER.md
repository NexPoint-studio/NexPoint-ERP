# NexPoint ERP Control Center V1

O Control Center é o painel privado da equipe NexPoint para acompanhar empresas,
instalações locais, chamados, saúde, riscos, incidentes e versões do ERP. Ele é
um aplicativo separado da interface usada pelo cliente e roda somente em
`127.0.0.1:8770`.

Esta versão usa persistência SQLite local. Ela não envia dados para Supabase ou
outra cloud, não habilita billing e não altera registros operacionais ou
financeiros dos ERPs acompanhados.

## Isolamento e acesso

O Control Center possui autenticação e cookie de sessão próprios. A conta
inicial tem papel interno `platform_admin`; o proprietário e os demais usuários
do ERP não são contas válidas no painel. As rotas administrativas exigem essa
sessão interna, token CSRF por sessão e origem local. Ao trocar a senha interna,
sessões emitidas com o hash anterior deixam de ser aceitas.

O banco padrão é `data/control_center.sqlite3`, separado de
`data/erp.sqlite3` e `data/demo_2_anos.sqlite3`. Arquivos SQLite sob `data/` são
ignorados pelo Git. A configuração recusa o banco operacional do ERP e caminhos
fora de `data/`.

O ERP operacional e o launcher do painel resolvem
`CONTROL_CENTER_DATABASE_PATH` pelo mesmo contrato. Assim, chamados e telemetria
aparecem no painel configurado sem criar um segundo sidecar silencioso. Uma
falha nesse armazenamento continua isolada e não impede a inicialização do ERP.

## Configuração

Copie `.env.example` para `.env.local` e substitua todos os marcadores usados
pela instalação. Para o Control Center, configure:

```dotenv
CONTROL_CENTER_HOST=127.0.0.1
CONTROL_CENTER_PORT=8770
CONTROL_CENTER_DATABASE_PATH=data/control_center.sqlite3
CONTROL_CENTER_SESSION_SECRET=SUBSTITUA_POR_UM_SEGREDO_LOCAL_COM_32_CARACTERES
CONTROL_CENTER_ADMIN_USERNAME=SUBSTITUA_POR_UM_USUARIO_INTERNO_NEXPOINT
CONTROL_CENTER_ADMIN_PASSWORD=SUBSTITUA_POR_UMA_SENHA_INTERNA_COM_12_CARACTERES
CONTROL_CENTER_SEED_DEMO=0
```

Não reutilize o login, a senha ou o segredo de sessão do ERP. O arquivo
`.env.local` permanece fora do Git.

Inicie o painel com:

```powershell
Set-Location 'D:\NexStudio\sistema ERP'
.\.venv\Scripts\python.exe run_control_center.py
```

O launcher verifica a porta, inicia o servidor local e abre uma janela dedicada
em `http://127.0.0.1:8770`.

## Seed fictícia opcional

`CONTROL_CENTER_SEED_DEMO=1` carrega, de forma idempotente, as empresas Alfa,
Beta e Gama com instalações, chamados, snapshots de saúde, fingerprints, riscos,
incidentes e versões de demonstração. Todos esses registros são identificados
como fictícios. A seed não cria uma senha de demonstração e a conta configurada
continua sendo a única credencial inicial.

Use `CONTROL_CENTER_SEED_DEMO=0` quando não quiser esses registros. O ERP real se
registra no repositório local pelo contrato próprio; essa operação não depende da
seed.

## Áreas do painel

- **Visão geral:** totais de empresas e instalações, chamados abertos ou em
  andamento, riscos altos ou críticos e incidentes recentes.
- **Empresas:** lista filtrável e detalhe com instalação, versão, saúde,
  chamados e riscos relacionados.
- **Chamados:** fila por empresa, status, prioridade e categoria; o detalhe
  permite alterar status, registrar notas internas e, somente no contrato de
  recuperação administrativa, emitir uma autorização temporária restrita.
- **Saúde:** snapshots técnicos por empresa e instalação.
- **Riscos:** nível, score, módulo, evidências e fingerprint sanitizado.
- **Incidentes:** acompanhamento, mudança de status e notas internas; um
  incidente pode nascer de um risco ou chamado conhecido.
- **Versões:** distribuição das versões instaladas e respectiva saúde.
- **Nexa:** apoio interno contextual e somente leitura para um chamado
  autorizado.
- **Sistema:** informações da execução local e do armazenamento separado.

O painel não oferece comandos de Caixa, pagamento, cancelamento, alteração de
cadastros do cliente ou qualquer outra ação operacional no ERP.

## Contrato de domínio e persistência

O contrato `ControlCenterRepository` separa o domínio da implementação de
armazenamento. O V1 usa `LocalControlCenterRepository`; uma implementação futura
poderá usar Supabase sem mudar os fluxos que consomem o contrato.

Os objetos centrais são `Tenant`, `ErpInstallation`, `HealthSnapshot`,
`SupportTicket`, `RiskSummary`, `Incident` e `PlatformUser`. IDs são opacos e os
registros ligados a uma empresa sempre usam `tenant_id`. As consultas do ERP
cliente são limitadas ao tenant derivado pelo backend; o navegador não escolhe
tenant, autor, status ou contexto técnico.

O `SupportTicket` guarda protocolo, tenant, instalação exata, autor pseudonimizado, assunto,
categoria, descrição, status, prioridade, datas, módulo, tela, versão, contexto
técnico sanitizado, fingerprint, correlation ID, risco relacionado e diagnóstico
opcional da Nexa. O histórico registra criação e transições de status. Notas
internas do Control Center não são expostas na listagem do cliente.

## Fluxo da Central de Suporte do ERP

Na interface do cliente, **Administração > Suporte** oferece abertura e histórico
de chamados. Assunto, categoria e descrição são obrigatórios; a prioridade pode
ser normal ou alta. O cliente não marca um chamado como crítico e não altera seu
status administrativo.

Ao criar o chamado, o backend deriva empresa, instalação e usuário da sessão e
acrescenta, quando disponíveis:

- módulo, tela, versão, build, ambiente e horário;
- identificador pseudonimizado do usuário;
- correlation ID;
- estado de saúde, riscos e até cinco eventos diagnósticos recentes;
- fingerprint diagnóstica relacionada.

A sanitização remove segredos, tokens, cookies, chaves, cabeçalhos sensíveis,
e-mails, documentos, telefone, CEP, endereço brasileiro reconhecível, valores
monetários e stacks brutos. A criação é
local e não depende da disponibilidade da Nexa.

O acesso temporário de suporte existente continua preservado no backend para um
fluxo futuro autorizado, mas deixou de ser a ação principal da interface do
cliente.

## Recuperação do cadeado administrativo

Chamados de categoria `admin_access_recovery` são identificados no painel e
mostram empresa, instalação, solicitante pseudonimizado, data, `correlation_id`
e o contexto de segurança sanitizado enviado pelo ERP. Isso inclui somente
estado do cadeado, tentativas recentes e lockout. A timeline registra pedido,
autorização e consumo, sem senha, hash, código ou token.

Somente um `platform_admin` ativo pode autorizar a redefinição. O chamado precisa
estar ativo, pertencer à categoria correta e possuir instalação vinculada; o
formulário exige CSRF e a frase `AUTORIZAR <protocolo>`. A autorização resultante:

- vale por 15 minutos;
- pertence ao chamado, tenant, instalação e solicitante exatos;
- revoga uma autorização ativa anterior do mesmo chamado;
- aceita um único consumo;
- armazena somente o hash `scrypt` de um verificador aleatório interno;
- nunca mostra ou entrega esse verificador ao cliente.

O Control Center não recebe a senha antiga nem a nova. Ele apenas autoriza o
Proprietário autenticado a iniciar a redefinição na instalação vinculada. O ERP
detecta a autorização pelo vínculo confiável e define a nova senha localmente;
papel, permissões, login e acesso técnico não são modificados. Autorização
expirada, revogada, consumida ou pertencente a outro
tenant/instalação/solicitante falha fechado.

## Telemetria local

O adaptador ERP → Control Center publica snapshots limitados no banco separado.
Ele usa nome exibido da empresa, IDs opacos, versão, build, ambiente, saúde,
latência, contagem de erros, tentativas, fingerprints e riscos produzidos pelo
monitor de diagnóstico. Não consulta clientes, notas, pagamentos, Caixa ou
outros registros comerciais.

Os identificadores do tenant e da instalação nascem de uma identidade opaca
persistida no próprio SQLite operacional. Por isso permanecem estáveis quando o
segredo de sessão é rotacionado e acompanham backup e restauração. Restaurar o
banco de outra instalação no mesmo caminho seleciona a identidade transportada
por esse banco e não expõe chamados ou riscos do ocupante anterior. No primeiro
upgrade, o sidecar pode adotar uma única vez os IDs locais baseados no caminho,
preservando os registros já criados antes desse contrato.

Somente uma janela com eventos realmente observados grava snapshot de saúde e
reconcilia riscos. Um processo recém-iniciado com monitor vazio registra a
instalação como desconhecida, ou preserva a última saúde conhecida, sem marcar
riscos ativos como mitigados. Em uma janela observada, riscos que deixam de
aparecer são marcados como mitigados; um fingerprint que reapareça volta a ser
aberto.

A publicação é periódica e de melhor esforço. Uma falha no Control Center nunca
interrompe o trabalho local do ERP.

## Nexa interna

A área Nexa do Control Center constrói o contexto no servidor a partir de um
chamado autorizado. Ela pode receber empresa, instalação, versão, saúde,
fingerprints, riscos e contexto técnico já sanitizado. O escopo é somente leitura
e não oferece SQL arbitrário nem acesso direto aos bancos.

O papel `platform_admin` possui escopo global. O papel
`nexpoint_control_admin` precisa de grants persistidos por tenant; sem grants,
dashboard, empresas, chamados, saúde, riscos, incidentes, Log Explorer,
detalhes, export, fingerprints e investigação Nexa falham fechados.
O bootstrap do administrador principal preserva as demais contas internas e seus
escopos.

A Tool `search_erp_help` recebe apenas trechos limitados, sanitizados e versionados
da Knowledge oficial do ERP, priorizados pelo módulo do chamado e pela pergunta.
Esse conteúdo é separado da Knowledge pública institucional da NexPoint.

A ponte usa o mesmo contrato HMAC da integração local ERP ↔ Nexa. Se a Nexa não
estiver configurada ou ficar indisponível, o painel mostra a falha sem impedir a
consulta dos dados locais ou a gestão da fila. Detalhes da assinatura, Tools e
limites de Web estão em [INTEGRACAO_NEXA.md](INTEGRACAO_NEXA.md).

## Limites do V1

- armazenamento apenas local e compartilhado somente pelas aplicações desta
  instalação;
- um administrador interno inicial configurado pelo ambiente;
- nenhuma sincronização com Supabase ou cloud;
- nenhum chat completo entre cliente e atendente;
- nenhuma execução remota ou escrita no banco operacional do ERP; a autorização
  de reset apenas emite uma prova temporária que o Proprietário consome no ERP;
- nenhuma ação financeira, operacional ou de billing.

A migração futura deverá implementar o mesmo `ControlCenterRepository`, manter o
isolamento por tenant e transportar somente contratos sanitizados. Ela exige uma
tarefa própria de identidade, RLS, auditoria e sincronização; não faz parte do V1
local.
