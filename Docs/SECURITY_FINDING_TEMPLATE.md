# Template de finding — preencher somente na descoberta autorizada

Template vazio. Não representa vulnerabilidade encontrada, evidência coletada,
CVE ou severidade. Referência de processo: [SECURITY_GATE.md](SECURITY_GATE.md).

| Campo | Preenchimento futuro |
| --- | --- |
| ID | A atribuir quando houver registro real |
| Título | A preencher |
| Componente e superfície | A preencher |
| Categoria | A preencher |
| Fronteira de confiança | TB aplicável da baseline, se houver |
| Ambiente e alvo autorizado | A preencher; não incluir dados pessoais |
| Commit/versão/artefato | A preencher |
| Ferramenta e versão / método manual | A preencher |
| Data, responsável e correlação | A preencher sem material secreto |
| Descrição | A preencher |
| Pré-condições e permissões necessárias | A preencher |
| Passos de reprodução | A preencher usando dados fictícios |
| Resultado esperado | A preencher |
| Resultado observado | A preencher |
| Evidência e localização restrita | A preencher com referência sanitizada, nunca valores de secrets |
| Classe da evidência | Hipótese / reproduzida / não reproduzida, somente após análise |
| Impacto potencial e alcance demonstrado | A preencher; distinguir demonstração de hipótese |
| Status | A preencher na descoberta; este template não tem status de finding |
| Observações, limites e duplicatas | A preencher |
| Referência externa/CVE | Somente se aplicável e verificada; não inventar identificador |

## Reprodução

1. [Pré-condição controlada em QA]
2. [Ação dentro do escopo autorizado]
3. [Observação e evidência redigida]

## Consolidação posterior

Prioridade/severidade e justificativa: **não atribuídas no template**. Preencher
após confirmar evidência e impacto durante a consolidação.

## Remediação e reteste — fase separada

- Tarefa/autorização de correção: [preencher somente na fase apropriada].
- Mudança e commit: [a preencher].
- Regressões verificadas: [a preencher].
- Reteste QA, resultado e evidência: [a preencher].
- Fechamento/reabertura: [a preencher após reteste].
- Smoke PROD não destrutivo: [somente quando autorizado após os gates].
