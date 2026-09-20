# Integração local ERP ↔ Nexa

O ERP é FastAPI/Jinja2/SQLite local. A Nexa é uma Edge Function separada. O painel
Nexa aparece nas telas autenticadas e envia mensagens ao backend do ERP, nunca
diretamente do navegador à Nexa. O ERP continua operacional quando a Nexa cai.

## Configuração local

Em arquivos ignorados pelo Git, configure o mesmo segredo aleatório de pelo
menos 32 caracteres:

- ERP `.env.local`: `NEXA_ERP_BRIDGE_SECRET` e, se necessário,
  `NEXA_ERP_BRIDGE_URL=http://127.0.0.1:54421/functions/v1/erp-chat`;
- Nexa `supabase/functions/.env`: `NEXA_ERP_BRIDGE_SECRET`.

Execute a Nexa local com Edge Functions servidas e abra o ERP normalmente. Não
coloque esse segredo, credenciais de provider ou chave Supabase no frontend.
Nenhum serviço pago ou billing é habilitado pela ponte.

## Diagnóstico de disponibilidade local

O estado exibido pelo ERP diferencia uma ponte configurada de um serviço
realmente alcançável. Para o endpoint local, o backend faz uma verificação TCP
curta, com cache, sem enviar mensagem nem contexto à função. A Nexa só é marcada
como disponível quando a porta local aceita a conexão. Endereços remotos podem
ser reconhecidos como configurados, mas não são consultados por essa verificação
de contexto.

Na investigação de setembro de 2026, o segredo local estava configurado e a URL
padrão apontava corretamente para
`http://127.0.0.1:54421/functions/v1/erp-chat`. A porta `54421` não estava
ouvindo e o stack local não estava iniciado. Portanto, a indisponibilidade
observada foi causada pelo serviço local parado. Não houve evidência de falha no
contrato HMAC, nas assinaturas ou na sanitização da ponte.

Esse diagnóstico não autoriza iniciar Cloud, produção ou serviços externos. O
ERP não executa um LLM offline: sem o stack Nexa e sem conectividade adequada, a
assistente permanece indisponível enquanto o ERP, o Doctor, os diagnósticos
determinísticos e a abertura local de chamados continuam funcionando.

## Contrato e limites

`POST /nexa/chat` exige a sessão ERP. O navegador fornece somente mensagem e
rota como pista; o backend valida a rota, recarrega o ator e suas permissões no
SQLite e cria um contexto sem nome, e-mail, documentos, valores ou IDs reais. O
usuário é identificado por um HMAC pseudônimo. A requisição à Nexa leva
`app=erp`, contexto e snapshots de Tools permitidas, assinados por HMAC-SHA256
com timestamp e nonce. Depois de validar o HMAC, a Nexa grava somente hashes do
identificador da ponte e do nonce por 121 segundos em uma tabela privada e usa
uma função SQL atômica para aceitar a primeira chamada. Assim, o bloqueio de
replay sobrevive a reinícios e é compartilhado por todas as instâncias. A
função Edge falha fechada com 503 se esse armazenamento estiver indisponível;
isso deixa a Nexa offline sem impedir qualquer operação do ERP. A Nexa rejeita
assinatura inválida, nonce repetido, timestamp vencido, contexto grande e Tool
desconhecida. A service role é usada apenas no processo servidor para chamar a
função restrita e nunca chega ao ERP ou ao navegador.

A resposta também é autenticada. A Nexa assina os bytes exatos com separação de
domínio e o challenge original (`timestamp`, `nonce` e `request_id`); o ERP
confere `X-Nexa-Signature`, `X-Request-ID` e o `request_id` do JSON antes de usar
o conteúdo. Uma resposta ausente, alterada ou reapresentada em outra chamada é
tratada como indisponibilidade da Nexa e não interrompe o ERP.

Tools V1 são apenas de leitura: contexto ERP, usuário atual, módulo, saúde,
eventos recentes do próprio usuário, permissões, ajuda e preflight. O adaptador
usa autorizações efetivas do ERP e dados já sanitizados. Não há SQL arbitrário,
service role, acesso direto ao banco, Tool financeira ou ação de escrita.

No contexto **Esqueci a senha da Administração**, a Tool
`get_admin_lock_status` segue o mesmo limite somente leitura. Ela pode informar
se o cadeado está configurado, tentativas, lockout, último evento de recuperação
e se há solicitação aguardando sincronização. O snapshot não inclui senha, hash,
código de recuperação ou token de reset.

O monitor local mantém no máximo 1.000 eventos por 24 horas. Avisos e falhas
sanitizados persistem no SQLite, são agregados por fingerprint em janela curta
e sobrevivem ao reinício; sondagens informativas permanecem somente na janela
volátil. Ele captura falhas HTTP relevantes, negações e latência alta e calcula
risco a partir de evidências. Alertas exigem score mínimo 75 e cooldown de 15
minutos. A Nexa pode explicar o risco, mas não prediz falha certa.

O histórico curto do chat fica somente na memória do processo ERP, por sessão,
por uma hora. A ponte não grava conversas na Nexa nem cria usuário Supabase. Uma
reinicialização perde esse histórico conversacional; telemetria relevante e a
proteção de replay permanecem persistentes em seus respectivos armazenamentos.

Consulta Web no fluxo ERP só pode usar uma expressão pública fixa derivada de
uma biblioteca conhecida; mensagem bruta, clientes e dados financeiros nunca
servem como query. Se não houver tema público seguro, Web não é ativada.

## Central de Suporte e Control Center

A abertura de chamado na Central de Suporte reutiliza os snapshots e a
sanitização desta integração, mas persiste o chamado localmente mesmo quando a
Nexa está indisponível. Conversar com a Nexa antes de abrir o chamado é opcional.

Para recuperação do cadeado, a Nexa orienta o usuário a usar **Solicitar
recuperação à NexPoint** na tela de acesso à Administração. Ela não inicia o
chamado, pede ou infere senha, gera autorização, consome verificador, redefine a
credencial ou eleva o papel do usuário. A nova senha é definida somente pelo
Proprietário no ERP depois da autorização. Sem a Nexa, a fila local do chamado
continua funcionando.

No Control Center, a Nexa é uma ferramenta interna e somente leitura. O backend
monta o contexto a partir de um chamado autorizado e pode incluir tenant,
instalação, versão, saúde, riscos, fingerprints e diagnóstico sanitizado. O
navegador não fornece esse contexto, não escolhe outro tenant e não recebe acesso
ao SQLite. A ponte não oferece SQL arbitrário nem ações operacionais ou
financeiras. Consulte [CONTROL_CENTER.md](CONTROL_CENTER.md) para configuração,
persistência e limites do painel local.

## Doctor

```powershell
.\.venv\Scripts\python.exe scripts\erp_doctor.py
```

O comando lê schema, tabelas e integridade SQLite sem migrar ou alterar dados.
Retorna PASS (0), WARN (1) ou FAIL (2) com motivos objetivos. Ele avisa se a
ponte estiver desconfigurada ou um arquivo crítico tiver mudado sem a suíte
completa. Também procura material de credencial não sanitizado nos registros
inspecionados, incluindo senha, hash, código de recuperação e token de reset,
sem imprimir o valor encontrado. O diagnóstico de release complementa, não
substitui, `pytest`.
