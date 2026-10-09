# Correções Smart One Clientes — revisão antes de publicar

O app.py da main não podia ser importado: IndentationError na linha 1246.
A rota /teste-clientes-vendas usava um cálculo diferente do dashboard e carregava somente a primeira página de vendas.

## Comportamento corrigido

- Dashboard e /teste-clientes-vendas compartilham gerar_base_clientes.
- Clientes e vendas percorrem todas as páginas, com data ou dados, proxima_url, proxima_pagina e totais de paginação.
- Sem metadados, a consulta continua até uma página vazia, mesmo se a página anterior tiver menos de 100 registros.
- Páginas repetidas, respostas inválidas e falhas intermediárias interrompem a consulta, sem mostrar uma base parcial como completa.
- IDs repetidos são deduplicados.
- Uma venda conta como uma compra, independentemente do número de parcelas.
- valor_total tem prioridade; pagamentos[].pagamento.valor é a alternativa quando o total está ausente. Os dois nunca são somados juntos.
- Valores usam Decimal; total e ticket são arredondados em centavos.
- Última compra usa a maior data válida, com saída AAAA-MM-DD.
- Datas inválidas de vendas e valores ausentes/inválidos são reportados como erro, em vez de gerar indicadores incorretos.
- Cancelamentos explicitamente marcados por cancelado ou nome_situacao Cancelado/Cancelada são excluídos. IDs de situações personalizados não são presumidos.
- Status preservados conforme README: Ativo até 89 dias; Em risco de 90 a 179; Inativo a partir de 180; Sem compra sem histórico.
- VIP preservado: total >= R$ 1.000 e pelo menos 5 compras.
- Data atual no fuso America/Sao_Paulo.
- Filtro Ativo não seleciona Inativo; filtro VIP usa o atributo da linha.
- Diagnósticos usam a mesma autenticação, timeout, tratamento HTTP e consulta GET.
- CSS e estrutura visual preservados. No template, só atributos de filtro e sua lógica JavaScript foram alterados.

## Verificação executada localmente

- Python 3.12.
- python -m py_compile app.py test_app.py: aprovado.
- python -m unittest -v test_app: 30 testes aprovados.
- python -m gunicorn --check-config app:app: aprovado.
- Comparação do CSS e da estrutura HTML com o arquivo original: preservados, exceto a correção funcional dos filtros.

Os testes simulam respostas de clientes/vendas, paginação, erros HTTP, timeout e JSON inválido. Não usam tokens reais nem gravam dados no GestãoClick.

## Validação ainda necessária

Não foi possível testar chamadas autenticadas na conta da Smart One: as credenciais do Render não estão disponíveis neste ambiente. A URL base continua configurável em GESTAOCLICK_BASE_URL, sem troca do endereço existente.

Antes de autorizar uma publicação, conferir com a conta real:
- URL base, permissões da chave e formato efetivamente retornado;
- totais de clientes/vendas e indicadores de alguns clientes conhecidos;
- significado e preenchimento dos pagamentos quando valor_total estiver ausente;
- situações de cancelamento/devolução personalizadas;
- tempo de carregamento com o histórico completo (a aplicação continua consultando a API a cada acesso, sem cache).

O conector.py independente não foi alterado; o serviço Flask usa app.py.

## Publicação

Alterações propostas em branch separada, sem alterar main e sem merge.
Commit com [skip render]; PR em rascunho com [skip render] no título.
Não disparar deploy manual nem remover os marcadores antes da validação e autorização.

Referências oficiais consultadas:
- https://gestaoclick.com.br/integracoes/api/
- https://render.com/docs/deploys
- https://render.com/docs/service-previews
