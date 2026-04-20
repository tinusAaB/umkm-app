from flask import Flask, render_template, request, jsonify
import json, os, datetime

app = Flask(__name__)
DATA_FILE = "data.json"

def load_data():
    if not os.path.exists(DATA_FILE):
        return {
            "produk": [
                {"id": 1, "nama": "E-book Panduan Bisnis", "harga": 85000, "stok": 50, "modal": 10000},
                {"id": 2, "nama": "Template Excel Keuangan", "harga": 45000, "stok": 30, "modal": 5000},
                {"id": 3, "nama": "Software Kasir Lite", "harga": 150000, "stok": 99, "modal": 20000}
            ],
            "transaksi": [],
            "invoice": []
        }
    with open(DATA_FILE) as f:
        return json.load(f)

def save_data(data):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=2)

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/produk", methods=["GET"])
def get_produk():
    data = load_data()
    return jsonify(data["produk"])

@app.route("/api/produk", methods=["POST"])
def add_produk():
    data = load_data()
    body = request.json
    new_id = max([p["id"] for p in data["produk"]], default=0) + 1
    produk = {"id": new_id, "nama": body["nama"], "harga": int(body["harga"]), "stok": int(body["stok"]), "modal": int(body["modal"])}
    data["produk"].append(produk)
    save_data(data)
    return jsonify(produk)

@app.route("/api/produk/<int:pid>", methods=["DELETE"])
def del_produk(pid):
    data = load_data()
    data["produk"] = [p for p in data["produk"] if p["id"] != pid]
    save_data(data)
    return jsonify({"ok": True})

@app.route("/api/transaksi", methods=["GET"])
def get_transaksi():
    data = load_data()
    return jsonify(data["transaksi"])

@app.route("/api/transaksi", methods=["POST"])
def add_transaksi():
    data = load_data()
    body = request.json
    items = body["items"]
    total = 0
    for item in items:
        produk = next((p for p in data["produk"] if p["id"] == item["id"]), None)
        if produk:
            produk["stok"] = max(0, produk["stok"] - item["qty"])
            total += produk["harga"] * item["qty"]
    trx = {
        "id": len(data["transaksi"]) + 1,
        "tanggal": datetime.datetime.now().strftime("%d/%m/%Y %H:%M"),
        "items": items,
        "total": total,
        "pelanggan": body.get("pelanggan", "Umum")
    }
    data["transaksi"].append(trx)
    save_data(data)
    return jsonify(trx)

@app.route("/api/invoice", methods=["GET"])
def get_invoice():
    data = load_data()
    return jsonify(data["invoice"])

@app.route("/api/invoice", methods=["POST"])
def add_invoice():
    data = load_data()
    body = request.json
    inv = {
        "id": len(data["invoice"]) + 1,
        "nomor": f"INV-{datetime.datetime.now().strftime('%Y%m%d')}-{len(data['invoice'])+1:03d}",
        "pelanggan": body["pelanggan"],
        "produk": body["produk"],
        "jumlah": int(body["jumlah"]),
        "harga": int(body["harga"]),
        "total": int(body["jumlah"]) * int(body["harga"]),
        "tanggal": datetime.datetime.now().strftime("%d/%m/%Y"),
        "jatuh_tempo": body["jatuh_tempo"],
        "status": "Belum Bayar"
    }
    data["invoice"].append(inv)
    save_data(data)
    return jsonify(inv)

@app.route("/api/invoice/<int:iid>/lunas", methods=["POST"])
def lunas_invoice(iid):
    data = load_data()
    for inv in data["invoice"]:
        if inv["id"] == iid:
            inv["status"] = "Lunas"
    save_data(data)
    return jsonify({"ok": True})

@app.route("/api/dashboard", methods=["GET"])
def dashboard():
    data = load_data()
    total_pendapatan = sum(t["total"] for t in data["transaksi"])
    total_transaksi = len(data["transaksi"])
    invoice_belum = len([i for i in data["invoice"] if i["status"] == "Belum Bayar"])
    stok_menipis = len([p for p in data["produk"] if p["stok"] < 10])
    return jsonify({
        "total_pendapatan": total_pendapatan,
        "total_transaksi": total_transaksi,
        "invoice_belum": invoice_belum,
        "stok_menipis": stok_menipis,
        "transaksi_terakhir": data["transaksi"][-5:][::-1]
    })

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
