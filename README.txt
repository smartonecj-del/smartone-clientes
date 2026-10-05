SMART ONE CLIENTES — CONECTOR V1

Objetivo:
Ler clientes e vendas do GestãoClick e gerar uma base consolidada para a interface Smart One Clientes.

Segurança:
- NÃO coloque tokens dentro do código.
- Use as variáveis de ambiente GESTAOCLICK_ACCESS_TOKEN e GESTAOCLICK_SECRET_ACCESS_TOKEN.
- A V1 usa apenas requisições GET (somente leitura).

Endpoints:
- /api/clientes
- /api/vendas

Saída:
- clientes_consolidados.json

Classificações iniciais:
- Ativo: última compra até 89 dias
- Em risco: 90 a 179 dias
- Inativo: 180+ dias
- Sem compra: cliente sem venda localizada
- VIP: parâmetro configurável por total gasto e/ou quantidade de compras

Antes de produção, valide as regras comerciais e regenere as credenciais que foram compartilhadas anteriormente.
