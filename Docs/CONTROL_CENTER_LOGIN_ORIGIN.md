# Correção de origem do login no Render

## Causa confirmada

O GET público de `/login` no commit `ab7519d` devolvia
`Referrer-Policy: no-referrer`. No Edge/Chromium, o POST do formulário enviava
`Origin: null` e nenhum `Referer`. A comparação com
`https://nexpoint-erp-control-center.onrender.com` recusava a requisição antes
da autenticação. O POST observado a partir da página pública foi interceptado
antes do envio, com dados fictícios.

Esse comportamento corresponde ao algoritmo
[append a request Origin header do Fetch](https://fetch.spec.whatwg.org/#append-a-request-origin-header).
Não foi encontrada evidência de erro nas variáveis de origem/host nem no proxy.

## Correção e proteções

- `Referrer-Policy: same-origin` permite o envio da origem legítima no formulário
  e mantém o Referer ausente em navegações para outra origem.
- A comparação usa scheme, hostname e porta normalizados, com a origem pública
  configurada como autoridade. `X-Forwarded-Host` não substitui essa autoridade.
- Origin inválido, vazio ou `null` é recusado mesmo com Referer válido. Cabeçalhos
  duplicados, URLs com userinfo, hosts externos, portas inesperadas e entradas
  malformadas são rejeitados. Referer é alternativa apenas quando Origin não existe.
- Em implantação com origem pública, a ausência de ambos também é recusada.
- CSRF de sessão, rotação após login e cookies Secure/HttpOnly/SameSite=Strict
  são preservados. Recusas de origem/CSRF no login mostram mensagem sanitizada
  na própria tela, mantendo HTTP 403 e sem autenticar.

## Evidências de validação

- 152 testes de Control Center, PROD e recuperação administrativa passaram.
- O teste específico simula HTTP interno atrás do proxy, Host público e
  X-Forwarded-Proto/Host, verificando sessão e rejeições de segurança.
- Edge real com HTTPS, DNS mapeado para backend local e banco temporário isolado:
  política antiga → Origin:null/403; política corrigida com senha fictícia
  incorreta → 401; credencial válida do teste → 303, painel 200 e sessão persistida.
- Blueprint validado pelo schema oficial Render; `render.yaml` não foi alterado.

Nenhuma credencial real, dado Supabase, senha administrativa, DPAPI, provisionamento
ERP ou build desktop foi alterado. Não há diagnóstico de credenciais em logs.

## Aceite em produção

**Concluído por confirmação do proprietário em 29/09/2026:** Control Center
publicado, login PROD funcional, página autenticada aberta e acesso mobile
utilizável. O encerramento consta em [PROD_BASELINE.md](PROD_BASELINE.md).

A pendência de aceite que existia ao publicar a correção deixou de existir.
Os testes e a preservação descritos acima pertencem à tarefa original; a
redefinição controlada da credencial ocorreu depois, em operação separada, sem
novo deploy. Este registro documental não altera origem, CSRF, secrets ou health.
