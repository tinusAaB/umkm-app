# UMKM Pro — Panduan Instalasi & Hosting

## Jalankan di Komputer Lokal (Ubuntu)

### 1. Install Python & Flask
```bash
sudo apt update
sudo apt install python3 python3-pip -y
pip3 install flask --break-system-packages
```

### 2. Jalankan Aplikasi
```bash
cd umkm-app
python3 app.py
```

Buka browser: http://localhost:5000
Buka dari HP Android (WiFi sama): http://IP_LAPTOP:5000

Cari IP laptop dengan: `hostname -I`

---

## Deploy Online 24 Jam (Gratis) — Railway.app

### Langkah:
1. Buat akun di https://railway.app (gratis)
2. Install Railway CLI:
   ```bash
   curl -fsSL https://railway.app/install.sh | sh
   ```
3. Login & deploy:
   ```bash
   cd umkm-app
   railway login
   railway init
   railway up
   ```
4. Dapatkan URL publik — bisa dibuka dari HP Android manapun!

---

## Struktur File
```
umkm-app/
├── app.py          ← Backend (logika bisnis)
├── requirements.txt
├── data.json       ← Database (otomatis dibuat)
└── templates/
    └── index.html  ← Tampilan aplikasi
```

---

## Fitur Aplikasi
- Dashboard ringkasan bisnis
- Kasir / POS dengan keranjang belanja
- Generator Invoice otomatis
- Manajemen Stok & Inventori
- Kalkulator Harga Jual & Laba
- Responsive — bisa dibuka dari HP Android
