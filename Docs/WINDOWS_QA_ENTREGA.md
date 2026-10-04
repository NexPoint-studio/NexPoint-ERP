# Gate de Windows limpo para a entrega Nil lav

Estado em 04/10/2026: **não executado**. Este documento é o roteiro de QA;
nenhum resultado abaixo deve ser tratado como evidência de instalação real.

O host de desenvolvimento é Windows 10 Pro 19045, mas não possui
`WindowsSandbox.exe`, VirtualBox, VMware ou QEMU disponível. A consulta DISM ao
recurso Windows Sandbox exigiu elevação (erro 740). O host e o notebook real
não substituem uma VM/ambiente descartável para este teste.

Há também um gate de arquitetura do ensaio: o executável congelado atual aceita
somente manifesto `production/PROD` e a ativação empacotada chama o projeto
Supabase PROD fixo. Assim, abrir um Setup candidato em VM sem código de ativação
testa instalação e primeira tela, mas **não** conclui ativação, sync, recovery ou
updater A→B sem tocar PROD. Antes de executar essas etapas, preparar um backend
QA isolado e uma build de ensaio que se vincule exclusivamente a ele, ou outro
ambiente equivalente que demonstre o mesmo comportamento sem dados PROD.
Nenhuma alteração no Supabase/Render PROD está autorizada como atalho para QA.

## Sequência quando o ambiente estiver disponível

1. Criar VM Windows limpa e descartável, usuário Windows novo, snapshot inicial,
   sem acesso a arquivos reais da Nil lav. Registrar versão do Windows e hash do
   instalador candidato.
2. Conferir no candidato os atalhos, pasta do programa, pasta de dados separada,
   desinstalação registrada e versão. Não instalar no usuário Windows principal.
3. Com backend QA isolado, ativar instalação fictícia e confirmar DPAPI no mesmo
   usuário Windows, fechamento/reabertura direta e ausência de login operacional.
4. Exercitar Clientes, Serviços, Notas, Caixa, Financeiro, Administração sem
   senha, Empresa, Admin Lock, mudança/remoção da senha, recovery com consumo
   único, heartbeat, sync, versão e isolamento entre duas empresas QA.
5. Criar dados fictícios; instalar build QA A, publicar B somente no backend QA;
   validar detecção, download, hash, fechamento, instalação, reabertura e
   persistência de SQLite, company profile, Admin Lock e DPAPI.
6. Repetir com rede ausente, download interrompido, hash incorreto, artefato
   corrompido e cancelamento de UAC quando aplicável. Validar recuperação sem
   restaurar banco antigo sobre dados novos.
7. Desinstalar e reinstalar na VM; conferir política de preservação dos dados.
   Guardar evidência sanitizada (versões, hashes, contagens e resultados), nunca
   código de ativação, credencial, cliente real ou segredo.
8. Restaurar/destruir a VM ao fim do ensaio. Só então considerar os gates
   pré-PROD junto aos testes de código, PostgreSQL/Edge e backup real.

O status de cada item deve ser registrado com evidência da VM quando executado.
Nenhum item foi marcado como aprovado por testes unitários ou navegador local.

Foi feita somente uma compilação de **sonda QA não distribuível** do helper
`NexPointUpdater.exe` a partir do worktree atual. O binário executou
`--verify-runtime` com exit code 0 e passou no scan de conteúdo do pacote.
Em um `%LOCALAPPDATA%` sintético e isolado neste mesmo host, o helper compilado
executou `--handoff-prepare`: criou journal DPAPI, backup e relatório, mantendo
credencial e SQLite originais. Não contatou a nuvem. Isso valida o caminho
empacotado de preparação neste Windows, mas não valida instalação limpa,
preparação no notebook, confirmação cloud, update A→B ou limpeza do Setup.
