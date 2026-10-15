# Pós-piloto — revisão de segurança e confiabilidade

Data: 14/10/2026. Revisão defensiva das alterações de suporte e updater no
working tree. Nenhuma operação desta auditoria foi realizada em PROD.

## Escopo e resultado

Não foram confirmados achados CRITICAL ou HIGH neste recorte. Isso não constitui
declaração de ausência de vulnerabilidades no produto inteiro. A suíte final,
Windows A→B e a promoção possuem gates próprios.

| Superfície | Evidência | Resultado |
| --- | --- | --- |
| HMAC e contexto da resposta | Edge e inspeção do cliente: domínio, tenant, installation, nonce, request ID, timestamp e corpo | PASS |
| Replay e idempotência | PostgreSQL isolado; retry não duplica respostas e colisão de operação é rejeitada | PASS |
| Isolamento | Tenant diferente e installation diferente dentro do mesmo tenant não acessam o chamado | PASS |
| Notas internas | Projeção SQL pública e allowlist independente no Edge; regressão com canário interno | PASS |
| Anexos | Ownership, hash, nome, MIME/cabeçalho, 1 MiB, três por chamado, download privado | PASS |
| Reassociação | FK composta com atualização em cascata preserva anexos junto ao chamado | PASS |
| Histórico completo | Paginação: cenário de 201 mensagens e 102 chamados | PASS |
| Contato opcional | Consentimento e formato explícitos; removido do contexto da Nexa | PASS na projeção inspecionada |
| CSRF/autorização | CC exige sessão, CSRF e escopo tenant; ERP preserva Origin/session guard e Admin Lock | Inspeção concluída |
| Erros públicos | Mensagens/códigos genéricos; sem corpo upstream, credenciais ou diagnóstico cru | Inspeção concluída |
| Updater | Download automático não instala; confirmação de instalação e verificação do artefato permanecem | Inspeção concluída |

Os testes específicos cloud totalizaram 21 casos Edge, typecheck Deno dos dois
endpoints e duas suítes SQL. A regressão `test_prod_control_center.py` passou em
36 casos. Esses números não substituem o resultado final integrado.

## Achados e triagem

### Fechamento de 15/10/2026

Nenhum CRITICAL/HIGH confirmado aberto na triagem consolidada. Alertas históricos
de scanners não resolvidos continuam limitações de cobertura, não findings
automaticamente confirmados nem controles certificados. O escopo tenant dos
três RPCs de suporte foi reforçado na migration
`20261015010000_support_admin_tenant_scope.sql`: 24 combinações role/tenant,
revogação, ausência de mutação nas recusas e grants privados passaram em QA.
Conversa/anexos SQL e 37 testes Python de Control Center/recuperação passaram.

Quota acumulada de anexos continua limitação documentada; não foi introduzida
política arbitrária de retenção/exclusão de dados. Histórico Git SD-003 não foi
reescrito; prevenção permanece vigente. Advisory setuptools segue documentado
em RELEASE_1.1.2.md, sem upgrade major não validado. Windows QA não foi repetido.

### MEDIUM — janela entre anexo local e outbox

Foi identificada uma janela de interrupção: o sidecar confirma os bytes antes
da transação independente da outbox operacional. Uma queda entre os commits
pode deixar a imagem local sem referência durável de envio. Os bytes não são
perdidos, mas o envio não pode depender de o usuário repetir a ação.

Corrigido: a reconciliação limitada por instalação recupera referências ausentes
antes do ciclo de sync. Recibos no sidecar evitam reenvio após a retenção da outbox.
`tests/test_support_attachment_recovery.py` passou: simula bytes confirmados sem
commit operacional, reinicia o reconciliador, confirma envio único e preservação
após remoção da referência já entregue. A suíte integrada permanece gate separado.

### MEDIUM — advisory em dependência exclusiva de build

`pip-audit` identificou `setuptools==80.9.0` no lock de build como afetado por
`CVE-2026-59890` / `GHSA-h35f-9h28-mq5c`. O problema envolve exclusões de arquivos
em sdist e normalização Unicode, especialmente no macOS. A fonte do mantenedor
lista versões até 82.0.1 e não declara uma versão corrigida; a base consultada
pelo scanner informa 83.0.0. Essa divergência permanece registrada, sem afirmar
que um upgrade foi validado. [Advisory do mantenedor](https://github.com/pypa/setuptools/security/advisories/GHSA-h35f-9h28-mq5c).

Aplicabilidade: o build auditado é Windows/instalador, sem publicação de sdist
macOS. O release guard usa inventário permitido e secret scan. Não foi
demonstrada exposição no artefato Windows. Mantida como pendência documentada
de supply chain; nenhum upgrade major de ferramenta foi feito nesta auditoria.

### MEDIUM — quota agregada de anexos

Há limite por arquivo, por chamado e orçamento compartilhado de 60 requisições
por minuto por installation. Não há quota acumulada por tenant nem política
de retenção automática. Um cliente autenticado que crie muitos chamados pode
consumir o armazenamento disponível. Limitação confirmada de configuração;
nenhum teste destrutivo de esgotamento foi executado.

### LOW — defesa adicional nos RPCs internos

Os RPCs administrativos de suporte verificam usuário interno ativo e papel;
o escopo por empresa de `nexpoint_control_admin` é imposto na rota do Control
Center. Não houve bypass HTTP confirmado. Repetir essa restrição no RPC seria
defesa adicional; os RPCs não são concedidos a `anon` ou `authenticated` e o
service role continua restrito ao servidor.

### LOW — limites assumidos

- Imagens passam por validação de cabeçalhos/estrutura, não por antivírus ou
  decoder completo. São baixadas com `attachment`, `nosniff` e CSP restritiva.
- `CODE_SIGNING_PENDING` é explícito: manifest autenticado e SHA-256 não são
  apresentados como certificado Authenticode.
- Pausar distribuição impede elegibilidade futura; não revoga automaticamente
  instaladores já baixados. A interface informa essa condição.

## Scanners e falsos positivos

`Bandit`: execução concluída, 289 alertas — 200 LOW, 89 MEDIUM, nenhum HIGH.
Alertas não equivalem a vulnerabilidades confirmadas. A triagem desta rodada
priorizou as alterações novas; não reclassificou todos os alertas históricos.

Amostras revisadas: `B105` sobre o estado textual `PASS` não é segredo;
`B608` em consultas revisadas usa fragmentos controlados e valores parametrizados;
`B110` de telemetria isola falhas de observabilidade sem aprovar acesso indevido.
A hipótese de falha de validação base64 no limite de 1 MiB não se reproduziu.

`pip-audit`: 28 dependências runtime sem advisory; sete dependências de build
com um advisory distinto, reportado duas vezes por aliases. Consulta somente
a metadados de pacotes/advisories, sem envio de dados do cliente.

## Evidências e limites

Artefatos locais ignorados pelo Git:

- `artifacts/post-pilot-20261014/support-cloud/security-bounded-review.json`
- `artifacts/post-pilot-20261014/support-cloud/same-tenant-isolation.log`
- `artifacts/security/remediation/scanners/run-9578faa430/results.json`
- `artifacts/security/remediation/scanners/run-d66fa4fa99/results.json`

Os scanners exportaram snapshots do código candidato por volta de 16:31 UTC.
Mudanças posteriores requerem os gates finais do coordenador. Não foram
executados nesta revisão: auditoria invasiva PROD, esgotamento prolongado de
recursos, certificação física Windows, nova atualização A→B ou revalidação
integral de todos os achados das auditorias antigas.
