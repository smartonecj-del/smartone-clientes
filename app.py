from flask import Flask, jsonify
app = Flask(__name__)
@app.route("/") 
def home(): 
  return jsonify({ "status": "online", "sistema": "Smart One Clientes", "versao": "V1" })
@app.route("/health") 
def health(): 
  return jsonify({"status": "ok"})
if __name__ == "__main__": 
  app.run(host="0.0.0.0", port=10000)
