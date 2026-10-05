req = urllib.request.Request(
    "https://gestaoclick.com/api/clientes",
    headers={
        "access-token": access,
        "secret-access-token": secret,
        "Accept": "application/json"
    }
)

try:
    with urllib.request.urlopen(req, timeout=30) as resposta:
        dados = json.loads(resposta.read().decode("utf-8"))
        return jsonify({
            "conexao": "sucesso",
            "gestaoclick": dados
        })
except Exception as erro:
    return jsonify({
        "conexao": "erro",
        "detalhes": str(erro)
    }), 500
