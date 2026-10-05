import os, json, urllib.request, urllib.parse
from collections import defaultdict
from datetime import date, datetime

BASE=os.getenv("GESTAOCLICK_BASE_URL","https://gestaoclick.com/api").rstrip("/")
ACCESS=os.getenv("GESTAOCLICK_ACCESS_TOKEN")
SECRET=os.getenv("GESTAOCLICK_SECRET_ACCESS_TOKEN")

if not ACCESS or not SECRET:
    raise SystemExit("Configure GESTAOCLICK_ACCESS_TOKEN e GESTAOCLICK_SECRET_ACCESS_TOKEN no ambiente.")

def get(path, params=None):
    url=BASE+"/"+path.lstrip("/")
    if params: url += "?" + urllib.parse.urlencode(params)
    req=urllib.request.Request(url, headers={
        "access-token": ACCESS,
        "secret-access-token": SECRET,
        "Accept":"application/json"
    }, method="GET")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))

def listar_todas(path, filtros=None, limite=100):
    pagina=1; saida=[]
    while True:
        p=dict(filtros or {}); p.update({"pagina":pagina,"limite":limite})
        payload=get(path,p)
        dados=payload.get("data",[])
        saida.extend(dados)
        meta=payload.get("meta",{})
        if not meta.get("proxima_pagina") and len(dados)<limite: break
        pagina += 1
    return saida

def dias_desde(data_str):
    if not data_str: return None
    try: return (date.today()-datetime.strptime(data_str,"%Y-%m-%d").date()).days
    except: return None

def status_cliente(ultima):
    d=dias_desde(ultima)
    if d is None: return "Sem compra"
    if d < 90: return "Ativo"
    if d < 180: return "Em risco"
    return "Inativo"

clientes=listar_todas("clientes")
vendas=listar_todas("vendas")

agg=defaultdict(lambda:{"compras":0,"total":0.0,"ultima":None,"produtos":[],"servicos":[]})
for v in vendas:
    cid=str(v.get("cliente_id") or "")
    if not cid: continue
    a=agg[cid]
    a["compras"]+=1
    try: a["total"]+=float(v.get("valor_total") or 0)
    except: pass
    dt=v.get("data")
    if dt and (not a["ultima"] or dt>a["ultima"]): a["ultima"]=dt
    for x in v.get("produtos") or []:
        p=x.get("produto",{})
        a["produtos"].append(p.get("nome_produto") or p.get("detalhes") or str(p.get("produto_id","")))
    for x in v.get("servicos") or []:
        s=x.get("servico",{})
        a["servicos"].append(s.get("nome_servico") or s.get("detalhes") or str(s.get("servico_id","")))

saida=[]
for c in clientes:
    cid=str(c.get("id"))
    a=agg[cid]
    compras=a["compras"]; total=round(a["total"],2)
    saida.append({
        "id":cid,
        "nome":c.get("nome"),
        "celular":c.get("celular") or c.get("telefone"),
        "email":c.get("email"),
        "data_nascimento":c.get("data_nascimento"),
        "cidade": ((c.get("enderecos") or [{}])[0].get("endereco") or {}).get("nome_cidade"),
        "ultima_compra":a["ultima"],
        "compras":compras,
        "total_gasto":total,
        "ticket_medio":round(total/compras,2) if compras else 0,
        "status":status_cliente(a["ultima"]),
        "produtos":sorted(set(filter(None,a["produtos"]))),
        "servicos":sorted(set(filter(None,a["servicos"])))
    })

with open("clientes_consolidados.json","w",encoding="utf-8") as f:
    json.dump(saida,f,ensure_ascii=False,indent=2)

print(f"OK: {len(clientes)} clientes + {len(vendas)} vendas processadas -> clientes_consolidados.json")
