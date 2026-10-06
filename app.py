
import os
from datetime import date, datetime
from collections import defaultdict

import requests
from flask import Flask, render_template_string, request, jsonify

app = Flask(__name__)

# =========================================================
# CONFIGURAÇÃO
# =========================================================

GESTAOCLICK_BASE_URL = os.getenv(
    "GESTAOCLICK_BASE_URL",
    "https://gestaoclick.com/api"
).rstrip("/")

ACCESS_TOKEN = os.getenv("GESTAOCLICK_ACCESS_TOKEN")
SECRET_ACCESS_TOKEN = os.getenv("GESTAOCLICK_SECRET_ACCESS_TOKEN")

# Regras iniciais da Smart One
DIAS_EM_RISCO = 90
DIAS_INATIVO = 180

# VIP provisório.
# Depois vamos calibrar com os números reais da Smart One.
VIP_VALOR_MINIMO = 1000
VIP_COMPRAS_MINIMAS = 5


# =========================================================
# GESTÃOCLICK
# =========================================================

def headers_gestaoclick():
    return {
        "access-token": ACCESS_TOKEN or "",
        "secret-access-token": SECRET_ACCESS_TOKEN or "",
        "Accept": "application/json"
    }


def consultar_api(endpoint, params=None):
    if not ACCESS_TOKEN or not SECRET_ACCESS_TOKEN:
        raise RuntimeError(
            "Credenciais do GestãoClick não configuradas no servidor."
        )

    url = f"{GESTAOCLICK_BASE_URL}/{endpoint.lstrip('/')}"

    response = requests.get(
        url,
        headers=headers_gestaoclick(),
        params=params or {},
        timeout=30
    )

    response.raise_for_status()

    return response.json()


def listar_todos(endpoint, filtros=None):
    """
    Percorre todas as páginas do GestãoClick.
    Limite de 100 registros por página.
    """

    pagina = 1
    registros = []

    while True:

        params = dict(filtros or {})

        params["pagina"] = pagina
        params["limite"] = 100

        resposta = consultar_api(endpoint, params)

        dados = resposta.get("data", [])
        meta = resposta.get("meta", {})

        registros.extend(dados)

        proxima = meta.get("proxima_pagina")

        if not proxima:
            break

        pagina += 1

    return registros


# =========================================================
# FUNÇÕES AUXILIARES
# =========================================================

def moeda(valor):
    try:
        valor = float(valor)

        texto = f"{valor:,.2f}"

        texto = texto.replace(",", "X")
        texto = texto.replace(".", ",")
        texto = texto.replace("X", ".")

        return f"R$ {texto}"

    except:
        return "R$ 0,00"


def converter_data(data_string):

    if not data_string:
        return None

    try:
        return datetime.strptime(
            data_string,
            "%Y-%m-%d"
        ).date()

    except:
        return None


def formatar_data(data_string):

    data_obj = converter_data(data_string)

    if not data_obj:
        return "-"

    return data_obj.strftime("%d/%m/%Y")


def dias_desde(data_string):

    data_obj = converter_data(data_string)

    if not data_obj:
        return None

    return (date.today() - data_obj).days


def definir_status(ultima_compra):

    dias = dias_desde(ultima_compra)

    if dias is None:
        return "Sem compra"

    if dias < DIAS_EM_RISCO:
        return "Ativo"

    if dias < DIAS_INATIVO:
        return "Em risco"

    return "Inativo"


def calcular_vip(total_gasto, quantidade_compras):

    return (
        total_gasto >= VIP_VALOR_MINIMO
        and quantidade_compras >= VIP_COMPRAS_MINIMAS
    )


def aniversario_hoje(data_nascimento):

    nascimento = converter_data(data_nascimento)

    if not nascimento:
        return False

    hoje = date.today()

    return (
        nascimento.day == hoje.day
        and nascimento.month == hoje.month
    )


def dias_ate_aniversario(data_nascimento):

    nascimento = converter_data(data_nascimento)

    if not nascimento:
        return None

    hoje = date.today()

    try:
        proximo = nascimento.replace(year=hoje.year)

    except ValueError:
        # 29/02
        proximo = date(
            hoje.year,
            2,
            28
        )

    if proximo < hoje:

        try:
            proximo = nascimento.replace(
                year=hoje.year + 1
            )

        except ValueError:
            proximo = date(
                hoje.year + 1,
                2,
                28
            )

    return (proximo - hoje).days


# =========================================================
# MOTOR SMART ONE
# =========================================================

def gerar_base_clientes():

    clientes = listar_todos("clientes")

    vendas = listar_todos("vendas")

    historico = defaultdict(
        lambda: {
            "compras": 0,
            "total": 0.0,
            "ultima_compra": None,
            "produtos": [],
            "servicos": [],
            "vendedores": [],
            "lojas": []
        }
    )

    # -----------------------------------------------------
    # PROCESSAR VENDAS
    # -----------------------------------------------------

    for venda in vendas:

        cliente_id = str(
            venda.get("cliente_id") or ""
        )

        if not cliente_id:
            continue

        registro = historico[cliente_id]

        registro["compras"] += 1

        try:
            registro["total"] += float(
                venda.get("valor_total") or 0
            )
        except:
            pass

        data_venda = venda.get("data")

        if data_venda:

            if (
                registro["ultima_compra"] is None
                or data_venda > registro["ultima_compra"]
            ):
                registro["ultima_compra"] = data_venda

        # vendedor

        vendedor = venda.get("nome_vendedor")

        if vendedor:
            registro["vendedores"].append(vendedor)

        # loja

        loja = venda.get("nome_loja")

        if loja:
            registro["lojas"].append(loja)

        # produtos

        for item in venda.get("produtos") or []:

            produto = item.get("produto", {})

            nome = (
                produto.get("nome_produto")
                or produto.get("detalhes")
            )

            if nome:
                registro["produtos"].append(nome)

        # serviços

        for item in venda.get("servicos") or []:

            servico = item.get("servico", {})

            nome = (
                servico.get("nome_servico")
                or servico.get("detalhes")
            )

            if nome:
                registro["servicos"].append(nome)

    # -----------------------------------------------------
    # CRUZAR CLIENTES + VENDAS
    # -----------------------------------------------------

    resultado = []

    for cliente in clientes:

        cliente_id = str(cliente.get("id"))

        h = historico[cliente_id]

        compras = h["compras"]

        total = round(
            h["total"],
            2
        )

        ticket = (
            round(total / compras, 2)
            if compras
            else 0
        )

        status = definir_status(
            h["ultima_compra"]
        )

        vip = calcular_vip(
            total,
            compras
        )

        nascimento = cliente.get(
            "data_nascimento"
        )

        dias_aniversario = dias_ate_aniversario(
            nascimento
        )

        # cidade

        cidade = None

        enderecos = cliente.get(
            "enderecos"
        ) or []

        if enderecos:

            endereco = enderecos[0].get(
                "endereco",
                {}
            )

            cidade = endereco.get(
                "nome_cidade"
            )

        resultado.append({

            "id": cliente_id,

            "nome": (
                cliente.get("nome")
                or "Sem nome"
            ),

            "celular": (
                cliente.get("celular")
                or cliente.get("telefone")
                or ""
            ),

            "email": (
                cliente.get("email")
                or ""
            ),

            "data_nascimento": nascimento,

            "data_nascimento_formatada":
                formatar_data(nascimento),

            "cidade": cidade or "",

            "ultima_compra":
                h["ultima_compra"],

            "ultima_compra_formatada":
                formatar_data(
                    h["ultima_compra"]
                ),

            "dias_sem_comprar":
                dias_desde(
                    h["ultima_compra"]
                ),

            "compras": compras,

            "total_gasto": total,

            "total_gasto_formatado":
                moeda(total),

            "ticket_medio": ticket,

            "ticket_medio_formatado":
                moeda(ticket),

            "status": status,

            "vip": vip,

            "aniversario_hoje":
                aniversario_hoje(
                    nascimento
                ),

            "dias_ate_aniversario":
                dias_aniversario,

            "produtos": sorted(
                set(h["produtos"])
            ),

            "servicos": sorted(
                set(h["servicos"])
            ),

            "vendedores": sorted(
                set(h["vendedores"])
            ),

            "lojas": sorted(
                set(h["lojas"])
            )
        })

    return resultado


# =========================================================
# DASHBOARD
# =========================================================

HTML = """
<!DOCTYPE html>

<html lang="pt-BR">

<head>

<meta charset="UTF-8">

<meta
name="viewport"
content="width=device-width, initial-scale=1.0"
>

<title>
Smart One Clientes
</title>

<style>

* {
    box-sizing: border-box;
}

body {
    margin: 0;
    font-family: Arial, sans-serif;
    background: #f5f7fb;
    color: #101828;
}

header {
    background: #111827;
    color: white;
    padding: 22px 30px;
}

header h1 {
    margin: 0;
    font-size: 22px;
}

header p {
    margin: 5px 0 0;
    opacity: .7;
    font-size: 13px;
}

.container {
    max-width: 1300px;
    margin: auto;
    padding: 25px;
}

.grid {
    display: grid;
    grid-template-columns:
    repeat(4, 1fr);
    gap: 14px;
}

.card {
    background: white;
    border: 1px solid #e4e7ec;
    border-radius: 14px;
    padding: 18px;
}

.card small {
    color: #667085;
}

.numero {
    font-size: 30px;
    font-weight: bold;
    margin-top: 8px;
}

h2 {
    margin-top: 28px;
}

.toolbar {
    display: flex;
    gap: 10px;
    margin-bottom: 15px;
    flex-wrap: wrap;
}

input,
select {
    padding: 11px;
    border: 1px solid #d0d5dd;
    border-radius: 8px;
}

input {
    min-width: 260px;
}

table {
    width: 100%;
    border-collapse: collapse;
    background: white;
}

th,
td {
    padding: 12px;
    text-align: left;
    border-bottom:
    1px solid #e4e7ec;
    font-size: 13px;
}

th {
    background: #f9fafb;
}

.badge {
    padding: 5px 8px;
    border-radius: 20px;
    background: #eef2f6;
    font-weight: bold;
    font-size: 11px;
}

.vip {
    background: #fff4cc;
}

.aniversario {
    background: #fef3f2;
}

.alerta {
    background: #fff7ed;
}

@media(max-width:800px) {

    .grid {
        grid-template-columns:
        repeat(2,1fr);
    }

    table {
        display: block;
        overflow-x: auto;
    }
}

</style>

</head>

<body>

<header>

<h1>
SMART ONE CLIENTES
</h1>

<p>
Inteligência comercial conectada ao GestãoClick
</p>

</header>

<div class="container">

<div class="grid">

<div class="card">

<small>
Clientes cadastrados
</small>

<div class="numero">
{{ total }}
</div>

</div>


<div class="card">

<small>
Clientes ativos
</small>

<div class="numero">
{{ ativos }}
</div>

</div>


<div class="card">

<small>
VIPs
</small>

<div class="numero">
{{ vips }}
</div>

</div>


<div class="card aniversario">

<small>
Aniversariantes hoje
</small>

<div class="numero">
{{ aniversariantes }}
</div>

</div>

</div>


<h2>
Oportunidades de hoje
</h2>


<div class="grid">

<div class="card">

<small>
Clientes em risco
</small>

<div class="numero">
{{ em_risco }}
</div>

</div>


<div class="card alerta">

<small>
Clientes inativos
</small>

<div class="numero">
{{ inativos }}
</div>

</div>


<div class="card">

<small>
VIPs em risco/inativos
</small>

<div class="numero">
{{ vips_reativar }}
</div>

</div>


<div class="card">

<small>
Aniversários próximos 7 dias
</small>

<div class="numero">
{{ proximos_aniversarios }}
</div>

</div>

</div>


<h2>
Clientes
</h2>


<div class="toolbar">

<input
id="busca"
placeholder="Buscar cliente ou telefone..."
onkeyup="filtrar()"
>

<select
id="filtroStatus"
onchange="filtrar()"
>

<option value="">
Todos
</option>

<option value="VIP">
VIP
</option>

<option value="Ativo">
Ativo
</option>

<option value="Em risco">
Em risco
</option>

<option value="Inativo">
Inativo
</option>

</select>

</div>


<table id="tabela">

<thead>

<tr>

<th>
Cliente
</th>

<th>
WhatsApp
</th>

<th>
Última compra
</th>

<th>
Compras
</th>

<th>
Total gasto
</th>

<th>
Ticket médio
</th>

<th>
Status
</th>

</tr>

</thead>


<tbody>

{% for cliente in clientes %}

<tr>

<td>

<strong>
{{ cliente.nome }}
</strong>

{% if cliente.vip %}

<span class="badge vip">
VIP
</span>

{% endif %}

{% if cliente.aniversario_hoje %}

<span class="badge aniversario">
🎂 Hoje
</span>

{% endif %}

</td>


<td>
{{ cliente.celular }}
</td>


<td>
{{ cliente.ultima_compra_formatada }}
</td>


<td>
{{ cliente.compras }}
</td>


<td>
{{ cliente.total_gasto_formatado }}
</td>


<td>
{{ cliente.ticket_medio_formatado }}
</td>


<td>

<span class="badge">
{{ cliente.status }}
</span>

</td>

</tr>

{% endfor %}

</tbody>

</table>

</div>


<script>

function filtrar() {

    const busca =
        document
        .getElementById("busca")
        .value
        .toLowerCase();

    const status =
        document
        .getElementById("filtroStatus")
        .value
        .toLowerCase();

    const linhas =
        document.querySelectorAll(
            "#tabela tbody tr"
        );

    linhas.forEach(linha => {

        const texto =
            linha.innerText
            .toLowerCase();

        const atendeBusca =
            texto.includes(busca);

        const atendeStatus =
            !status ||
            texto.includes(status);

        linha.style.display =
            atendeBusca &&
            atendeStatus
            ? ""
            : "none";

    });

}

</script>

</body>

</html>
"""


# =========================================================
# ROTAS
# =========================================================

@app.route("/")
def dashboard():

    try:

        clientes = gerar_base_clientes()

        total = len(clientes)

        ativos = sum(
            1
            for c in clientes
            if c["status"] == "Ativo"
        )

        em_risco = sum(
            1
            for c in clientes
            if c["status"] == "Em risco"
        )

        inativos = sum(
            1
            for c in clientes
            if c["status"] == "Inativo"
        )

        vips = sum(
            1
            for c in clientes
            if c["vip"]
        )

        aniversariantes = sum(
            1
            for c in clientes
            if c["aniversario_hoje"]
        )

        proximos_aniversarios = sum(
            1
            for c in clientes
            if c["dias_ate_aniversario"]
            is not None
            and
            0 <=
            c["dias_ate_aniversario"]
            <= 7
        )

        vips_reativar = sum(
            1
            for c in clientes
            if c["vip"]
            and c["status"]
            in [
                "Em risco",
                "Inativo"
            ]
        )

        # VIPs primeiro
        # depois maior gasto

        clientes.sort(
            key=lambda c: (
                c["vip"],
                c["total_gasto"]
            ),
            reverse=True
        )

        return render_template_string(

            HTML,

            clientes=clientes,

            total=total,

            ativos=ativos,

            em_risco=em_risco,

            inativos=inativos,

            vips=vips,

            aniversariantes=
                aniversariantes,

            proximos_aniversarios=
                proximos_aniversarios,

            vips_reativar=
                vips_reativar
        )

    except Exception as erro:

        return f"""
        <h2>Smart One Clientes</h2>

        <p>
        Não foi possível carregar os dados.
        </p>

        <pre>
        {str(erro)}
        </pre>

        <p>
        Verifique as variáveis de ambiente
        do GestãoClick no Render.
        </p>
        """, 500

@app.route("/teste-gestaoclick")
def teste_gestaoclick():
    try:
        resposta = requests.get(
            f"{GESTAOCLICK_BASE_URL}/clientes",
            headers={
                "access-token": ACCESS_TOKEN,
                "secret-access-token": SECRET_ACCESS_TOKEN,
                "accept": "application/json"
            },
            timeout=30
        )

        return jsonify({
            "status_http": resposta.status_code,
            "resposta": resposta.json()
        })

    except Exception as erro:
        return jsonify({
            "conexao": "erro",
            "detalhes": str(erro)
        }), 500
@app.route("/health")
def health():

    return jsonify({
        "status": "ok",
        "app": "Smart One Clientes"
    })


# =========================================================
# INICIAR
# =========================================================
@app.route("/teste-vendas")
def teste_vendas():
    url = f"{GESTAOCLICK_BASE_URL}/vendas"

    headers = {
        "access-token": ACCESS_TOKEN,
        "secret-access-token": SECRET_ACCESS_TOKEN,
        "Accept": "application/json"
    }

    try:
        resposta = requests.get(
            url,
            headers=headers,
            timeout=30
        )

        return jsonify({
            "status_http": resposta.status_code,
            "resposta": resposta.json()
        })

    except Exception as erro:
        return jsonify({
            "erro": str(erro)
        }), 500
@app.route("/debug-clientes")
def debug_clientes():
    headers = {
        "access-token": ACCESS_TOKEN,
        "secret-access-token": SECRET_ACCESS_TOKEN,
        "Accept": "application/json"
    }

    try:
        resposta = requests.get(
            f"{GESTAOCLICK_BASE_URL}/clientes",
            headers=headers,
            timeout=30
        )

        return jsonify({
            "status_http": resposta.status_code,
            "resposta_completa": resposta.json()
        })

    except Exception as erro:
        return jsonify({
            "erro": str(erro)
        }), 500
@app.route("/debug-vendas")
def debug_vendas():
    headers = {
        "access-token": ACCESS_TOKEN,
        "secret-access-token": SECRET_ACCESS_TOKEN,
        "Accept": "application/json"
    }

    try:
        resposta = requests.get(
            f"{GESTAOCLICK_BASE_URL}/vendas",
            headers=headers,
            timeout=30
        )

        return jsonify({
            "status_http": resposta.status_code,
            "resposta_completa": resposta.json()
        })

    except Exception as erro:
        return jsonify({
            "erro": str(erro)
        }), 500
@app.route("/teste-clientes-vendas")
def teste_clientes_vendas():
    headers = {
        "access-token": ACCESS_TOKEN,
        "secret-access-token": SECRET_ACCESS_TOKEN,
        "accept": "application/json"
    } 
    try:
            # Buscar clientes
            resposta_clientes = requests.get(
                f"{GESTAOCLICK_BASE_URL}/clientes",
                headers=headers,
                timeout=30
            )
    
            clientes_json = resposta_clientes.json()
            clientes = clientes_json.get("data", [])
    
            # Buscar vendas
            resposta_vendas = requests.get(
                f"{GESTAOCLICK_BASE_URL}/vendas",
                headers=headers,
                timeout=30
            )
    
            vendas_json = resposta_vendas.json()
            vendas = vendas_json.get("data", [])
        
            # Organizar vendas por cliente
            historico = defaultdict(list)
    
            for venda in vendas:
                    cliente_id = str(venda.get("cliente_id", ""))
        
                    if cliente_id:
                        historico[cliente_id].append(venda)
        
            resultado = []
    
            for cliente in clientes:
    
                cliente_id = str(cliente.get("id", ""))
                vendas_cliente = historico.get(cliente_id, [])
    
                quantidade_compras = len(vendas_cliente)
    
                total_gasto = 0
    
                for venda in vendas_cliente:
                    for pagamento in venda.get("pagamentos",[]):
                        try:
                             total_gasto += float(
                                    pagamento.get("pagamento", {}) .get("valor", 0) or 0
                             )
                        except:
                            pass
                    datas_compras = []
            
                    for venda in vendas_cliente:
                        data_venda = venda.get("data", "")
                        if data_venda:
                            datas_compras.append(data_venda)
            
                    ultima_compra = max(datas_compras) if datas_compras else ""
                    if data_ultima:
                        dias_sem_comprar = (data.today() - data_ultima).days

                        if dias_sem_comprar <= DIAS_EM_RISCO:
                            status_cliente = "Ativo"
                        elif dias_sem_comprar <= DIAS_INATIVO:
                            status_cliente = "Em risco"
                        else:
                            status_cliente = "Inativo"
                     else:
                        dias_sem_comprar = None
                        status_cliente = "Sem histórico"
                    ticket_medio = (
                    total_gasto / quantidade_compras
                    if quantidade_compras > 0
                    else 0
                )
        
                    resultado.append({
                        "id": cliente_id,
                        "nome": cliente.get("nome", ""),
                        "celular": cliente.get("celular", ""),
                        "data_nascimento": cliente.get(
                            "data_nascimento", ""
                        ),
                            "quantidade_compras": quantidade_compras,
                            "total_gasto": round(total_gasto, 2),
                            "ticket_medio": round(ticket_medio, 2),
                            "ultima_compra": ultima_compra,
                            "dias_sem_comprar": dias_sem_coomprar,
                            "status": status_cliente
                        })
        
return jsonify({
    "code": 200,
    "quantidade_clientes": len(resultado),
    "clientes": resultado
})

    except Exception as erro:
        return jsonify({
            "code": 500,
            "erro": str(erro)
        }), 500
@app.route("/rotas")
def rotas():
    return jsonify([
        str(regra)
        for regra in app.url_map.iter_rules()
])
if __name__ == "__main__":

    port = int(
        os.environ.get(
            "PORT",
            10000
        )
    )

    app.run(
        host="0.0.0.0",
        port=port
    )
