# Pós-piloto: catálogo e recebimentos

## Escopo e baseline

Em 14/10/2026, o checkout estava limpo em `8a38978266b40c44b809c533a2eb497bccd80213`.
Antes das edições foi criado e verificado `artifacts/post-pilot-20261014/baseline.bundle`,
SHA-256 `3a74dc4006901e1b8b7d3f819ec20fdc664a5e7333f16a326cb2c30d235155b5`.
É checkpoint de código, não backup de dados de cliente. Nenhum dado operacional
da Nil lav foi usado nos testes desta fase. Deploy/push não fazem parte deste checkpoint.

A consulta PROD somente leitura de 14/10 encontrou `nil-lav-pc-01` ainda no
tenant `nexpoint`, canal `internal`, heartbeat de 13/10/2026 às 22:05 UTC.
Isso não comprova o tenant independente desejado. Preservamos esse estado;
antes da promoção será necessário conferir o vínculo e planejar qualquer
reassociação com backup e autorização, sem interromper o piloto.

## Auditoria inicial

O ERP continua sem login operacional ou papéis de funcionários. Administração
permanece visível e usa Admin Lock opcional. Cada SQLite operacional pertence a
uma empresa/instalação. Autenticação e isolamento no cloud usam a credencial
técnica e RLS. Não se introduz catálogo compartilhado entre empresas.

O catálogo possuía `services.category_id` único por serviço e histórico de preços
padrão em `service_prices`. Notas já congelavam descrição, unidade e preço.
Pagamentos já possuíam regras percentual/fixo, snapshot bruto/taxa/líquido,
idempotência e ledger para recebimentos parciais. Esses contratos são reutilizados.

Suporte já tem criação transacional em Outbox e gestão interna no Control Center;
conversa descendente, anexos e reabertura pelo cliente precisam da Fase 2. Notas
internas do painel não equivalem a respostas públicas. Nexa possui tools restritas
e contexto versionado, mas seus textos precisam acompanhar os novos fluxos.

O updater existente autentica respostas por HMAC, verifica tamanho/hash e tem
política de assinatura, helper separado e recuperação. A build anterior era
`CODE_SIGNING_PENDING`: não houve comprovação de assinatura digital Authenticode.
Download automático, retomada e distribuição gradual são itens da Fase 3.

Backups locais verificados e restauração controlada já existem. Uma cópia gravável
pelo mesmo processo não protege sozinha contra ransomware. Cópia independente,
retenção e restauração isolada permanecem no escopo da Fase 4.

## Catálogo e compatibilidade

A migration aditiva `0017_catalog_associations` copia os vínculos atuais para
`service_category_links`, com PK `(service_id, category_id)`, FKs restritivas e
preço opcional em centavos inteiros. Essa unicidade ocorre dentro do banco da
empresa; IDs de outro SQLite nunca são resolvidos nessa sessão. Não há novo
endpoint cloud de catálogo nem relaxamento de RLS.

`services.category_id` permanece como projeção de compatibilidade do primeiro
vínculo; operações novas consultam a associação. Cada vínculo pode usar preço
próprio ou o padrão vigente do serviço. O padrão pode ficar ausente somente
quando todas as categorias selecionadas têm preço. Categorias inativas não
podem ser acrescentadas a novos vínculos e não aparecem em novas notas.

Notas novas selecionam o par serviço/categoria. Payload antigo pode omitir
categoria somente quando não há ambiguidade. Notas existentes mantêm descrição,
categoria e preço do snapshot, inclusive após inativação ou remoção do vínculo.
O novo `service_category_id` é snapshot sem FK ao catálogo mutável; notas antigas
ficam com NULL nesse campo, sem adivinhar a categoria a partir do catálogo atual.

Não há exclusão de categorias na UI; usa-se inativação. As FKs da associação
impedem apagar categoria ainda vinculada. A unicidade de nomes de categorias e
códigos de serviços foi mantida, assim como o aviso de serviço parecido.

## Validação e promoção

### Integração de recebimentos

`0018_payment_customer_surcharge` adiciona o acréscimo ao cliente com valor
inicial zero, sem recalcular pagamentos anteriores. O principal quita o saldo;
o bruto soma principal e acréscimo; o líquido desconta o custo da operadora.
Percentual e valor fixo ficam congelados no pagamento. Ajustes exigem motivo
e geram auditoria; a prévia usa o cálculo do servidor.

A quitação direta de saldo devedor também consulta a configuração vigente.
Anteriormente esse caminho sempre lançava taxa zero. Agora usa o mesmo cálculo
Decimal dos pagamentos, registra bruto/taxa/líquido no Caixa e registra a regra,
percentual e fixo aplicados na auditoria. A repetição da mesma confirmação
retorna o recibo existente sem recalcular sua taxa. O contrato desse formulário
continua restrito a formas sem terminal; ajustes e acréscimos ficam no fluxo de
pagamento da Nota, inclusive para saldos anteriores vinculados.

### Evidências parciais de 14/10/2026

- 27 testes focados de pagamentos e evolução financeira passaram, incluindo
  regressão da taxa na quitação direta e preservação após mudar configuração.
- 12 testes de migrations das fases quatro e financeiro offline passaram;
  contagem esperada atualizada de 16 para 18, mantendo verificações de dados,
  integridade e chaves estrangeiras.
- 14 telas visitadas em larguras 1366 e 390: 28 respostas HTTP 200, sem
  transbordamento horizontal da página nem erros de JavaScript. Capturas locais
  em `artifacts/postpilot-catalog-visual`; isso não comprova todos os modais,
  estados preenchidos ou o notebook real.
- Capturas do formulário de catálogo e pagamentos conferidas. Estado vazio da
  tabela de taxas substituído por texto que quebra linha em telas estreitas.
- Prévia executada no navegador por `fetch` real ao servidor local: principal
  1500, acréscimo 200, bruto 1700, taxa 101 e líquido 1599 centavos. Sem overflow
  em 1366/390. O CSS da grade antes sobrepunha o atributo `hidden`, mostrando
  a prévia vazia; seletor específico corrigido e resultado renderizado conferido.
- Fixtures históricas das migrations 0015 e fase três foram ajustadas para
  remover colunas/entradas futuras de forma coerente. Não foi necessário
  relaxar invariantes de migration. Regressões: 1 e 7 testes passaram.
- 19 testes de configuração de pagamentos e catálogo passaram após integração.
- Secret scan passou em 505 arquivos candidatos; `git diff --check` passou.
- Suíte completa: 1283 passaram e 7 falharam em expectativas/fixtures antigas
  de schema (770,28 segundos). Os sete casos foram corrigidos e reexecutados:
  7/7 passaram. Portanto, 1290 casos validados no conjunto execução + reexecução;
  não se apresenta a primeira execução como totalmente verde. Evidências:
  `artifacts/post-pilot-20261014/full-suite.xml`, `corrected-suite.xml` e log.
- Backups/restauração, migrations com rollback, `integrity_check` e
  `foreign_key_check` cobertos pelos testes locais. Não equivalem à restauração
  do notebook ou à homologação em PROD.

Fase 1 validada localmente, pronta para checkpoint. Fases 2, 3 e 4 permanecem
pendentes; a evolução completa não está pronta para publicação. Não promover a
instalação da cliente com base somente nos testes locais. O schema novo exige
backup verificado e binário compatível; downgrade simples do executável não é
rollback de schema. Nenhuma alteração PROD, push ou deploy nesta fase.
