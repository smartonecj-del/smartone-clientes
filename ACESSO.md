# Acesso ao Smart One Clientes

O painel e todos os diagnósticos exigem autenticação HTTP Basic. Use somente o endereço HTTPS do Render. O navegador solicitará usuário e senha ao abrir o painel.

Configure no Render, em Environment:

- `SMARTONE_ADMIN_USER`: usuário de acesso (padrão: `admin`).
- `SMARTONE_ADMIN_PASSWORD`: senha exclusiva e aleatória, com pelo menos 20 caracteres, criada e guardada pelo responsável em um gerenciador de senhas.

Sem senha configurada, ou com menos de 20 caracteres, as rotas ficam bloqueadas e retornam 503. Com senha configurada, tentativas não autenticadas retornam 401. Apenas `/health` e `HEAD /` continuam públicos, sem dados de clientes.

Não use os tokens do GestãoClick como senha. Não coloque a senha no GitHub, em URLs ou em mensagens. Configure o valor somente no ambiente do Render. Após salvar, publique/reinicie o serviço para aplicar a configuração.

As respostas usam `Cache-Control: no-store, private` e `Vary: Authorization`. O cache interno da importação só é consultado após autenticação. Esta proteção não apaga cópias eventualmente obtidas antes da mudança.

O acesso é compartilhado entre administradores que conhecem a credencial; não há contas individuais nesta versão. Para encerrar o acesso em um computador compartilhado, feche todas as janelas do navegador ou use uma janela privativa e feche-a ao terminar.
