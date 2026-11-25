"""Cadastro interno de parceiros, indicações e vendas atribuídas pela equipe."""
import re
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import psycopg
from flask import send_file, abort, redirect, render_template, request, url_for
from psycopg.rows import dict_row

from aniversarios_store import conexao


def preparar(conn):
    conn.execute("SELECT pg_advisory_xact_lock(17340522)")
    conn.execute("""CREATE TABLE IF NOT EXISTS smartone_parceiros (
        id BIGSERIAL PRIMARY KEY, nome TEXT NOT NULL, contato TEXT NOT NULL)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS smartone_indicacoes (
        id UUID PRIMARY KEY, parceiro BIGINT NOT NULL REFERENCES smartone_parceiros,
        cliente TEXT NOT NULL, contato TEXT NOT NULL)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS smartone_vendas_indicadas (
        referencia TEXT PRIMARY KEY, indicacao UUID NOT NULL REFERENCES smartone_indicacoes,
        data DATE NOT NULL, valor NUMERIC(14,2) NOT NULL CHECK (valor > 0))""")

    conn.execute("""ALTER TABLE smartone_vendas_indicadas ADD COLUMN IF NOT EXISTS
        servicos NUMERIC(14,2) CHECK (servicos BETWEEN 0 AND valor)""")
    conn.execute("""ALTER TABLE smartone_vendas_indicadas ADD COLUMN IF NOT EXISTS
        comissao NUMERIC(14,2) GENERATED ALWAYS AS
        (round(servicos * 0.05, 2) + round((valor - servicos) * 0.03, 2)) STORED""")


def valores_venda():
    valores = []
    for nome in ('servicos', 'produtos'):
        bruto = campo(nome, 16, False).replace(',', '.') or '0'
        if not re.fullmatch(r'\d{1,12}(?:\.\d{1,2})?', bruto):
            raise ValueError('Informe os valores com até duas casas decimais.')
        valores.append(Decimal(bruto))
    servicos, produtos = valores
    total = servicos + produtos
    if not 0 < total < 10**12:
        raise ValueError('Confira o total de serviços e produtos.')
    return servicos, total


def campo(nome, limite, obrigatorio=True):
    valor = request.form.get(nome, '').strip()
    if (obrigatorio and not valor) or len(valor) > limite:
        raise ValueError('Confira o preenchimento do campo ' + nome + '.')
    return valor


def gravar(conn, hoje):
    acao = campo('acao', 20)
    if acao == 'parceiro':
        nome = campo('nome', 120)
        contato = campo('contato', 80, False)
        existente = conn.execute(
            "SELECT id FROM smartone_parceiros WHERE lower(nome)=lower(%s) AND contato=%s",
            (nome, contato)).fetchone()
        if existente:
            return existente[0]
        return conn.execute("INSERT INTO smartone_parceiros(nome, contato) VALUES (%s,%s) RETURNING id",
                            (nome, contato)).fetchone()[0]
    if acao == 'indicacao':
        parceiro = int(campo('parceiro', 18))
        conn.execute("""INSERT INTO smartone_indicacoes(id, parceiro, cliente, contato)
                     VALUES (%s,%s,%s,%s) ON CONFLICT (id) DO NOTHING""",
                     (UUID(campo('id', 36)), parceiro, campo('cliente', 120), campo('contato', 80, False)))
        return parceiro
    if acao not in ('venda', 'classificar'):
        abort(400)
    indicacao = UUID(campo('indicacao', 36))
    referencia = campo('referencia', 80).upper()
    servicos, total = valores_venda()
    if acao == 'classificar':
        atualizado = conn.execute("""UPDATE smartone_vendas_indicadas SET servicos=%s
            WHERE referencia=%s AND indicacao=%s AND valor=%s AND servicos IS NULL""",
            (servicos, referencia, indicacao, total))
        if atualizado.rowcount != 1:
            raise ValueError('Confira o total original e se a venda ainda aguarda classificação.')
        return conn.execute('SELECT parceiro FROM smartone_indicacoes WHERE id=%s', (indicacao,)).fetchone()[0]
    dia = date.fromisoformat(campo('data', 10))
    if dia > hoje:
        raise ValueError('A data da venda deve ser hoje ou anterior.')
    conn.execute("""INSERT INTO smartone_vendas_indicadas(referencia, indicacao, data, valor, servicos)
                 VALUES (%s,%s,%s,%s,%s)""", (referencia, indicacao, dia, total, servicos))
    return conn.execute('SELECT parceiro FROM smartone_indicacoes WHERE id=%s', (indicacao,)).fetchone()[0]


def registrar_parceiros(app, token, validar, moeda):

    @app.route('/parceiros', methods=['GET', 'POST'])
    def parceiros():
        hoje = datetime.now(ZoneInfo('America/Sao_Paulo')).date()
        selecionado = request.args.get('parceiro', type=int)
        resposta = dict(parceiros=[], indicacoes=[], selecionado=None, vendas={}, hoje=hoje,
                        csrf=token(), moeda=moeda, novo_id=uuid4(), erro=None)
        status = 200
        if request.method == 'POST' and not validar(request.form.get('csrf')):
            abort(403)
        try:
            with conexao() as conn:
                preparar(conn)
                if request.method == 'POST':
                    try:
                        with conn.transaction():
                            selecionado = gravar(conn, hoje)
                        return redirect(url_for('parceiros', parceiro=selecionado), code=303)
                    except psycopg.errors.UniqueViolation:
                        resposta['erro'] = 'Já existe uma venda com esse número.'
                    except (ValueError, psycopg.errors.ForeignKeyViolation) as exc:
                        resposta['erro'] = str(exc) if type(exc) is ValueError and str(exc).startswith(('Confira', 'Informe', 'A data')) else 'Confira os dados informados.'
                    status = 400
                with conn.cursor(row_factory=dict_row) as cur:
                    cur.execute("""SELECT p.id,p.nome,p.contato, count(DISTINCT i.id) AS indicacoes,
                                count(v.referencia) AS vendas, coalesce(sum(v.valor),0) AS total,
                                coalesce(sum(v.comissao),0) AS comissao,
                                count(v.referencia) FILTER (WHERE v.servicos IS NULL) AS pendentes
                                FROM smartone_parceiros p LEFT JOIN smartone_indicacoes i ON i.parceiro=p.id
                                LEFT JOIN smartone_vendas_indicadas v ON v.indicacao=i.id
                                GROUP BY p.id ORDER BY total DESC,p.nome,p.id""")
                    resposta['parceiros'] = cur.fetchall()
                    resposta['selecionado'] = next((p for p in resposta['parceiros'] if p['id']==selecionado), None)
                    if resposta['selecionado']:
                        cur.execute('SELECT * FROM smartone_indicacoes WHERE parceiro=%s ORDER BY cliente,id', (selecionado,))
                        resposta['indicacoes'] = cur.fetchall()
                        cur.execute("""SELECT v.* FROM smartone_vendas_indicadas v JOIN smartone_indicacoes i
                                    ON i.id=v.indicacao WHERE i.parceiro=%s ORDER BY v.data DESC,v.referencia""", (selecionado,))
                        for venda in cur:
                            resposta['vendas'].setdefault(venda['indicacao'], []).append(venda)
        except (psycopg.Error, RuntimeError):
            app.logger.exception('Banco de parceiros indisponível')
            resposta['erro'] = 'Não foi possível acessar os cadastros. Tente novamente em instantes.'
            status = 503
        return render_template('parceiros.html', **resposta), status

    @app.get('/parceiros/<int:parceiro>/relatorio.pdf')
    def relatorio_parceiro(parceiro):
        from relatorio_parceiro import gerar_pdf
        mes = request.args.get('mes', '')
        inicio = fim = None
        if mes:
            try:
                inicio = date.fromisoformat(mes + '-01')
                if inicio.strftime('%Y-%m') != mes:
                    raise ValueError
                fim = date(inicio.year + (inicio.month == 12), inicio.month % 12 + 1, 1)
            except ValueError:
                abort(400, 'Mês inválido.')
        with conexao() as conn:
            preparar(conn)
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute('SELECT nome FROM smartone_parceiros WHERE id=%s', (parceiro,))
                cadastro = cur.fetchone()
                if not cadastro:
                    abort(404)
                sql = """SELECT v.referencia,v.data,v.valor,v.servicos,v.comissao
                         FROM smartone_vendas_indicadas v JOIN smartone_indicacoes i
                         ON i.id=v.indicacao WHERE i.parceiro=%s"""
                args = [parceiro]
                if inicio:
                    sql += ' AND v.data >= %s AND v.data < %s'
                    args.extend((inicio, fim))
                cur.execute(sql + ' ORDER BY v.data,v.referencia', args)
                vendas = cur.fetchall()
        pdf = gerar_pdf(cadastro['nome'], inicio, vendas, moeda)
        return send_file(pdf, mimetype='application/pdf', as_attachment=True,
                         download_name=f'Smart-One-comissoes-{parceiro}-{mes or "completo"}.pdf')
