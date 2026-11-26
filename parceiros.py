from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from io import BytesIO
import os
import re
from zoneinfo import ZoneInfo

import psycopg
from psycopg.rows import dict_row
from flask import abort, redirect, render_template, request, send_file, url_for
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from xml.sax.saxutils import escape
from aniversarios_store import conexao as conexao_padrao


def conexao():
    url = os.getenv("PARCEIROS_DATABASE_URL", "").strip()
    if url:
        return psycopg.connect(url, connect_timeout=8)
    return conexao_padrao()


def preparar(conn):
    conn.execute("SELECT pg_advisory_xact_lock(17340522)")
    conn.execute("""CREATE TABLE IF NOT EXISTS smartone_parceiros (
        id BIGSERIAL PRIMARY KEY, nome VARCHAR(100) NOT NULL, contato VARCHAR(80) NOT NULL)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS smartone_indicacoes (
        referencia VARCHAR(64) PRIMARY KEY, parceiro BIGINT NOT NULL REFERENCES smartone_parceiros(id),
        dia DATE NOT NULL, servicos NUMERIC(12,2) NOT NULL CHECK(servicos >= 0),
        produtos NUMERIC(12,2) NOT NULL CHECK(produtos >= 0), CHECK(servicos + produtos > 0))""")


def valor(texto):
    texto = (texto or "0").strip().replace(",", ".")
    if not re.fullmatch(r"[0-9]{1,10}(?:\.[0-9]{1,2})?", texto):
        raise ValueError("Informe um valor positivo ou zero, com até duas casas decimais.")
    return Decimal(texto)


def reais(n):
    return "R$ " + f"{n:,.2f}".translate(str.maketrans(",.", ".,"))


def resumo(cur, parceiro, inicio):
    fim = (inicio.replace(day=28) + timedelta(days=4)).replace(day=1)
    cur.execute("""SELECT referencia, dia, servicos, produtos FROM smartone_indicacoes
        WHERE parceiro=%s AND dia >= %s AND dia < %s ORDER BY dia, referencia""", (parceiro, inicio, fim))
    linhas = cur.fetchall()
    for v in linhas:
        v["total"] = v["servicos"] + v["produtos"]
        v["comissao"] = (v["servicos"] * Decimal(".05") + v["produtos"] * Decimal(".03")).quantize(
            Decimal(".01"), rounding=ROUND_HALF_UP)
    return linhas, sum(v["total"] for v in linhas), sum(v["comissao"] for v in linhas)


def pdf(nome, mes, vendas, total, comissao):
    arquivo = BytesIO()
    doc = SimpleDocTemplate(arquivo, pagesize=A4, leftMargin=30, rightMargin=30)
    estilo = getSampleStyleSheet()
    corpo = [Paragraph("Smart One | Relatório de comissão", estilo["Title"]),
             Paragraph(escape(nome) + " · " + mes.strftime("%m/%Y"), estilo["Heading2"]),
             Paragraph(f"{len(vendas)} venda(s) · Total vendido: {reais(total)}", estilo["Normal"]),
             Paragraph("Comissão apurada: " + reais(comissao), estilo["Heading3"]),
             Paragraph("5% sobre serviços e 3% sobre produtos.", estilo["Normal"]), Spacer(1, 15)]
    dados = [["Data", "Venda", "Total vendido", "Serviços", "Produtos", "Comissão"]]
    for v in vendas:
        dados.append([v["dia"].strftime("%d/%m/%Y"), Paragraph(escape(v["referencia"]), estilo["Normal"]),
                      *[reais(v[k]) for k in ("total", "servicos", "produtos", "comissao")]])
    tabela = Table(dados, colWidths=[60, 90, 95, 95, 95, 95], repeatRows=1)
    tabela.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,0), colors.HexColor("#154989")),
                                ("TEXTCOLOR",(0,0),(-1,0),colors.white), ("FONTSIZE",(0,0),(-1,-1),8),
                                ("ROWBACKGROUNDS",(0,1),(-1,-1),[colors.white,colors.HexColor("#eff4fa")])]))
    doc.build(corpo + [tabela])
    arquivo.seek(0)
    return arquivo


def registrar_parceiros(app, token, validar):
    @app.route("/parceiros", methods=["GET", "POST"])
    def parceiros():
        hoje = datetime.now(ZoneInfo("America/Sao_Paulo")).date()
        dados = request.form if request.method == "POST" else request.args
        if request.method == "POST" and not validar(dados.get("csrf_token")):
            abort(403)
        try:
            mes = date.fromisoformat(dados.get("mes", hoje.strftime("%Y-%m")) + "-01")
            if not 1900 <= mes.year <= 9998:
                raise ValueError
            pid = int(dados["parceiro"]) if dados.get("parceiro") else None
            if pid is not None and not 0 < pid < 2**63:
                raise ValueError
        except ValueError:
            abort(400, "Parceiro ou mês inválido.")
        try:
            with conexao() as conn, conn.cursor(row_factory=dict_row) as cur:
                preparar(conn)
                if request.method == "POST":
                    if dados.get("acao") == "cadastrar":
                        nome, contato = (dados.get(k, "").strip() for k in ("nome", "contato"))
                        if not nome or len(nome) > 100 or len(contato) > 80:
                            raise ValueError("Confira o nome e o contato do parceiro.")
                        cur.execute("INSERT INTO smartone_parceiros(nome,contato) VALUES (%s,%s) RETURNING id",
                                    (nome, contato))
                        pid = cur.fetchone()["id"]
                    elif dados.get("acao") == "venda":
                        ref = dados.get("referencia", "").strip().upper()
                        serv, prod = (valor(dados.get(k)) for k in ("servicos", "produtos"))
                        try:
                            dia = date.fromisoformat(dados.get("dia", ""))
                        except ValueError:
                            raise ValueError("Informe a data da venda.") from None
                        if not ref or len(ref) > 64 or dia > hoje or dia.year < 1900 or serv + prod == 0:
                            raise ValueError("Confira a referência, a data e os valores da venda.")
                        cur.execute("""INSERT INTO smartone_indicacoes(referencia,parceiro,dia,servicos,produtos)
                            VALUES (%s,%s,%s,%s,%s)""", (ref, pid, dia, serv, prod))
                        mes = dia.replace(day=1)
                    else:
                        abort(400)
                    return redirect(url_for("parceiros", parceiro=pid, mes=mes.strftime("%Y-%m")), 303)
                cur.execute("SELECT id,nome,contato FROM smartone_parceiros ORDER BY lower(nome),id")
                lista = cur.fetchall()
                atual = next((p for p in lista if p["id"] == pid), None) if pid else next(iter(lista), None)
                if pid and not atual:
                    abort(404)
                vendas, total, comissao = resumo(cur, atual["id"], mes) if atual else ([], 0, 0)
        except psycopg.errors.UniqueViolation:
            return render_template("parceiros.html", erro="Essa venda já foi vinculada a um parceiro."), 409
        except (ValueError, psycopg.errors.ForeignKeyViolation, psycopg.errors.NotNullViolation) as exc:
            msg = str(exc) if isinstance(exc, ValueError) else "Selecione um parceiro válido."
            return render_template("parceiros.html", erro=msg), 400
        except (psycopg.Error, RuntimeError):
            app.logger.warning("Banco de parceiros indisponível.")
            return render_template("parceiros.html", erro="Banco de parceiros indisponível. Tente novamente."), 503
        if request.args.get("formato") == "pdf" and atual:
            return send_file(pdf(atual["nome"], mes, vendas, total, comissao), mimetype="application/pdf",
                             as_attachment=True, download_name=f"Comissao-{atual['id']}-{mes:%Y-%m}.pdf")
        return render_template("parceiros.html", lista=lista, atual=atual, mes=mes.strftime("%Y-%m"),
                               hoje=hoje, vendas=vendas, total=total, comissao=comissao, reais=reais, token=token())
