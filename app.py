
import os
import json
import re
import unicodedata
from hmac import compare_digest
from hashlib import sha256
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo
from datetime import date, datetime
from collections import defaultdict

import requests
from flask import Flask, render_template_string, jsonify, request, url_for, redirect
from base_cache import BaseCache, BaseCarregando
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from aniversarios_store import configurado as banco_aniversarios_configurado, listar_aniversarios, salvar_aniversario

app = Flask(__name__)
app.config["SMARTONE_ADMIN_USER"] = os.getenv("SMARTONE_ADMIN_USER", "admin")
app.config["SMARTONE_ADMIN_PASSWORD"] = os.getenv("SMARTONE_ADMIN_PASSWORD", "")


@app.before_request
def exigir_autenticacao():
    # Apenas verificações sem dados continuam públicas.
    if request.path == "/health":
        return None
    senha = app.config["SMARTONE_ADMIN_PASSWORD"]
    usuario = app.config["SMARTONE_ADMIN_USER"]
    if not usuario or len(senha) < 20:
        return jsonify({"erro": "Acesso restrito. Autenticação ainda não configurada."}), 503
    credencial = request.authorization
    if (credencial is not None and credencial.type == "basic"
            and compare_digest((credencial.username or "").encode(), usuario.encode())
            and compare_digest((credencial.password or "").encode(), senha.encode())):
        return None
    resposta = jsonify({"erro": "Autenticação necessária."})
    resposta.status_code = 401
    resposta.headers["WWW-Authenticate"] = 'Basic realm="Smart One Clientes", charset="UTF-8"'
    return resposta


@app.after_request
def impedir_cache_publico(resposta):
    resposta.headers["Cache-Control"] = "no-store, private"
    resposta.headers["Pragma"] = "no-cache"
    resposta.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive"
    resposta.headers["Referrer-Policy"] = "no-referrer"
    resposta.headers["X-Content-Type-Options"] = "nosniff"
    resposta.vary.add("Authorization")
    return resposta

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

def listar_lojas():
    # /lojas retorna uma lista única, sem paginação na documentação oficial.
    resposta = consultar_api("lojas")
    if (not isinstance(resposta, dict) or str(resposta.get("code", 200)) != "200"
            or resposta.get("status") in ("error", "erro", "fail", "failed")):
        raise RuntimeError("Não foi possível consultar as lojas do GestãoClick.")
    lojas = resposta.get("data", resposta.get("dados"))
    if not isinstance(lojas, list) or not lojas or any(
        not isinstance(loja, dict) or not loja.get("id") for loja in lojas
    ):
        raise RuntimeError("Lista de lojas inválida no GestãoClick.")
    return list(dict.fromkeys(str(loja["id"]) for loja in lojas))


def listar_vendas():
    """Consulta explicitamente os três tipos documentados pelo GestãoClick."""
    vendas = []
    ids_vistos = set()
    for loja_id in listar_lojas():
        for tipo in ("produto", "servico", "vendas_balcao"):
            for venda in listar_todos("vendas", {"tipo": tipo, "loja_id": loja_id}):
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
    # A vinculação usa o telefone, mesmo que o nome varie entre vendas.
    texto = str(venda.get("nome_cliente") or "").strip()
    partes = re.fullmatch(r"(.+?)\s*[-–—]\s*([+()0-9 .-]+)", texto)
    if not partes:
        return None
    nome, telefone = partes.group(1).strip(), telefone_normalizado(partes.group(2))
    if not nome or not telefone:
        return None
    return {"nome": nome, "celular": telefone}


def gerar_base_clientes():

    clientes = listar_todos("clientes")

    vendas = listar_vendas()

    # O mesmo contato representa a mesma pessoa, conforme regra da loja.
    # IDs originais continuam como aliases para preservar aniversários salvos.
    clientes_por_id = {str(c["id"]): c for c in clientes if c.get("id") not in (None, "")}
    pais = {cid: cid for cid in clientes_por_id}

    def raiz(cid):
        while pais[cid] != cid:
            pais[cid] = pais[pais[cid]]
            cid = pais[cid]
        return cid

    indice_telefones = {}
    for cid in sorted(clientes_por_id):
        cliente = clientes_por_id[cid]
        for valor in (cliente.get("celular"), cliente.get("telefone")):
            telefone = telefone_normalizado(valor)
            if not telefone:
                continue
            if telefone in indice_telefones:
                r1, r2 = raiz(cid), raiz(indice_telefones[telefone])
                pais[max(r1, r2)] = min(r1, r2)
            else:
                indice_telefones[telefone] = cid
    indice_telefones = {tel: raiz(cid) for tel, cid in indice_telefones.items()}
    consolidados = {}
    for cid in sorted(clientes_por_id):
        destino = raiz(cid)
        if destino not in consolidados:
            consolidados[destino] = {**clientes_por_id[destino], "ids_agrupados": []}
        consolidados[destino]["ids_agrupados"].append(cid)
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

        data_venda = converter_data(venda.get("data"))
        if data_venda is None:
            raise RuntimeError("Venda com data ausente ou inválida no GestãoClick.")
        if data_venda < date(2025, 9, 1):
            continue

        cliente_id = str(venda.get("cliente_id") or "")
        consumidor = consumidor_da_venda(venda)
        if consumidor:
            telefone = consumidor["celular"]
            # Recupera também o ID usado pela versão anterior do painel.
            chave_antiga = chave_consumidor(consumidor["nome"], telefone)
            id_antigo = "consumidor-" + sha256(
                json.dumps(chave_antiga, ensure_ascii=False).encode("utf-8")
            ).hexdigest()
            cliente_id = indice_telefones.get(telefone)
            if cliente_id is None:
                cliente_id = "contato-" + sha256(telefone.encode("utf-8")).hexdigest()
                if cliente_id not in consumidores_avulsos:
                    consumidores_avulsos[cliente_id] = {
                        **consumidor, "id": cliente_id,
                        "origem": "Identificado na venda", "ids_agrupados": []
                    }
            grupo = consolidados.get(cliente_id, consumidores_avulsos.get(cliente_id))
            if id_antigo not in grupo["ids_agrupados"]:
                grupo["ids_agrupados"].append(id_antigo)
        elif cliente_id in clientes_por_id:
            cliente_id = raiz(cliente_id)
        else:
            # Sem contato nem cadastro reconhecido: não atribuir a outra pessoa.
            continue

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

    for cliente in list(consolidados.values()) + list(consumidores_avulsos.values()):

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
            "ids_agrupados": cliente.get("ids_agrupados", [cliente_id]),

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

.paginacao { margin-top: 16px; display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 12px; font-size: 14px; color: #475467; }
.paginacao-acoes { display: flex; gap: 14px; align-items: center; }
.paginacao a { color: #175cd3; text-decoration: none; padding: 8px 10px; border: 1px solid #d0d5dd; border-radius: 8px; background: white; }
.paginacao a:hover { background: #eff4ff; }
.filtro-card { width: 100%; text-align: left; font: inherit; color: inherit; cursor: pointer; }
.filtro-card:hover { border-color: #2563eb; background: #f1f6ff; }
.filtro-card:focus-visible { outline: 3px solid #2563eb; outline-offset: 3px; }

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
{% if sincronizacao %}
<div style="padding:12px 24px;background:#e8f1ff;color:#17427a;font-size:14px">
{% if sincronizacao == 'atualizando' %}Atualizando as compras. Exibindo a última base completa.
{% elif sincronizacao == 'erro' %}A atualização falhou. Exibindo a última base completa; tentaremos novamente.
{% else %}Base sincronizada. Ao abrir o painel, verificamos se há uma atualização pendente.{% endif %}
 <a href="/">Atualizar página</a>
</div>
{% endif %}

<header>

<h1>
SMART ONE CLIENTES
</h1>

<p>
Inteligência comercial conectada ao GestãoClick
</p>

</header>

<div class="container">
<nav class="toolbar" aria-label="Abas">
<a href="/">Painel de clientes</a>
<a href="/whatsapp" {% if aba_whatsapp %}aria-current="page"{% endif %}>WhatsApp</a>
<a href="/aniversarios">Aniversários</a>
</nav>
<section class="card" style="margin-bottom:20px" aria-label="Critérios dos clientes">
<strong>Como os clientes são classificados</strong>
<p>Compras desde 01/09/2025, conforme os registros retornados pelo GestãoClick. Cadastros com o mesmo telefone são agrupados.</p>
<p>Ativo: menos de {{ dias_risco }} dias sem comprar · Em risco: de {{ dias_risco }} a {{ dias_inativo - 1 }} dias · Inativo: {{ dias_inativo }} dias ou mais.</p>
<p>VIP: pelo menos {{ vip_valor }} em compras <strong>e</strong> {{ vip_compras }} compras no histórico disponível, sem prazo definido. Um VIP também pode estar em risco ou inativo.</p>
<small>Os prazos contam desde a última compra. Clientes sem compra registrada ficam como “Sem compra”.</small>
</section>
{% if aba_whatsapp %}
<section class="card" style="margin-bottom:20px">
<h2 style="margin-top:0">Enviar mensagem pelo WhatsApp</h2>
<p>Busque um cliente ou filtre por situação. Escreva a mensagem e clique em “Abrir WhatsApp” ao lado do nome.</p>
<label for="mensagemWhatsApp">Mensagem (opcional)</label>
<textarea id="mensagemWhatsApp" rows="4" maxlength="2000" style="display:block;width:100%;padding:12px;margin:12px 0;font:inherit;border:1px solid #d0d5dd;border-radius:8px" placeholder="Olá! Aqui é da Smart One. Como podemos ajudar?"></textarea>
<small>Você confere e envia a mensagem no WhatsApp. O painel não confirma se o número tem WhatsApp ou se houve envio.</small>
</section>
{% endif %}

<div class="grid">

<button type="button" class="card filtro-card" onclick="abrirClientes('')">

<small>
Clientes cadastrados
</small>

<div class="numero">
{{ total }}
</div>

</button>


<button type="button" class="card filtro-card" onclick="abrirClientes('Ativo')">

<small>
Clientes ativos
</small>

<div class="numero">
{{ ativos }}
</div>

</button>


<button type="button" class="card filtro-card" onclick="abrirClientes('VIP')">

<small>
VIPs
</small>

<div class="numero">
{{ vips }}
</div>

</button>


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

<button type="button" class="card filtro-card" onclick="abrirClientes('Em risco')">

<small>
Clientes em risco
</small>

<div class="numero">
{{ em_risco }}
</div>

</button>


<button type="button" class="card alerta filtro-card" onclick="abrirClientes('Inativo')">

<small>
Clientes inativos
</small>

<div class="numero">
{{ inativos }}
</div>

</button>


<button type="button" class="card filtro-card" onclick="abrirClientes('vip_risco_inativo')">

<small>
VIPs em risco/inativos
</small>

<div class="numero">
{{ vips_reativar }}
</div>

</button>


<div class="card">

<small>
Aniversários próximos 7 dias
</small>

<div class="numero">
{{ proximos_aniversarios }}
</div>

</div>

</div>


<p><a href="/aniversarios" style="display:inline-block;padding:12px 16px;background:#175cd3;color:white;text-decoration:none;border-radius:8px;margin-top:18px">🎂 Abrir aba Aniversários</a></p>
<h2 id="secaoClientes">
Clientes
</h2>


<div class="toolbar">

<input
id="busca"
value="{{ busca_atual|e }}"
placeholder="Buscar cliente ou telefone..."
oninput="agendarBusca()"
>

<select
id="filtroStatus"
onchange="aplicarFiltros()"
>

<option value="" {% if not status_atual %}selected{% endif %}>
Todos
</option>

<option value="VIP" {% if status_atual == "VIP" %}selected{% endif %}>
VIP
</option>

<option value="Ativo" {% if status_atual == "Ativo" %}selected{% endif %}>
Ativo
</option>

<option value="Em risco" {% if status_atual == "Em risco" %}selected{% endif %}>
Em risco
</option>

<option value="Inativo" {% if status_atual == "Inativo" %}selected{% endif %}>
Inativo
</option>
<option value="vip_risco_inativo" {% if status_atual == "vip_risco_inativo" %}selected{% endif %}>VIP em risco/inativo</option>

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
{{ cliente.celular or 'Sem telefone' }}
{% if cliente.whatsapp %}
<br><a class="abrir-whatsapp" href="https://wa.me/55{{ cliente.whatsapp }}" target="_blank" rel="noopener noreferrer" style="display:inline-block;margin-top:8px;color:#087443;font-weight:bold">Abrir WhatsApp</a>
{% else %}
<br><small>Telefone ausente ou inválido</small>
{% endif %}
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
<div class="paginacao" aria-label="Paginação de clientes">
  <span>Mostrando {{ exibindo_inicio }}–{{ exibindo_fim }} de {{ quantidade_filtrada }} clientes</span>
  <div class="paginacao-acoes">
    {% if pagina_atual > 1 %}
    <a href="{{ caminho_lista }}?{{ dict(pagina=pagina_atual-1, status=status_atual, busca=busca_atual)|urlencode }}#secaoClientes">← Anterior</a>
    {% endif %}
    <span>Página {{ pagina_atual }} de {{ total_paginas }}</span>
    {% if pagina_atual < total_paginas %}
    <a href="{{ caminho_lista }}?{{ dict(pagina=pagina_atual+1, status=status_atual, busca=busca_atual)|urlencode }}#secaoClientes">Próxima →</a>
    {% endif %}
  </div>
</div>
</div>


<script>

document.querySelectorAll(".abrir-whatsapp").forEach(link => {
    const destino = link.href;
    link.addEventListener("click", () => {
        const campo = document.getElementById("mensagemWhatsApp");
        const mensagem = campo ? campo.value.trim() : "";
        link.href = destino + (mensagem ? "?text=" + encodeURIComponent(mensagem) : "");
    });
});
let esperaBusca;
function irParaLista(status, busca, pagina) {
    const parametros = new URLSearchParams();
    if (status) parametros.set("status", status);
    if (busca) parametros.set("busca", busca);
    if (pagina > 1) parametros.set("pagina", String(pagina));
    window.location.assign(window.location.pathname + (parametros.toString() ? "?" + parametros.toString() : "") + "#secaoClientes");
}
function abrirClientes(status) { irParaLista(status, "", 1); }
function aplicarFiltros() {
    clearTimeout(esperaBusca);
    irParaLista(document.getElementById("filtroStatus").value,
                document.getElementById("busca").value.trim(), 1);
}
function agendarBusca() {
    clearTimeout(esperaBusca);
    esperaBusca = setTimeout(aplicarFiltros, 450);
}

</script>

</body>

</html>
"""


# =========================================================
# ROTAS
# =========================================================

def sincronizar_clientes():
    try:
        return gerar_base_clientes()
    except Exception:
        app.logger.exception("Falha na sincronização completa das lojas")
        raise


cache_clientes = BaseCache(sincronizar_clientes)


def carregar_clientes():
    return cache_clientes.obter()


def pagina_carregamento(erro=False):
    titulo = "Sincronização temporariamente indisponível" if erro else "Preparando seu painel"
    mensagem = ("Não conseguimos concluir a consulta. Vamos tentar novamente automaticamente."
                if erro else "Estamos reunindo clientes e compras de todas as lojas. A primeira carga pode levar alguns minutos.")
    return render_template_string("""<!doctype html><html lang="pt-BR"><head>
    <meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
    <meta http-equiv="refresh" content="{{ intervalo }}"><title>Smart One Clientes</title>
    <style>body{margin:0;background:#eff4fb;font:18px system-ui;color:#16304e;display:grid;min-height:100vh;place-items:center}
    main{background:white;padding:40px;border-radius:20px;max-width:520px;margin:24px;box-shadow:0 15px 50px #183b6414}
    p{line-height:1.6}small{color:#62748a}a{color:#155acf}</style></head>
    <body><main><small>SMART ONE CLIENTES</small><h1>{{ titulo }}</h1><p>{{ mensagem }}</p>
    <p>Esta página será atualizada automaticamente.</p><a href="/">Verificar agora</a></main></body></html>""",
    titulo=titulo, mensagem=mensagem, intervalo=30 if erro else 5), 500 if erro else 202


@app.route("/")
@app.route("/whatsapp")
def dashboard():
    # HEAD é usado para verificar disponibilidade; não deve consultar o ERP.
    if request.method == "HEAD":
        return "", 200
    try:

        clientes = carregar_clientes()
        locais_aniversarios, _ = carregar_aniversarios_locais()
        clientes = clientes_com_aniversarios(clientes, locais_aniversarios)

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

        # Filtrar e paginar no servidor para evitar milhares de linhas no navegador.
        status_atual = request.args.get("status", "").strip()
        if status_atual not in ("", "Ativo", "Em risco", "Inativo", "VIP", "vip_risco_inativo"):
            status_atual = ""
        busca_atual = request.args.get("busca", "").strip()[:100]
        busca_normalizada = busca_atual.casefold()

        def corresponde(cliente):
            if status_atual == "VIP" and not cliente["vip"]:
                return False
            if status_atual == "vip_risco_inativo":
                if not cliente["vip"] or cliente["status"] not in ("Em risco", "Inativo"):
                    return False
            elif status_atual not in ("", "VIP") and cliente["status"] != status_atual:
                return False
            if busca_normalizada:
                conteudo = str(cliente.get("nome") or "") + " " + str(cliente.get("celular") or "")
                return busca_normalizada in conteudo.casefold()
            return True

        clientes_filtrados = sorted(
            (cliente for cliente in clientes if corresponde(cliente)),
            key=lambda c: (c["vip"], c["total_gasto"]),
            reverse=True
        )
        por_pagina = 80
        quantidade_filtrada = len(clientes_filtrados)
        total_paginas = max(1, (quantidade_filtrada + por_pagina - 1) // por_pagina)
        try:
            pagina_atual = int(request.args.get("pagina", "1"))
        except ValueError:
            pagina_atual = 1
        pagina_atual = max(1, min(pagina_atual, total_paginas))
        indice_inicio = (pagina_atual - 1) * por_pagina
        clientes = clientes_filtrados[indice_inicio:indice_inicio + por_pagina]
        clientes = [
            {**cliente, "whatsapp": telefone_normalizado(cliente.get("celular"))}
            for cliente in clientes
        ]
        exibindo_inicio = indice_inicio + 1 if quantidade_filtrada else 0
        exibindo_fim = indice_inicio + len(clientes)

        return render_template_string(

            HTML,

            sincronizacao=cache_clientes.estado(),
            aba_whatsapp=request.path == "/whatsapp",
            caminho_lista=request.path,
            dias_risco=DIAS_EM_RISCO,
            dias_inativo=DIAS_INATIVO,
            vip_valor=moeda(VIP_VALOR_MINIMO),
            vip_compras=VIP_COMPRAS_MINIMAS,

            clientes=clientes,
            status_atual=status_atual,
            busca_atual=busca_atual,
            pagina_atual=pagina_atual,
            total_paginas=total_paginas,
            quantidade_filtrada=quantidade_filtrada,
            exibindo_inicio=exibindo_inicio,
            exibindo_fim=exibindo_fim,

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

    except BaseCarregando:
        return pagina_carregamento()
    except Exception:
        return pagina_carregamento(erro=True)

# =========================================================
# ANIVERSÁRIOS: dados locais, isolados do GestãoClick
# =========================================================

def token_aniversario():
    senha = app.config["SMARTONE_ADMIN_PASSWORD"]
    return URLSafeTimedSerializer(senha, salt="smartone-aniversarios-form-v1").dumps(
        request.authorization.username if request.authorization else ""
    )


def validar_token_aniversario(token):
    if not token or not isinstance(token, str):
        return False
    try:
        usuario = URLSafeTimedSerializer(
            app.config["SMARTONE_ADMIN_PASSWORD"], salt="smartone-aniversarios-form-v1"
        ).loads(token, max_age=3600)
    except (BadSignature, SignatureExpired):
        return False
    return usuario == app.config["SMARTONE_ADMIN_USER"]


def carregar_aniversarios_locais():
    if not banco_aniversarios_configurado():
        return {}, "Banco de dados não configurado. O cadastro de aniversários está desativado."
    try:
        return listar_aniversarios(), None
    except Exception:
        app.logger.exception("Erro ao carregar aniversários persistentes")
        return {}, "Não foi possível acessar os aniversários salvos. Tente novamente mais tarde."


def clientes_com_aniversarios(clientes, locais):
    resultado = []
    for original in clientes:
        cliente = original.copy()
        nascimento = locais.get(str(cliente["id"]))
        if nascimento is None:
            nascimento = next(
                (locais[cid] for cid in cliente.get("ids_agrupados", []) if cid in locais),
                cliente.get("data_nascimento")
            )
        cliente["data_nascimento"] = nascimento
        cliente["data_nascimento_formatada"] = formatar_data(nascimento)
        cliente["aniversario_hoje"] = aniversario_hoje(nascimento)
        cliente["dias_ate_aniversario"] = dias_ate_aniversario(nascimento)
        resultado.append(cliente)
    return resultado


ANIVERSARIOS_HTML = """
<!doctype html>
<html lang="pt-BR"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Aniversários — Smart One Clientes</title>
<style>
*{box-sizing:border-box}body{font:15px Arial,sans-serif;background:#f5f7fb;color:#101828;margin:0}
header{background:#111827;color:white;padding:22px 30px}
header h1{font-size:22px;margin:0 0 8px}
header a{color:#e5e7eb}
main{max-width:1150px;margin:auto;padding:24px}
.card{background:#fff;border:1px solid #e4e7ec;border-radius:14px;padding:20px;margin-bottom:18px}
form.barra{display:flex;gap:10px;flex-wrap:wrap;align-items:center}
input,select,button{padding:11px;border:1px solid #d0d5dd;border-radius:8px;font:inherit}
input[type="search"]{min-width:240px;flex:1}
button{background:#175cd3;color:white;cursor:pointer}
table{width:100%;border-collapse:collapse;background:white}
th,td{padding:12px;border-bottom:1px solid #e4e7ec;text-align:left}
th{background:#f9fafb}
small,.muted{color:#667085}
.msg{padding:14px;background:#fff7ed;border-radius:8px}
.success{background:#ecfdf3}
.paginacao{display:flex;justify-content:space-between;align-items:center;gap:16px;margin-top:18px;flex-wrap:wrap}
.paginacao a{padding:9px 12px;border:1px solid #d0d5dd;border-radius:8px;text-decoration:none;margin-left:10px}
input[type="date"]{min-width:150px}
a{color:#175cd3}
@media(max-width:760px){.scroll{overflow-x:auto}table{min-width:800px}}
</style></head><body>
<header><h1>SMART ONE CLIENTES · ANIVERSÁRIOS</h1><a href="/">← Voltar ao painel</a></header>
<main>
<div class="card">
<h2>Consultar e cadastrar aniversários</h2>
<p class="muted">Os clientes aparecem abaixo com a data de nascimento ao lado do nome. Cadastre ou edite e clique em Salvar. Os dados ficam apenas no Smart One Clientes.</p>
{% if aviso %}<p class="msg">{{ aviso }}</p>{% endif %}
{% if sucesso %}<p class="msg success">Aniversário salvo com sucesso.</p>{% endif %}
<form method="get" action="/aniversarios" class="barra">
<input type="search" name="busca" value="{{ busca }}" placeholder="Nome ou telefone" maxlength="100">
<label>Mês <select name="mes">
<option value="0" {% if mes == 0 %}selected{% endif %}>Todos os meses</option>
{% for n,nome in meses %}<option value="{{ n }}" {% if mes == n %}selected{% endif %}>{{ nome }}</option>{% endfor %}
</select></label>
<button type="submit">Buscar</button>
<a href="/aniversarios">Limpar</a>
</form>
</div>
<div class="card">
<h2>Clientes para cadastrar aniversários</h2>
<p class="muted">Mostrando {{ exibindo_inicio }}–{{ exibindo_fim }} de {{ total }} clientes. Os clientes sem data cadastrada aparecem primeiro.</p>
<div class="scroll"><table>
<thead><tr><th>Cliente</th><th>Cadastrar / editar aniversário</th><th>Telefone</th><th>WhatsApp</th></tr></thead>
<tbody>{% for cliente in clientes %}
<tr><td><strong>{{ cliente.nome }}</strong></td>
<td><form method="post" action="/aniversarios/salvar" class="barra">
<input type="hidden" name="csrf_token" value="{{ csrf_token }}">
<input type="hidden" name="cliente_id" value="{{ cliente.id }}">
<input type="hidden" name="busca" value="{{ busca }}">
<input type="hidden" name="mes" value="{{ mes }}">
<input type="hidden" name="pagina" value="{{ pagina }}">
<input type="date" name="data_nascimento" value="{{ cliente.data_nascimento_iso }}" required min="1900-01-01" max="{{ hoje }}" {% if not pode_salvar %}disabled{% endif %}>
<button type="submit" {% if not pode_salvar %}disabled{% endif %}>Salvar</button>
</form></td>
<td>{{ cliente.celular }}</td>
<td>{% if cliente.whatsapp %}<a href="https://wa.me/55{{ cliente.whatsapp }}" target="_blank" rel="noopener noreferrer">Enviar parabéns</a>{% else %}—{% endif %}</td>
</tr>{% else %}<tr><td colspan="4">Nenhum cliente encontrado.</td></tr>{% endfor %}
</tbody></table></div>
<div class="paginacao">
  <span>Página {{ pagina }} de {{ total_paginas }}</span>
  <div>
  {% if pagina > 1 %}
    <a href="{{ url_for('tela_aniversarios', busca=busca, mes=mes, pagina=pagina-1) }}">← Anterior</a>
  {% endif %}
  {% if pagina < total_paginas %}
    <a href="{{ url_for('tela_aniversarios', busca=busca, mes=mes, pagina=pagina+1) }}">Próxima →</a>
  {% endif %}
  </div>
</div>
</div>
</main></body></html>
"""


@app.route("/aniversarios")
def tela_aniversarios():
    try:
        clientes = carregar_clientes()
        locais, aviso = carregar_aniversarios_locais()
        clientes = clientes_com_aniversarios(clientes, locais)
        busca = request.args.get("busca", "").strip()[:100]
        try:
            mes = int(request.args.get("mes", "0"))
        except ValueError:
            mes = 0
        mes = mes if 0 <= mes <= 12 else 0
        busca_comparavel = busca.casefold()
        selecionados = [
            cliente for cliente in clientes
            if (not busca_comparavel or busca_comparavel in (
                str(cliente.get("nome") or "") + " " +
                str(cliente.get("celular") or "")
            ).casefold())
            and (mes == 0 or (
                converter_data(cliente.get("data_nascimento")) is not None
                and converter_data(cliente["data_nascimento"]).month == mes
            ))
        ]
        # Clientes sem data primeiro, depois ordem alfabética.
        selecionados.sort(key=lambda cliente: (
            converter_data(cliente.get("data_nascimento")) is not None,
            str(cliente.get("nome") or "").casefold()
        ))
        por_pagina = 60
        total = len(selecionados)
        total_paginas = max(1, (total + por_pagina - 1) // por_pagina)
        try:
            pagina = int(request.args.get("pagina", "1"))
        except ValueError:
            pagina = 1
        pagina = max(1, min(pagina, total_paginas))
        inicio = (pagina - 1) * por_pagina
        exibindo_inicio = inicio + 1 if total else 0
        exibindo_fim = min(total, inicio + por_pagina)
        exibidos = []
        for cliente in selecionados[inicio:inicio + por_pagina]:
            data = converter_data(cliente.get("data_nascimento"))
            telefone = re.sub(r"\D", "", str(cliente.get("celular") or ""))
            if telefone.startswith("55") and len(telefone) in (12, 13):
                telefone = telefone[2:]
            exibidos.append({
                **cliente,
                "data_nascimento_iso": data.isoformat() if data else "",
                "whatsapp": telefone if len(telefone) in (10, 11) else ""
            })
        return render_template_string(
            ANIVERSARIOS_HTML, clientes=exibidos, total=total, pagina=pagina,
            total_paginas=total_paginas, exibindo_inicio=exibindo_inicio,
            exibindo_fim=exibindo_fim, busca=busca, mes=mes, csrf_token=token_aniversario(),
            pode_salvar=banco_aniversarios_configurado() and aviso is None,
            aviso=aviso, sucesso=request.args.get("salvo") == "1",
            hoje=hoje_local().isoformat(),
            meses=list(enumerate(("Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho",
                                   "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro"), 1))
        )
    except BaseCarregando:
        return pagina_carregamento()
    except Exception:
        app.logger.exception("Falha na aba Aniversários")
        return pagina_carregamento(erro=True)


@app.route("/aniversarios/salvar", methods=["POST"])
def gravar_aniversario():
    if not validar_token_aniversario(request.form.get("csrf_token")):
        return jsonify({"erro": "Formulário expirado ou inválido. Atualize a página."}), 403
    if not banco_aniversarios_configurado():
        return jsonify({"erro": "O banco de dados persistente ainda não está configurado."}), 503
    cliente_id = (request.form.get("cliente_id") or "").strip()
    nascimento = converter_data(request.form.get("data_nascimento"))
    if not nascimento or nascimento.year < 1900 or nascimento > hoje_local():
        return jsonify({"erro": "Data de nascimento inválida."}), 400
    try:
        clientes = carregar_clientes()
        if not any(str(cliente.get("id")) == cliente_id for cliente in clientes):
            return jsonify({"erro": "Cliente não encontrado."}), 404
        salvar_aniversario(cliente_id, nascimento)
    except BaseCarregando:
        return jsonify({"erro": "Aguarde a sincronização e tente novamente."}), 503
    except Exception:
        app.logger.exception("Falha ao salvar aniversário")
        return jsonify({"erro": "Falha ao salvar aniversário. Tente novamente."}), 503
    busca = (request.form.get("busca") or "").strip()[:100]
    mes = (request.form.get("mes") or "").strip()
    pagina = (request.form.get("pagina") or "1").strip()
    return redirect(url_for("tela_aniversarios", busca=busca, mes=mes, pagina=pagina, salvo=1), code=303)


@app.route("/health")
def health():
    return jsonify({"status": "ok", "app": "Smart One Clientes"})


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
