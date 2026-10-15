# Agenda de serviços

Nova aba `/agenda` no Smart One Clientes. Usa `DATABASE_URL` existente com tabela independente `smartone_agenda`, criada automaticamente no primeiro acesso. Não altera dados nem ordens de serviço do GestãoClick. Os aniversários permanecem na tabela existente.

- Consulta por dia, semana ou entregas atrasadas; considera agendamento e previsão de entrega.
- Cadastro e edição de cliente, WhatsApp, aparelho, serviço, responsável, O.S., datas, status e observações.
- Busca opcional dos clientes da base existente. Cadastro manual não depende da sincronização do ERP.
- Horários de Brasília. Autenticação administrativa existente, formulário assinado, validação no servidor, versão para evitar sobrescrever edições simultâneas e identificador para impedir duplicação por reenvio.
- WhatsApp abre mensagem para revisão e envio manual. Não envia automaticamente.
- Cancelamento pelo status Cancelado. Não há exclusão definitiva pela interface.
- Consulta limitada aos primeiros 500 registros com aviso; busca/status se aplicam ao lote. Usar períodos menores em bases grandes.

## Validação

Oito testes novos em `test_agenda.py` aprovados: autenticação, proteção do formulário, criação, validação dos campos, preservação de dados em falha, semana, escape HTML e conflito de edição.

A suíte anterior em `main` já apresentava 19 falhas e 3 erros em 68 testes. A branch mantém os mesmos resultados anteriores, somando os oito testes novos aprovados (76 no total). Entre as expectativas antigas estão VIP por dois critérios e HEAD público. Não foram alteradas regras do painel para satisfazer expectativas desatualizadas.

Validação visual concluída em Chromium: cadastro, edição, status Pronto, link de WhatsApp e formulário móvel de 390 px, sem erros JavaScript ou transbordamento horizontal no formulário. Capturas revisadas em desktop e celular. As rotas também foram exercitadas pelo cliente de testes do Flask.

As consultas SQL do módulo de armazenamento foram executadas em PGlite local (motor PostgreSQL), por adaptador de teste: criação da tabela, persistência entre consultas, reenvio sem duplicação, consulta por data, atrasos, edição, conflito de versão e rollback aprovados. Esse teste não substitui o teste de conexão psycopg com o Render.

O PostgreSQL real do Render ainda precisa de validação: criar serviço, recarregar para conferir persistência, editar status e conferir consulta. O banco de produção não foi acessado no desenvolvimento. Os testes automatizados usam armazenamento simulado.

## Publicação

Interface e gravação local validadas. Após o deploy manual no Render, cadastrar um serviço de teste e recarregar a página para confirmar a conexão com o PostgreSQL da loja. Nenhuma credencial ou dado real de cliente é incluído nesta alteração.
