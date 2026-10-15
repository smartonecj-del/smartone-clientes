"""Agenda interna: não cria nem altera ordens de serviço no GestãoClick."""
import re
from datetime import datetime, date, time, timedelta
from uuid import UUID, uuid4
from urllib.parse import urlencode
from zoneinfo import ZoneInfo
from flask import Blueprint, current_app, request, render_template, redirect, url_for, jsonify
from itsdangerous import URLSafeTimedSerializer, BadData
from aniversarios_store import configurado
import agenda_store as store

STATUS = ('Agendado', 'Recebido', 'Em diagnóstico', 'Aguardando aprovação',
          'Aguardando peça', 'Em execução', 'Pronto', 'Entregue', 'Cancelado')
TZ = ZoneInfo('America/Sao_Paulo')


def validar(form):
    limites = {'nome': 150, 'telefone': 25, 'aparelho': 150, 'servico': 200,
               'responsavel': 100, 'os': 60, 'observacoes': 2000, 'cliente_id': 200}
    dados = {}
    for campo, limite in limites.items():
        valor = str(form.get(campo, '')).strip()
        if len(valor) > limite:
            raise ValueError('Campo muito longo: ' + campo)
        dados[campo] = valor
    if not all(dados[c] for c in ('nome', 'telefone', 'aparelho', 'servico', 'responsavel')):
        raise ValueError('Preencha cliente, WhatsApp, aparelho, serviço e responsável.')
    telefone = re.sub(r'\D', '', dados['telefone'])
    if len(telefone) in (12, 13) and telefone.startswith('55'):
        telefone = telefone[2:]
    if len(telefone) not in (10, 11):
        raise ValueError('Informe o WhatsApp com DDD.')
    dados['telefone'] = telefone
    for campo in ('inicio', 'entrega'):
        try:
            dt = datetime.strptime(form.get(campo, ''), '%Y-%m-%dT%H:%M')
            if not 2000 <= dt.year <= 2100:
                raise ValueError()
            dados[campo] = dt.isoformat(timespec='minutes')
        except (ValueError, TypeError):
            raise ValueError('Informe datas e horários válidos para agendamento e entrega.')
    if dados['entrega'] < dados['inicio']:
        raise ValueError('A entrega não pode ser anterior ao agendamento.')
    dados['status'] = form.get('status', '')
    if dados['status'] not in STATUS:
        raise ValueError('Selecione um status válido.')
    return dados


def registrar_agenda(app, carregar_clientes):
    bp = Blueprint('agenda', __name__)

    def serializer():
        return URLSafeTimedSerializer(current_app.config['SMARTONE_ADMIN_PASSWORD'], salt='agenda-form-v1')

    def token(identificador, versao):
        return serializer().dumps({'id': identificador, 'versao': versao,
                                   'user': current_app.config['SMARTONE_ADMIN_USER']})

    def contexto_form(dados=None, erro=None, codigo=200):
        dados = dados or {}
        identificador = str(dados.get('id') or uuid4())
        versao = int(dados.get('versao') or 0)
        return render_template('agenda_form.html', dados=dados, estados=STATUS,
                               csrf=token(identificador, versao), erro=erro,
                               editando=versao > 0, configurado=configurado()), codigo

    @bp.get('/agenda')
    def index():
        agora = datetime.now(TZ).replace(tzinfo=None)
        try:
            dia = date.fromisoformat(request.args.get('data', agora.date().isoformat()))
            if not 2000 <= dia.year <= 2100:
                raise ValueError()
        except ValueError:
            dia = agora.date()
        visao = request.args.get('visao', 'dia')
        visao = visao if visao in ('dia', 'semana', 'atrasados') else 'dia'
        inicio = dia - timedelta(days=dia.weekday()) if visao == 'semana' else dia
        fim = inicio + timedelta(days=7 if visao == 'semana' else 1)
        rows, total, atrasados, erro = [], 0, 0, None
        try:
            rows, total, atrasados = store.consultar(datetime.combine(inicio, time()), datetime.combine(fim, time()), agora, visao == 'atrasados')
        except Exception:
            current_app.logger.exception('Falha ao consultar agenda')
            erro = 'Não foi possível acessar a agenda. Verifique o banco de dados e tente novamente.'
        busca = request.args.get('busca', '').strip()[:100]
        status = request.args.get('status', '')
        for row in rows:
            row['atrasado'] = row['status'] not in ('Entregue', 'Cancelado') and datetime.fromisoformat(row['entrega']) < agora
            row['inicio_label'] = datetime.fromisoformat(row['inicio']).strftime('%d/%m/%Y às %H:%M')
            row['entrega_label'] = datetime.fromisoformat(row['entrega']).strftime('%d/%m/%Y às %H:%M')
            if row['status'] == 'Pronto':
                mensagem = f"Olá, {row['nome']}! Aqui é da Smart One. Seu {row['aparelho']} está pronto para retirada!"
            elif row['status'] == 'Agendado':
                mensagem = f"Olá, {row['nome']}! Aqui é da Smart One. Seu agendamento de {row['servico']} para {row['aparelho']} está marcado para {row['inicio_label']}. Podemos confirmar?"
            else:
                mensagem = f"Olá, {row['nome']}! Aqui é da Smart One. Estamos entrando em contato sobre o serviço de {row['servico']} do seu {row['aparelho']}."
            row['whatsapp'] = 'https://wa.me/55' + row['telefone'] + '?' + urlencode({'text': mensagem})
        rows = [r for r in rows if (not status or r['status'] == status) and
                (not busca or busca.casefold() in ' '.join(str(r.get(k, '')) for k in ('nome','telefone','aparelho','servico','os','responsavel')).casefold())]
        return render_template('agenda.html', registros=rows, total=total, atrasados=atrasados,
                               erro=erro, dia=dia.isoformat(), visao=visao, busca=busca, status=status,
                               estados=STATUS, inicio=inicio.strftime('%d/%m'), ultimo=(fim-timedelta(days=1)).strftime('%d/%m'),
                               anterior=(inicio-timedelta(days=7 if visao=='semana' else 1)).isoformat(),
                               proximo=fim.isoformat(), hoje=agora.date().isoformat(), salvo=request.args.get('salvo')=='1'), 503 if erro else 200

    @bp.get('/agenda/novo')
    def novo():
        return contexto_form({'inicio': datetime.now(TZ).strftime('%Y-%m-%dT%H:%M'), 'status': 'Agendado'})

    @bp.get('/agenda/<uuid:identificador>/editar')
    def editar(identificador):
        try:
            dados = store.obter(identificador)
        except Exception:
            current_app.logger.exception('Falha ao abrir serviço')
            return 'Não foi possível acessar o serviço. Tente novamente.', 503
        if not dados:
            return 'Serviço não encontrado.', 404
        return contexto_form(dados)

    @bp.post('/agenda/salvar')
    def salvar():
        try:
            assinatura = serializer().loads(request.form.get('csrf', ''), max_age=7200)
            if assinatura['user'] != current_app.config['SMARTONE_ADMIN_USER']:
                raise ValueError()
            identificador, versao = str(UUID(assinatura['id'])), int(assinatura['versao'])
        except (BadData, ValueError, KeyError, TypeError):
            return 'Formulário expirado ou inválido. Reabra a agenda.', 403
        dados = dict(request.form, id=identificador, versao=versao)
        try:
            validado = validar(request.form)
            store.salvar(identificador, validado, versao)
        except ValueError as e:
            return contexto_form(dados, str(e), 400)
        except Exception:
            current_app.logger.exception('Falha ao salvar agenda')
            return contexto_form(dados, 'Não foi possível salvar. Seus dados continuam no formulário. Tente novamente.', 503)
        return redirect(url_for('agenda.index', data=validado['inicio'][:10], salvo=1), code=303)

    @bp.get('/agenda/clientes')
    def clientes():
        busca = request.args.get('q', '').strip()[:100].casefold()
        if len(busca) < 2:
            return jsonify([])
        try:
            encontrados = []
            digitos = re.sub(r'\D', '', busca)
            for c in carregar_clientes():
                nome, tel = str(c.get('nome') or ''), str(c.get('celular') or '')
                if busca in nome.casefold() or (len(digitos) >= 3 and digitos in re.sub(r'\D', '', tel)):
                    encontrados.append({'id': str(c.get('id') or ''), 'nome': nome, 'telefone': tel})
                    if len(encontrados) == 15:
                        break
            return jsonify(encontrados)
        except Exception:
            return jsonify({'erro': 'Base de clientes indisponível. Você pode preencher os dados manualmente.'}), 503

    app.register_blueprint(bp)
