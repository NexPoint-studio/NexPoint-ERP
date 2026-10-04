# Instalação e ativação Windows

Contrato v2, em validação local. A liberação depende dos gates de
`SIMPLIFICACAO_ENTREGA.md`; a documentação não confirma publicação das novas
migrations, função Edge ou instalador.

O instalador prepara o programa em `%LOCALAPPDATA%\Programs\NexPoint ERP` e os
dados ficam em `%LOCALAPPDATA%\NexPoint\ERP`. Os dados são preservados após
desinstalação. Python e Inno Setup não são necessários no computador de destino.

## Primeiro acesso

1. O pacote contém um perfil público `activation-profile.json`, com
   `schema_version: 2`, `tenant_key`, `installation_key` e `company_name`.
   Não contém conta, senha nem credencial de instalação.
2. A tela **Ativar este computador** recebe somente um código emitido pela
   NexPoint. O formulário exige origem loopback exata, CSRF e corpo limitado.
3. O desktop gera request ID e chave aleatória e grava o estado protegido por
   DPAPI CurrentUser antes da comunicação remota.
4. A Edge `erp-activate` recebe código transitório, escopo, request ID, key ID e
   digest SHA-256 da chave. O código é transformado em digest antes do RPC.
   A chamada usa HTTPS com certificado validado, sem redirects.
5. `np_consume_installation_code` confere código, empresa, instalação, validade
   e limites de tentativas. A transação cria instalação, credencial e recibo.
6. A resposta contém somente associação e metadados. Não retorna hash de senha
   humana nem capacidade administrativa do backend.
7. O desktop registra o recibo protegido, prepara SQLite vazio com migrations e
   uma identidade técnica não autenticável, verifica schema/integridade/FKs e
   publica sem substituir arquivos existentes. Não cria catálogo ou dados fictícios.
8. O ERP abre diretamente. A abertura seguinte usa banco e credencial locais,
   inclusive offline, sem exigir código novamente.

## Emissão e revogação

Em `/ativacoes` no Control Center, somente `platform_admin` ativo pode emitir ou
revogar. O formulário exige CSRF. A emissão mostra uma única vez um código de
256 bits; a listagem posterior apresenta somente metadados e a ação de revogar.
Respostas não podem ser armazenadas em cache.

O servidor persiste apenas o digest do código. A validade padrão é 24 horas,
com limite de sete dias. Uma nova emissão para a mesma instalação ainda não
ativada revoga a anterior. Uma instalação já existente não é reprovisionada por
esse caminho. Emissão e revogação deixam auditoria sem código ou digest exposto.

Consumo é único. Retry remoto aceita somente o mesmo request, key ID e digest,
por no máximo um dia após consumo. Outra chave/máquina não recebe a credencial.
Código revogado, expirado ou associação indisponível impede autorização.

O código deve chegar separadamente ao responsável pelo computador. Não deve
entrar no pacote, Git, URL, documentação ou chamado. O perfil público sozinho
não autoriza ativação.

## Interrupções e preservação

`credentials/activation.dpapi` guarda request, chave local e recibo, sem código
de ativação. Um lock de arquivo impede provisionamentos concorrentes.
Se houver recibo autorizado, a finalização local pode retomar sem outra chamada
remota. Se a resposta se perder, a repetição reutiliza request e chave originais.

Banco existente precisa corresponder ao request e à empresa esperados. Arquivos
corrompidos, links, escopo diferente ou DPAPI de outro Windows são recusados,
sem reset automático. Credenciais completas anteriores continuam usando a
abertura local; os registros históricos são preservados.

As RPCs antigas de ativação por conta/senha ficam sem EXECUTE para service_role
nas novas migrations. O script `prepare_activation_grant.py` está desativado.
A credencial administrativa cloud continua exclusivamente no servidor/cofre.

## Build e aceite

O build oficial exige checkout limpo, testes aprovados, manifesto e inventário
de hashes. O scanner examina bundle e instalador. O atualizador é um executável
separado, verificado antes de integrar o pacote.

Antes de entregar, validar Setup, ativação, DPAPI, segundo start, funcionamento
offline, Empresa, Admin Lock, recuperação, sync, heartbeat e atualização em
Windows QA limpo. Instalar em outro diretório no mesmo Windows não isola HKCU,
Menu Iniciar ou dados do perfil e não substitui esse teste.

Se a versão anterior não tiver updater, esta entrega pode ser uma última
atualização manual, sempre com backup e preservação dos dados. A validação do
notebook real e sua associação no painel continuam sendo aceites próprios.
