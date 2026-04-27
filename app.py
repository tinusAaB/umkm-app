import os
from dotenv import load_dotenv
load_dotenv()
import midtransclient
from flask import Flask, render_template, request, jsonify, send_file, redirect, url_for
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from flask_bcrypt import Bcrypt
from datetime import datetime
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
import json, io, requests as req

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'postgresql://martin:umkm1234@localhost/umkmpro')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SECRET_KEY'] = 'umkmpro-secret-2024'
db = SQLAlchemy(app)
bcrypt = Bcrypt(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'

MIDTRANS_SERVER_KEY = os.environ.get('MIDTRANS_SERVER_KEY', '')
MIDTRANS_CLIENT_KEY = os.environ.get('MIDTRANS_CLIENT_KEY', '')
print(f"Server Key loaded: {MIDTRANS_SERVER_KEY[:10]}...")

snap = midtransclient.Snap(
    is_production=False,
    server_key=MIDTRANS_SERVER_KEY
)

@app.after_request
def add_security_headers(response):
    response.headers['Content-Security-Policy'] = (
        "default-src *; "
        "script-src * 'unsafe-inline' 'unsafe-eval'; "
        "style-src * 'unsafe-inline'; "
        "frame-src *; "
        "connect-src *; "
        "img-src * data:;"
    )
    return response

class Toko(db.Model):
    __tablename__ = 'toko'
    id = db.Column(db.Integer, primary_key=True)
    nama = db.Column(db.String(200), default='Toko Saya')
    alamat = db.Column(db.String(500), default='')
    telepon = db.Column(db.String(50), default='')
    email = db.Column(db.String(100), default='')
    tagline = db.Column(db.String(200), default='')
    dibuat = db.Column(db.DateTime, default=datetime.now)

class User(UserMixin, db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)
    role = db.Column(db.String(20), default='kasir')
    nama = db.Column(db.String(100))
    dibuat = db.Column(db.DateTime, default=datetime.now)

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

class Produk(db.Model):
    __tablename__ = 'produk'
    id = db.Column(db.Integer, primary_key=True)
    nama = db.Column(db.String(200), nullable=False)
    harga = db.Column(db.Integer, nullable=False)
    modal = db.Column(db.Integer, default=0)
    stok = db.Column(db.Integer, default=0)
    kategori = db.Column(db.String(100), default='Umum')
    dibuat = db.Column(db.DateTime, default=datetime.now)

class Transaksi(db.Model):
    __tablename__ = 'transaksi'
    id = db.Column(db.Integer, primary_key=True)
    pelanggan = db.Column(db.String(200), default='Umum')
    kasir = db.Column(db.String(100), default='Kasir')
    total = db.Column(db.Integer, nullable=False)
    items = db.Column(db.Text)
    tanggal = db.Column(db.DateTime, default=datetime.now)

class Invoice(db.Model):
    __tablename__ = 'invoice'
    id = db.Column(db.Integer, primary_key=True)
    nomor = db.Column(db.String(50))
    pelanggan = db.Column(db.String(200), nullable=False)
    produk = db.Column(db.String(200))
    jumlah = db.Column(db.Integer, default=1)
    harga = db.Column(db.Integer, default=0)
    total = db.Column(db.Integer, default=0)
    jatuh_tempo = db.Column(db.String(50))
    status = db.Column(db.String(50), default='Belum Bayar')
    tanggal = db.Column(db.DateTime, default=datetime.now)

def kirim_whatsapp(nomor, pesan):
    token = os.environ.get('FONNTE_TOKEN', '')
    if not token:
        return False
    try:
        response = req.post('https://api.fonnte.com/send',
            headers={'Authorization': token},
            data={'target': nomor, 'message': pesan, 'countryCode': '62'}
        )
        print(f"WhatsApp sent: {response.json()}")
        return True
    except Exception as e:
        print(f"WhatsApp error: {str(e)}")
        return False

@app.route('/setup', methods=['GET', 'POST'])
def setup():
    if User.query.filter_by(role='owner').count() > 0:
        return redirect(url_for('login'))
    if request.method == 'POST':
        data = request.json
        toko = Toko.query.first()
        if not toko:
            toko = Toko()
            db.session.add(toko)
        toko.nama = data.get('nama_toko', 'Toko Saya')
        toko.alamat = data.get('alamat', '')
        toko.telepon = data.get('telepon', '')
        toko.email = data.get('email', '')
        owner = User(
            username=data.get('username', 'owner'),
            password=bcrypt.generate_password_hash(data.get('password', 'owner123')).decode('utf-8'),
            role='owner',
            nama=data.get('nama_owner', 'Owner')
        )
        db.session.add(owner)
        db.session.commit()
        return jsonify({'ok': True})
    return render_template('setup.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if User.query.filter_by(role='owner').count() == 0:
        return redirect(url_for('setup'))
    if request.method == 'POST':
        data = request.json
        user = User.query.filter_by(username=data['username']).first()
        if user and bcrypt.check_password_hash(user.password, data['password']):
            login_user(user)
            return jsonify({'ok': True, 'role': user.role, 'nama': user.nama})
        return jsonify({'ok': False, 'pesan': 'Username atau password salah'}), 401
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

@app.route('/api/me')
@login_required
def me():
    return jsonify({'id': current_user.id, 'username': current_user.username, 'role': current_user.role, 'nama': current_user.nama})

@app.route('/')
@login_required
def index():
    return render_template('index.html')

@app.route('/api/toko', methods=['GET'])
@login_required
def get_toko():
    toko = Toko.query.first()
    if not toko:
        toko = Toko()
        db.session.add(toko)
        db.session.commit()
    return jsonify({'nama': toko.nama, 'alamat': toko.alamat, 'telepon': toko.telepon, 'email': toko.email, 'tagline': toko.tagline})

@app.route('/api/toko', methods=['POST'])
@login_required
def update_toko():
    if current_user.role != 'owner':
        return jsonify({'error': 'Akses ditolak'}), 403
    toko = Toko.query.first()
    if not toko:
        toko = Toko()
        db.session.add(toko)
    b = request.json
    toko.nama = b.get('nama', toko.nama)
    toko.alamat = b.get('alamat', toko.alamat)
    toko.telepon = b.get('telepon', toko.telepon)
    toko.email = b.get('email', toko.email)
    toko.tagline = b.get('tagline', toko.tagline)
    db.session.commit()
    return jsonify({'ok': True})

@app.route('/api/produk', methods=['GET'])
@login_required
def get_produk():
    produk = Produk.query.all()
    return jsonify([{'id': p.id, 'nama': p.nama, 'harga': p.harga, 'modal': p.modal, 'stok': p.stok, 'kategori': p.kategori} for p in produk])

@app.route('/api/produk', methods=['POST'])
@login_required
def add_produk():
    b = request.json
    p = Produk(nama=b['nama'], harga=int(b['harga']), modal=int(b.get('modal', 0)), stok=int(b.get('stok', 99)), kategori=b.get('kategori', 'Umum'))
    db.session.add(p)
    db.session.commit()
    return jsonify({'id': p.id, 'nama': p.nama, 'harga': p.harga, 'modal': p.modal, 'stok': p.stok})

@app.route('/api/produk/<int:pid>', methods=['DELETE'])
@login_required
def del_produk(pid):
    p = Produk.query.get_or_404(pid)
    db.session.delete(p)
    db.session.commit()
    return jsonify({'ok': True})

@app.route('/api/transaksi', methods=['GET'])
@login_required
def get_transaksi():
    transaksi = Transaksi.query.order_by(Transaksi.tanggal.desc()).all()
    return jsonify([{'id': t.id, 'pelanggan': t.pelanggan, 'total': t.total, 'items': json.loads(t.items or '[]'), 'tanggal': t.tanggal.strftime('%d/%m/%Y %H:%M')} for t in transaksi])

@app.route('/api/transaksi', methods=['POST'])
@login_required
def add_transaksi():
    b = request.json
    items = b['items']
    total = 0
    for item in items:
        p = Produk.query.get(item['id'])
        if p:
            p.stok = max(0, p.stok - item['qty'])
            total += p.harga * item['qty']
    t = Transaksi(
    pelanggan=b.get('pelanggan', 'Umum'),
    kasir=current_user.nama or current_user.username,
    total=total,
    items=json.dumps(items)
)
    db.session.add(t)
    db.session.commit()
    nomor = b.get('nomor_wa', '')
    if nomor:
        toko = Toko.query.first()
        nama_toko = toko.nama if toko else 'UMKM Pro'
        pesan = f"✅ *Struk Pembelian {nama_toko}*\n\n"
        pesan += f"No: TRX-{t.id:04d}\n"
        pesan += f"Pelanggan: {t.pelanggan}\n"
        pesan += f"Tanggal: {t.tanggal.strftime('%d/%m/%Y %H:%M')}\n\n"
        for item in items:
            p = Produk.query.get(item['id'])
            if p:
                pesan += f"- {p.nama} x{item['qty']} = Rp {p.harga * item['qty']:,}\n"
        pesan += f"\n*Total: Rp {total:,}*\n\n"
        pesan += "Terima kasih telah berbelanja! 🙏"
        kirim_whatsapp(nomor, pesan)
    return jsonify({'id': t.id, 'total': total})

@app.route('/api/invoice', methods=['GET'])
@login_required
def get_invoice():
    invoices = Invoice.query.order_by(Invoice.tanggal.desc()).all()
    return jsonify([{'id': i.id, 'nomor': i.nomor, 'pelanggan': i.pelanggan, 'produk': i.produk, 'jumlah': i.jumlah, 'harga': i.harga, 'total': i.total, 'jatuh_tempo': i.jatuh_tempo, 'status': i.status, 'tanggal': i.tanggal.strftime('%d/%m/%Y')} for i in invoices])

@app.route('/api/invoice', methods=['POST'])
@login_required
def add_invoice():
    b = request.json
    nomor = f"INV-{datetime.now().strftime('%Y%m%d')}-{Invoice.query.count()+1:03d}"
    i = Invoice(nomor=nomor, pelanggan=b['pelanggan'], produk=b['produk'], jumlah=int(b['jumlah']), harga=int(b['harga']), total=int(b['jumlah'])*int(b['harga']), jatuh_tempo=b.get('jatuh_tempo', '-'))
    db.session.add(i)
    db.session.commit()
    return jsonify({'id': i.id, 'nomor': i.nomor})

@app.route('/api/invoice/<int:iid>/lunas', methods=['POST'])
@login_required
def lunas_invoice(iid):
    i = Invoice.query.get_or_404(iid)
    i.status = 'Lunas'
    db.session.commit()
    return jsonify({'ok': True})

@app.route('/api/invoice/<int:iid>/pdf')
@login_required
def cetak_invoice(iid):
    i = Invoice.query.get_or_404(iid)
    toko = Toko.query.first()
    nama_toko = toko.nama if toko else 'UMKM Pro'
    alamat_toko = toko.alamat if toko else ''
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=2*cm, leftMargin=2*cm, topMargin=2*cm, bottomMargin=2*cm)
    elemen = []
    judul = ParagraphStyle('judul', fontSize=20, fontName='Helvetica-Bold', spaceAfter=4)
    normal = ParagraphStyle('normal', fontSize=10, fontName='Helvetica', spaceAfter=4)
    elemen.append(Paragraph(f'INVOICE — {nama_toko.upper()}', judul))
    if alamat_toko:
        elemen.append(Paragraph(alamat_toko, normal))
    elemen.append(Paragraph(f'Nomor: {i.nomor}', normal))
    elemen.append(Paragraph(f'Tanggal: {i.tanggal.strftime("%d/%m/%Y")}', normal))
    elemen.append(Paragraph(f'Jatuh Tempo: {i.jatuh_tempo}', normal))
    elemen.append(Spacer(1, 0.3*cm))
    elemen.append(Paragraph(f'Kepada: {i.pelanggan}', normal))
    elemen.append(Spacer(1, 0.5*cm))
    data = [['Produk/Layanan', 'Jumlah', 'Harga Satuan', 'Total'],
            [i.produk, str(i.jumlah), f"Rp {i.harga:,}", f"Rp {i.total:,}"],
            ['', '', 'TOTAL', f"Rp {i.total:,}"]]
    tabel = Table(data, colWidths=[8*cm, 2*cm, 4*cm, 4*cm])
    tabel.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#4f6ef7')),
        ('TEXTCOLOR', (0,0), (-1,0), colors.white),
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ('ALIGN', (1,0), (-1,-1), 'CENTER'),
        ('FONTNAME', (0,-1), (-1,-1), 'Helvetica-Bold'),
        ('BACKGROUND', (0,-1), (-1,-1), colors.HexColor('#f0f0f0')),
        ('GRID', (0,0), (-1,-2), 0.5, colors.grey),
        ('LINEABOVE', (0,-1), (-1,-1), 1, colors.black),
    ]))
    elemen.append(tabel)
    elemen.append(Spacer(1, 0.5*cm))
    elemen.append(Paragraph(f'Status: {i.status}', normal))
    elemen.append(Paragraph(f'Dibuat oleh: {current_user.nama}', normal))
    doc.build(elemen)
    buffer.seek(0)
    return send_file(buffer, mimetype='application/pdf', download_name=f'invoice-{i.nomor}.pdf')

@app.route('/api/struk/<int:tid>')
@login_required
def cetak_struk(tid):
    t = Transaksi.query.get_or_404(tid)
    items = json.loads(t.items or '[]')
    toko = Toko.query.first()
    nama_toko = toko.nama if toko else 'UMKM Pro'
    alamat_toko = toko.alamat if toko else ''
    telepon_toko = toko.telepon if toko else ''
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=2*cm, leftMargin=2*cm, topMargin=2*cm, bottomMargin=2*cm)
    elemen = []
    judul = ParagraphStyle('judul', fontSize=16, fontName='Helvetica-Bold', alignment=1, spaceAfter=4)
    sub = ParagraphStyle('sub', fontSize=10, fontName='Helvetica', alignment=1, spaceAfter=2)
    elemen.append(Paragraph(nama_toko.upper(), judul))
    if alamat_toko:
        elemen.append(Paragraph(alamat_toko, sub))
    if telepon_toko:
        elemen.append(Paragraph(f'Telp: {telepon_toko}', sub))
    elemen.append(Paragraph('Struk Pembelian', sub))
    elemen.append(Paragraph(f'No: TRX-{t.id:04d}', sub))
    elemen.append(Paragraph(f'Tanggal: {t.tanggal.strftime("%d/%m/%Y %H:%M")}', sub))
    elemen.append(Paragraph(f'Pelanggan: {t.pelanggan}', sub))
    elemen.append(Paragraph(f'Kasir: {t.kasir}', sub))
    elemen.append(Spacer(1, 0.3*cm))
    data = [['Produk', 'Qty', 'Harga', 'Subtotal']]
    for item in items:
        p = Produk.query.get(item['id'])
        if p:
            subtotal = p.harga * item['qty']
            data.append([p.nama, str(item['qty']), f"Rp {p.harga:,}", f"Rp {subtotal:,}"])
    data.append(['', '', 'TOTAL', f"Rp {t.total:,}"])
    tabel = Table(data, colWidths=[8*cm, 2*cm, 4*cm, 4*cm])
    tabel.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#4f6ef7')),
        ('TEXTCOLOR', (0,0), (-1,0), colors.white),
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ('ALIGN', (1,0), (-1,-1), 'CENTER'),
        ('FONTNAME', (0,-1), (-1,-1), 'Helvetica-Bold'),
        ('BACKGROUND', (0,-1), (-1,-1), colors.HexColor('#f0f0f0')),
        ('GRID', (0,0), (-1,-2), 0.5, colors.grey),
        ('LINEABOVE', (0,-1), (-1,-1), 1, colors.black),
        ('ROWBACKGROUNDS', (0,1), (-1,-2), [colors.white, colors.HexColor('#f9f9f9')]),
    ]))
    elemen.append(tabel)
    elemen.append(Spacer(1, 0.5*cm))
    elemen.append(Paragraph('Terima kasih telah berbelanja!', ParagraphStyle('thanks', fontSize=10, fontName='Helvetica-Bold', alignment=1)))
    doc.build(elemen)
    buffer.seek(0)
    return send_file(buffer, mimetype='application/pdf', download_name=f'struk-{t.id}.pdf')

@app.route('/api/bayar', methods=['POST'])
@login_required
def bayar():
    b = request.json
    items = b['items']
    total = 0
    item_details = []
    for item in items:
        p = Produk.query.get(item['id'])
        if p:
            subtotal = p.harga * item['qty']
            total += subtotal
            item_details.append({'id': str(p.id), 'price': p.harga, 'quantity': item['qty'], 'name': p.nama[:50]})
    order_id = f"UMKM-{datetime.now().strftime('%Y%m%d%H%M%S')}"
    param = {
        'transaction_details': {'order_id': order_id, 'gross_amount': total},
        'item_details': item_details,
        'customer_details': {'first_name': b.get('pelanggan', 'Pelanggan')}
    }
    try:
        transaction = snap.create_transaction(param)
        return jsonify({'ok': True, 'token': transaction['token'], 'redirect_url': transaction['redirect_url'], 'order_id': order_id, 'total': total})
    except Exception as e:
        print(f"Midtrans error: {str(e)}")
        return jsonify({'ok': False, 'error': str(e)}), 500

@app.route('/api/users', methods=['GET'])
@login_required
def get_users():
    if current_user.role != 'owner':
        return jsonify({'error': 'Akses ditolak'}), 403
    users = User.query.all()
    return jsonify([{'id': u.id, 'username': u.username, 'nama': u.nama, 'role': u.role} for u in users])

@app.route('/api/users', methods=['POST'])
@login_required
def add_user():
    if current_user.role != 'owner':
        return jsonify({'error': 'Akses ditolak'}), 403
    b = request.json
    if User.query.filter_by(username=b['username']).first():
        return jsonify({'error': 'Username sudah dipakai'}), 400
    u = User(username=b['username'], password=bcrypt.generate_password_hash(b['password']).decode('utf-8'), role=b.get('role', 'kasir'), nama=b['nama'])
    db.session.add(u)
    db.session.commit()
    return jsonify({'id': u.id, 'username': u.username, 'nama': u.nama, 'role': u.role})

@app.route('/api/users/<int:uid>', methods=['DELETE'])
@login_required
def del_user(uid):
    if current_user.role != 'owner':
        return jsonify({'error': 'Akses ditolak'}), 403
    if uid == current_user.id:
        return jsonify({'error': 'Tidak bisa hapus akun sendiri'}), 400
    u = User.query.get_or_404(uid)
    db.session.delete(u)
    db.session.commit()
    return jsonify({'ok': True})

@app.route('/api/stok-alert')
@login_required
def stok_alert():
    batas = request.args.get('batas', 10, type=int)
    menipis = Produk.query.filter(Produk.stok <= batas).all()
    return jsonify({'jumlah': len(menipis), 'produk': [{'id': p.id, 'nama': p.nama, 'stok': p.stok, 'harga': p.harga} for p in menipis]})

@app.route('/api/laporan')
@login_required
def laporan():
    from collections import defaultdict
    transaksi = Transaksi.query.order_by(Transaksi.tanggal.asc()).all()
    per_hari = defaultdict(int)
    per_bulan = defaultdict(int)
    for t in transaksi:
        tgl = t.tanggal.strftime('%d/%m')
        per_hari[tgl] += t.total
        bln = t.tanggal.strftime('%b %Y')
        per_bulan[bln] += t.total
    produk_count = defaultdict(int)
    for t in transaksi:
        items = json.loads(t.items or '[]')
        for item in items:
            p = Produk.query.get(item['id'])
            if p:
                produk_count[p.nama] += item['qty']
    terlaris = sorted(produk_count.items(), key=lambda x: x[1], reverse=True)[:5]
    return jsonify({
        'per_hari': [{'tanggal': k, 'total': v} for k, v in list(per_hari.items())[-14:]],
        'per_bulan': [{'bulan': k, 'total': v} for k, v in per_bulan.items()],
        'terlaris': [{'nama': k, 'qty': v} for k, v in terlaris],
        'total_pendapatan': sum(t.total for t in transaksi),
        'rata_per_hari': int(sum(t.total for t in transaksi) / max(len(per_hari), 1)),
        'total_transaksi': len(transaksi),
    })

@app.route('/api/neraca')
@login_required
def neraca():
    total_pendapatan = db.session.query(db.func.sum(Transaksi.total)).scalar() or 0
    total_hpp = 0
    transaksi = Transaksi.query.all()
    for t in transaksi:
        items = json.loads(t.items or '[]')
        for item in items:
            p = Produk.query.get(item['id'])
            if p:
                total_hpp += p.modal * item['qty']
    laba_kotor = total_pendapatan - total_hpp
    nilai_stok = db.session.query(db.func.sum(Produk.modal * Produk.stok)).scalar() or 0
    piutang = db.session.query(db.func.sum(Invoice.total)).filter_by(status='Belum Bayar').scalar() or 0
    total_aset = nilai_stok + piutang + total_pendapatan
    invoice_lunas = db.session.query(db.func.sum(Invoice.total)).filter_by(status='Lunas').scalar() or 0
    return jsonify({
        'pendapatan': total_pendapatan, 'hpp': total_hpp, 'laba_kotor': laba_kotor,
        'margin': round(laba_kotor / total_pendapatan * 100, 1) if total_pendapatan > 0 else 0,
        'nilai_stok': nilai_stok, 'piutang': piutang, 'total_aset': total_aset,
        'invoice_lunas': invoice_lunas, 'total_transaksi': len(transaksi),
    })

@app.route('/api/dashboard', methods=['GET'])
@login_required
def dashboard():
    total_pendapatan = db.session.query(db.func.sum(Transaksi.total)).scalar() or 0
    total_transaksi = Transaksi.query.count()
    invoice_belum = Invoice.query.filter_by(status='Belum Bayar').count()
    stok_menipis = Produk.query.filter(Produk.stok < 10).count()
    transaksi_terakhir = Transaksi.query.order_by(Transaksi.tanggal.desc()).limit(5).all()
    return jsonify({
        'total_pendapatan': total_pendapatan, 'total_transaksi': total_transaksi,
        'invoice_belum': invoice_belum, 'stok_menipis': stok_menipis,
        'transaksi_terakhir': [{'id': t.id, 'pelanggan': t.pelanggan, 'total': t.total, 'items': json.loads(t.items or '[]'), 'tanggal': t.tanggal.strftime('%d/%m/%Y %H:%M')} for t in transaksi_terakhir]
    })

with app.app_context():
    try:
        db.create_all()
    except Exception as e:
        print(f"Database init error: {e}")

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(debug=False, host='0.0.0.0', port=port)
