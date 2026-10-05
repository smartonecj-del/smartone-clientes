import os 
import urllib.request 
import json 
from flask import Flask, jsonify
app = Flask(__name__)
@app.route("/") def home(): 
    return jsonify({ 
        "status": "online", 
        "sistema": "Smart One Clientes", 
        "versao": "V1" })
@app.route("/health") 
def health(): 
    return jsonify({"status": "ok"})
@app.route("/teste-gestaoclick") 
def teste_gestaoclick(): 
    access = os.getenv("GESTAOCLICK_ACCESS_TOKEN") 
    secret = os.getenv("GESTAOCLICK_SECRET_ACCESS_TOKEN")
 
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
if __name__ == "__main__": app.run(host="0.0.0.0", port=10000)

