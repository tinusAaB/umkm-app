/**
 * app.js — Entry point UMKM Pro (Capacitor Android)
 * Urutan boot: DB.init() → SyncManager.init() → UI ready
 */

import { DB } from './db.js';
import { SyncManager } from './sync.js';

// ---------------------------------------------------------------------------
// Boot sequence
// ---------------------------------------------------------------------------

document.addEventListener('DOMContentLoaded', async () => {
    document.body.classList.add('app-loading');

    // 1. Inisialisasi database lokal (SQLite)
    try {
        await DB.init();
    } catch (err) {
        _showFatalError(`Gagal membuka database lokal: ${err.message}`);
        return;
    }

    // 2. Inisialisasi sync manager (non-fatal — app tetap jalan offline)
    try {
        await SyncManager.init();
    } catch (err) {
        console.warn('[UMKM Pro] SyncManager.init() gagal, app berjalan offline:', err.message);
    }

    // 3. UI siap
    document.body.classList.remove('app-loading');
    document.body.classList.add('app-ready');

    // 4. Tampilkan status sync awal
    await updateSyncIndicator();

    // Polling setiap 5 detik
    setInterval(updateSyncIndicator, 5_000);
});

// ---------------------------------------------------------------------------
// Sync indicator
// ---------------------------------------------------------------------------

/**
 * Membaca status sync terkini dari SyncManager dan memperbarui elemen DOM:
 *   #sync-status-dot   — kelas 'online' | 'offline' | 'syncing'
 *   #sync-status-text  — teks singkat status
 *   #sync-pending-count — badge angka pending (disembunyikan jika 0)
 */
async function updateSyncIndicator() {
    let status;
    try {
        status = await SyncManager.getStatus();
    } catch {
        return;
    }

    const dot     = document.getElementById('sync-status-dot');
    const text    = document.getElementById('sync-status-text');
    const badge   = document.getElementById('sync-pending-count');

    if (!dot || !text || !badge) return;

    // Dot class
    dot.classList.remove('online', 'offline', 'syncing');
    if (status.isSyncing) {
        dot.classList.add('syncing');
        text.textContent = 'Menyinkron...';
    } else if (status.isOnline) {
        dot.classList.add('online');
        text.textContent = 'Online';
    } else {
        dot.classList.add('offline');
        text.textContent = 'Offline';
    }

    // Pending badge
    if (status.pendingCount > 0) {
        badge.textContent    = status.pendingCount;
        badge.style.display  = 'inline-block';
    } else {
        badge.style.display  = 'none';
    }
}

// ---------------------------------------------------------------------------
// Manual sync button
// ---------------------------------------------------------------------------

document.addEventListener('DOMContentLoaded', () => {
    const btn = document.getElementById('btn-sync-manual');
    if (!btn) return;

    btn.addEventListener('click', async () => {
        btn.disabled = true;
        try {
            await SyncManager.sync();
        } finally {
            btn.disabled = false;
            await updateSyncIndicator();
        }
    });
});

// ---------------------------------------------------------------------------
// Global unhandled rejection handler
// ---------------------------------------------------------------------------

window.addEventListener('unhandledrejection', event => {
    console.error('[UMKM Pro] Unhandled promise rejection:', event.reason);
});

// ---------------------------------------------------------------------------
// Expose globals for legacy scripts that don't use ES modules
// ---------------------------------------------------------------------------

window.DB          = DB;
window.SyncManager = SyncManager;
