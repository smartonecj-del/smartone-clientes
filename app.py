
import os
import json
import re
import unicodedata
from hashlib import sha256
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo
from datetime import date, datetime
from collections import defaultdict

import requests
from flask import Flask, render_template_string, jsonify, request
from markupsafe import escape

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
    """Lê todas as páginas; nunca devolve uma base parcialmente carregada."""
    pagina = 1
    registros = []
    ids_vistos = set()
    paginas_vistas = set()

    for _ in range(10000):
        params = dict(filtros or {})
        params.update({"pagina": pagina, "limite": 100})
        resposta = consultar_api(endpoint, params)
        if not isinstance(resposta, dict):
            raise RuntimeError("Resposta inválida do GestãoClick.")
        if str(resposta.get("code", 200)) != "200" or resposta.get("status") in (
            "error", "erro", "fail", "failed"
        ):
            raise RuntimeError("O GestãoClick recusou a consulta.")
        chave = "data" if "data" in resposta else "dados"
        if chave not in resposta:
            raise RuntimeError("Resposta do GestãoClick sem data/dados.")
        dados = resposta[chave]
        if not isinstance(dados, list) or any(
            not isinstance(item, dict) for item in dados
        ):
            raise RuntimeError("Lista de registros inválida no GestãoClick.")
        meta = resposta.get("meta") or {}
        if not isinstance(meta, dict):
            raise RuntimeError("Paginação inválida no GestãoClick.")

        assinatura = sha256(json.dumps(
            dados, sort_keys=True, ensure_ascii=False
        ).encode("utf-8")).digest()
        if dados and assinatura in paginas_vistas:
            raise RuntimeError("O GestãoClick repetiu uma página; consulta interrompida.")
        paginas_vistas.add(assinatura)
        for item in dados:
            identificador = item.get("id")
            if identificador not in (None, ""):
                identificador = str(identificador)
                if identificador in ids_vistos:
                    continue
                ids_vistos.add(identificador)
            registros.append(item)

        proxima = None
        tem_proxima = False
        for campo in ("proxima_url", "proxima_pagina"):
            if campo in meta:
                tem_proxima = True
                proxima = meta[campo]
                break

        if not dados:
            if meta.get("total_paginas") is not None and pagina < int(meta["total_paginas"]):
                raise RuntimeError("Página vazia antes do fim da consulta no GestãoClick.")
            if proxima:
                raise RuntimeError("Página vazia com continuação no GestãoClick.")
            if meta.get("total_registros") is not None:
                if len(registros) != int(meta["total_registros"]):
                    raise RuntimeError("Quantidade de registros incompleta no GestãoClick.")
            return registros

        if tem_proxima:
            if proxima in (None, "", False, 0, "0", "false"):
                if meta.get("total_registros") is not None:
                    if len(registros) != int(meta["total_registros"]):
                        raise RuntimeError("Quantidade de registros incompleta no GestãoClick.")
                return registros
            if isinstance(proxima, str) and ("?" in proxima or "/" in proxima):
                # Extrai somente o número; nunca envia tokens à URL recebida.
                consulta = parse_qs(urlparse(proxima).query)
                valor = consulta.get("pagina", [None])[0]
                proxima = int(valor) if valor is not None else pagina + 1
            elif isinstance(proxima, bool):
                proxima = pagina + 1
            else:
                proxima = int(proxima)
            if proxima <= pagina:
                raise RuntimeError("Próxima página inválida no GestãoClick.")
            pagina = proxima
        elif meta.get("total_paginas") is not None:
            if pagina >= int(meta["total_paginas"]):
                if meta.get("total_registros") is not None:
                    if len(registros) != int(meta["total_registros"]):
                        raise RuntimeError("Quantidade de registros incompleta no GestãoClick.")
                return registros
            pagina += 1
        elif meta.get("total_registros") is not None:
            total = int(meta["total_registros"])
            if len(registros) == total:
                return registros
            if len(registros) > total:
                raise RuntimeError("Total de registros inconsistente no GestãoClick.")
            pagina += 1
        else:
            # Sem metadados, uma página curta não garante o fim da lista.
            pagina += 1

    raise RuntimeError("Limite de páginas excedido; base não carregada.")


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


def hoje_local():
    return datetime.now(ZoneInfo("America/Sao_Paulo")).date()


def converter_data(data_string):
    if isinstance(data_string, datetime):
        return data_string.date()
    if isinstance(data_string, date):
        return data_string
    if not isinstance(data_string, str) or not data_string.strip():
        return None
    texto = data_string.strip()
    try:
        return datetime.fromisoformat(texto.replace("Z", "+00:00")).date()
    except ValueError:
        try:
            return datetime.strptime(texto, "%d/%m/%Y").date()
        except ValueError:
            return None


def valor_decimal(valor):
    """Aceita decimais da API e valores monetários brasileiros."""
    texto = str(valor).strip().replace("R$", "").replace(" ", "")
    if "," in texto:
        texto = texto.replace(".", "").replace(",", ".")
    try:
        numero = Decimal(texto)
    except InvalidOperation as erro:
        raise RuntimeError("Valor monetário inválido no GestãoClick.") from erro
    if not numero.is_finite():
        raise RuntimeError("Valor monetário inválido no GestãoClick.")
    return numero


def total_da_venda(venda):
    # Total da venda tem prioridade. Parcelas não são compras adicionais.
    if venda.get("valor_total") not in (None, ""):
        return valor_decimal(venda["valor_total"])
    pagamentos = venda.get("pagamentos") or []
    if not pagamentos:
        raise RuntimeError("Venda sem valor_total ou pagamentos no GestãoClick.")
    total = Decimal("0")
    for item in pagamentos:
        pagamento = item.get("pagamento", item)
        valor = pagamento.get("valor")
        if valor in (None, ""):
            raise RuntimeError("Pagamento sem valor no GestãoClick.")
        total += valor_decimal(valor)
    return total


def venda_cancelada(venda):
    # Situações possuem IDs personalizados: não presumir um ID de cancelamento.
    return (
        str(venda.get("cancelado", "")).lower() in ("1", "true", "sim")
        or str(venda.get("nome_situacao", "")).strip().casefold()
        in ("cancelado", "cancelada")
    )


def formatar_data(data_string):

    data_obj = converter_data(data_string)

    if not data_obj:
        return "-"

    return data_obj.strftime("%d/%m/%Y")


def dias_desde(data_string):

    data_obj = converter_data(data_string)

    if not data_obj:
        return None

    return (hoje_local() - data_obj).days


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

    hoje = hoje_local()

    return (
        nascimento.day == hoje.day
        and nascimento.month == hoje.month
    )


def dias_ate_aniversario(data_nascimento):

    nascimento = converter_data(data_nascimento)

    if not nascimento:
        return None

    hoje = hoje_local()

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

def listar_vendas():
    """Consulta explicitamente os três tipos documentados pelo GestãoClick."""
    vendas = []
    ids_vistos = set()
    for tipo in ("produto", "servico", "vendas_balcao"):
        for venda in listar_todos("vendas", {"tipo": tipo}):
            venda_id = venda.get("id")
            if venda_id not in (None, ""):
                venda_id = str(venda_id)
                if venda_id in ids_vistos:
                    continue
                ids_vistos.add(venda_id)
            vendas.append(venda)
    return vendas


def telefone_normalizado(valor):
    numero = re.sub(r"\D", "", str(valor or ""))
    if len(numero) in (12, 13) and numero.startswith("55"):
        numero = numero[2:]
    return numero if len(numero) in (10, 11) else ""


def chave_consumidor(nome, telefone):
    nome = " ".join(unicodedata.normalize("NFC", str(nome or "")).casefold().split())
    telefone = telefone_normalizado(telefone)
    return (nome, telefone) if nome and telefone else None


def consumidor_da_venda(venda):
    # Formato observado na API: nome_cliente = "Nome completo - telefone".
    # Nunca vincula somente pelo nome, ou somente por telefone compartilhado.
    texto = str(venda.get("nome_cliente") or "").strip()
    partes = re.fullmatch(r"(.+?)\s+-\s+([+()0-9 .-]+)", texto)
    if not partes:
        return None
    nome, telefone = partes.group(1).strip(), telefone_normalizado(partes.group(2))
    if not nome or not telefone:
        return None
    return {"nome": nome, "celular": telefone}


def gerar_base_clientes():

    clientes = listar_todos("clientes")

    vendas = listar_vendas()

    indice_consumidores = defaultdict(set)
    for cliente in clientes:
        for telefone in (cliente.get("celular"), cliente.get("telefone")):
            chave = chave_consumidor(cliente.get("nome"), telefone)
            if chave and cliente.get("id") not in (None, ""):
                indice_consumidores[chave].add(str(cliente["id"]))
    consumidores_avulsos = {}

    historico = defaultdict(
        lambda: {
            "compras": 0,
            "total": Decimal("0"),
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

    vendas_vistas = set()
    for venda in vendas:
        venda_id = venda.get("id")
        if venda_id not in (None, ""):
            venda_id = str(venda_id)
            if venda_id in vendas_vistas:
                continue
            vendas_vistas.add(venda_id)
        if venda_cancelada(venda):
            continue

        cliente_id = str(
            venda.get("cliente_id") or ""
        )

        if cliente_id in ("", "0"):
            consumidor = consumidor_da_venda(venda)
            if consumidor is None:
                continue
            chave = chave_consumidor(consumidor["nome"], consumidor["celular"])
            candidatos = indice_consumidores.get(chave, set())
            if len(candidatos) == 1:
                cliente_id = next(iter(candidatos))
            else:
                cliente_id = "consumidor-" + sha256(
                    json.dumps(chave, ensure_ascii=False).encode("utf-8")
                ).hexdigest()
                if cliente_id not in consumidores_avulsos:
                    consumidor.update({"id": cliente_id, "origem": "Identificado na venda"})
                    consumidores_avulsos[cliente_id] = consumidor

        registro = historico[cliente_id]

        registro["compras"] += 1

        registro["total"] += total_da_venda(venda)
        data_venda = converter_data(venda.get("data"))
        if data_venda is None:
            raise RuntimeError("Venda com data ausente ou inválida no GestãoClick.")
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

    for cliente in clientes + list(consumidores_avulsos.values()):

        cliente_id = str(cliente.get("id"))

        h = historico[cliente_id]

        compras = h["compras"]

        total = float(h["total"].quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
        ticket = (
            float((h["total"] / compras).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            ))
            if compras else 0.0
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
                h["ultima_compra"].isoformat() if h["ultima_compra"] else None,

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

<tr data-status="{{ cliente.status|lower }}" data-vip="{{ 'true' if cliente.vip else 'false' }}">

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
            (status === "vip"
                ? linha.dataset.vip === "true"
                : linha.dataset.status === status);

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
    # HEAD é usado para verificar disponibilidade; não deve consultar o ERP.
    if request.method == "HEAD":
        return "", 200
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
        {escape(str(erro))}
        </pre>

        <p>
        Verifique as variáveis de ambiente
        do GestãoClick no Render.
        </p>
        """, 500

def diagnosticar_api(endpoint, campo_resposta="resposta"):
    params = {"pagina": 1, "limite": 100}
    if endpoint == "vendas":
        venda_id = request.args.get("id")
        tipo = request.args.get("tipo")
        codigo = request.args.get("codigo")
        if tipo is not None:
            if tipo not in ("produto", "servico", "vendas_balcao"):
                return jsonify({"erro": "Tipo de venda inválido."}), 400
            params["tipo"] = tipo
        if codigo is not None:
            if not codigo.isascii() or not codigo.isdigit() or len(codigo) > 20:
                return jsonify({"erro": "Código de venda inválido."}), 400
            params["codigo"] = codigo
        if venda_id is not None:
            if not venda_id.isascii() or not venda_id.isdigit() or len(venda_id) > 20:
                return jsonify({"erro": "Identificador de venda inválido."}), 400
            if tipo is not None or codigo is not None:
                return jsonify({"erro": "Use o identificador sem tipo ou código."}), 400
            endpoint = f"vendas/{venda_id}"
            params = {}
    try:
        resposta = consultar_api(endpoint, params)
        return jsonify({"status_http": 200, campo_resposta: resposta})
    except requests.HTTPError as erro:
        status = erro.response.status_code if erro.response is not None else 502
        return jsonify({
            "status_http": status,
            "erro": "O GestãoClick recusou a consulta."
        }), 502
    except (requests.RequestException, ValueError):
        return jsonify({"erro": "Falha na comunicação com o GestãoClick."}), 502
    except RuntimeError as erro:
        return jsonify({"erro": str(erro)}), 500


@app.route("/teste-gestaoclick")
def teste_gestaoclick():
    return diagnosticar_api("clientes")


@app.route("/health")
def health():
    return jsonify({"status": "ok", "app": "Smart One Clientes"})


@app.route("/teste-vendas")
def teste_vendas():
    return diagnosticar_api("vendas")


@app.route("/debug-clientes")
def debug_clientes():
    return diagnosticar_api("clientes", "resposta_completa")


@app.route("/debug-vendas")
def debug_vendas():
    return diagnosticar_api("vendas", "resposta_completa")


@app.route("/teste-clientes-vendas")
def teste_clientes_vendas():
    try:
        clientes = gerar_base_clientes()
        resultado = [{
            "id": cliente["id"],
            "nome": cliente["nome"],
            "celular": cliente["celular"],
            "data_nascimento": cliente["data_nascimento"],
            "quantidade_compras": cliente["compras"],
            "total_gasto": cliente["total_gasto"],
            "ticket_medio": cliente["ticket_medio"],
            "ultima_compra": cliente["ultima_compra"],
            "dias_sem_comprar": cliente["dias_sem_comprar"],
            "status": cliente["status"]
        } for cliente in clientes]
        return jsonify({
            "code": 200,
            "quantidade_clientes": len(resultado),
            "clientes": resultado
        })
    except Exception:
        app.logger.exception("Falha ao consolidar clientes e vendas")
        return jsonify({
            "code": 500,
            "erro": "Não foi possível carregar a base completa do GestãoClick."
        }), 500


@app.route("/rotas")
def rotas():
    return jsonify([
        str(regra)
        for regra in app.url_map.iter_rules()
])

# =========================================================
# INICIAR
# =========================================================
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
