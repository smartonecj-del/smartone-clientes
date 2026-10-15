import ast,re,json,unicodedata
from hashlib import sha256
from decimal import Decimal,InvalidOperation,ROUND_HALF_UP
from datetime import date,datetime
from zoneinfo import ZoneInfo
from collections import defaultdict
s=open('app.py').read(); tree=ast.parse(s)
ns=dict(globals(), DIAS_EM_RISCO=90,DIAS_INATIVO=180,VIP_VALOR_MINIMO=1000,VIP_COMPRAS_MINIMAS=5)
funcs=[n for n in tree.body if isinstance(n,ast.FunctionDef) and not n.decorator_list]
exec(compile(ast.Module(body=funcs,type_ignores=[]),'app.py','exec'),ns)
def run(clientes,vendas):
 ns['listar_todos']=lambda endpoint:clientes
 ns['listar_vendas']=lambda:vendas
 return ns['gerar_base_clientes']()
def venda(i,nome='',cid='0',data='2025-09-01',valor='100'):
 return dict(id=str(i),nome_cliente=nome,cliente_id=cid,data=data,valor_total=valor)
clientes=[dict(id='1',nome='Maria Silva',celular='(38) 99999-9999'),dict(id='2',nome='Maria Souza',telefone='+55 38 99999-9999')]
vendas=[venda(1,'Maria - 38999999999'),venda(2,'Maria Souza - +55 (38) 99999-9999'),venda(3,cid='2'),venda(4,cid='1'),venda(5,'Maria Silva-38999999999'),venda(5,'Maria Silva-38999999999'),venda(6,'Maria - 38999999999',data='2025-08-31')]
r=run(clientes,vendas)
assert len(r)==1 and r[0]['compras']==5 and r[0]['total_gasto']==500,r
assert set(r[0]['ids_agrupados']) >= {'1','2'}
r=run([],vendas[:2]);assert len(r)==1 and r[0]['compras']==2
r=run([], [venda(1,'Maria - 38999999999'),venda(2,'Maria - 38988888888')]);assert len(r)==2
r=run([], [venda(1,'Maria')]);assert r==[]
r=run(clientes,[venda(1,'Maria - 38999999999')]); c=r[0]
oldid=next(x for x in c['ids_agrupados'] if x.startswith('consumidor-'))
assert ns['clientes_com_aniversarios'](r,{oldid:'1990-01-01'})[0]['data_nascimento']=='1990-01-01'
print('OK: nomes diferentes, cadastros duplicados, +55, venda repetida, corte 01/09/2025, contatos distintos, sem contato e aniversários antigos')
