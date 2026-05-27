# extensions.py
# Memisahkan inisialisasi SQLAlchemy dari app.py agar bisa diimport
# oleh blueprint/routes lain tanpa circular import.
#
# File ini MENGGANTIKAN baris berikut di app.py:
#   db = SQLAlchemy(app)
#
# ── Cara pakai di app.py ────────────────────────────────────────────────────
#
#   from extensions import db          # ganti: db = SQLAlchemy(app)
#
#   app = Flask(__name__)
#   app.config['SQLALCHEMY_DATABASE_URI'] = ...
#   db.init_app(app)                   # ikat db ke app setelah config selesai
#
# ── Cara import di file lain (routes, models, dsb.) ─────────────────────────
#
#   from extensions import db
#
# ────────────────────────────────────────────────────────────────────────────

from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()
