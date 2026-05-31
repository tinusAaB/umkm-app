/**
 * sync.js — Offline-first sync manager for UMKM Pro (Capacitor Android)
 * Strategy : last-write-wins, push before pull
 * Backend  : Flask on Railway — https://umkm-app-production.up.railway.app
 * Depends  : db.js (DB), @capacitor/network
 */

import { Network } from '@capacitor/network';
import { DB } from './db.js';

const BASE_URL     = 'https://umkm-app-production.up.railway.app';
const FETCH_TIMEOUT_MS = 15_000;
const MAX_RETRY        = 5;

// ---------------------------------------------------------------------------
// Internal state
// ---------------------------------------------------------------------------

let _isOnline  = false;
let _isSyncing = false;
/** @type {ReturnType<typeof setTimeout> | null} */
let _syncTimer = null;

// ---------------------------------------------------------------------------
// Internal helpers
// ---------------------------------------------------------------------------

/**
 * Returns a fetch Promise that rejects after FETCH_TIMEOUT_MS milliseconds.
 * @param {string} url
 * @param {RequestInit} [options]
 * @returns {Promise<Response>}
 */
function fetchWithTimeout(url, options = {}) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), FETCH_TIMEOUT_MS);
    return fetch(url, { ...options, signal: controller.signal })
        .finally(() => clearTimeout(timer));
}

/**
 * Schedules _onOnline() with a short debounce to avoid rapid-fire triggers
 * when the network flickers (e.g. switching between WiFi and mobile data).
 */
function _scheduleSync() {
    if (_syncTimer) clearTimeout(_syncTimer);
    _syncTimer = setTimeout(() => {
        _syncTimer = null;
        SyncManager._onOnline();
    }, 1_500);
}

// ---------------------------------------------------------------------------
// Upsert helper — bypasses DB.insert/update so no enqueue occurs
// ---------------------------------------------------------------------------

/**
 * Writes a server record directly into SQLite without touching sync_queue.
 * Applies last-write-wins: the server's version always wins.
 * If record.deleted_at is set, ensures the local row is soft-deleted.
 * @param {string} table
 * @param {Record<string, unknown>} record  Full row from server pull response
 */
async function _upsert(table, record) {
    // Access the raw SQLiteDBConnection that db.js manages internally.
    // DB._db is intentionally not exported, so we reach it via DB.getAll trick:
    // Instead, db.js exposes _db as a named export for sync use.
    // We call DB._rawRun() which db.js exposes for exactly this purpose.
    const existing = await DB.getById(table, record.id)
        // getById filters deleted_at IS NULL; we need to find deleted rows too
        .catch(() => null);

    // Re-query without the deleted_at filter to detect soft-deleted local rows
    const existingAny = await DB._queryOne(
        `SELECT id FROM ${table} WHERE id = ?`,
        [record.id]
    );

    // Normalise: treat empty string as null for deleted_at
    const deletedAt = record.deleted_at || null;

    const row = {
        ...record,
        deleted_at: deletedAt,
        synced: 1,
    };

    if (existingAny) {
        // UPDATE — build SET from all columns except id
        const { id, ...fields } = row;
        const cols   = Object.keys(fields);
        const clause = cols.map(k => `${k} = ?`).join(', ');
        const values = cols.map(k => fields[k]);
        await DB._rawRun(
            `UPDATE ${table} SET ${clause} WHERE id = ?`,
            [...values, id]
        );
    } else {
        // INSERT
        const cols         = Object.keys(row);
        const placeholders = cols.map(() => '?').join(', ');
        const values       = cols.map(k => row[k]);
        await DB._rawRun(
            `INSERT INTO ${table} (${cols.join(', ')}) VALUES (${placeholders})`,
            values
        );
    }
}

// ---------------------------------------------------------------------------
// Push: local → server
// ---------------------------------------------------------------------------

/**
 * Reads the pending sync_queue, skips items that exceeded MAX_RETRY,
 * and POSTs the rest to /sync/push.
 * On success clears confirmed ids; on per-item failure marks errors.
 * On network error leaves the queue untouched for the next retry.
 * @returns {Promise<void>}
 */
async function _push() {
    const allItems = await DB.getPendingQueue();
    if (!allItems.length) return;

    // Separate items that still have retries left
    const eligible = [];
    const skipped  = [];
    for (const item of allItems) {
        if (item.retry_count >= MAX_RETRY) {
            skipped.push(item);
        } else {
            eligible.push(item);
        }
    }

    if (skipped.length) {
        console.warn(
            `[Sync] Skipping ${skipped.length} queue item(s) that reached max retries (${MAX_RETRY}).`,
            skipped.map(i => i.id)
        );
    }

    if (!eligible.length) return;

    const deviceId = await DB.getMeta('device_id');
    const items = eligible.map(item => ({
        id:         item.id,
        table_name: item.table_name,
        record_id:  item.record_id,
        operation:  item.operation,
        payload:    (() => {
            try   { return JSON.parse(item.payload); }
            catch { return item.payload; }
        })(),
        created_at: item.created_at,
    }));

    let response;
    try {
        response = await fetchWithTimeout(`${BASE_URL}/sync/push`, {
            method:  'POST',
            headers: { 'Content-Type': 'application/json' },
            body:    JSON.stringify({ device_id: deviceId, items }),
        });
    } catch (err) {
        // Network/timeout error — leave queue intact, will retry on next sync
        const msg = err.name === 'AbortError'
            ? '[Sync] Push timed out after 15s.'
            : `[Sync] Push network error: ${err.message}`;
        console.warn(msg);
        return;
    }

    if (!response.ok) {
        console.warn(`[Sync] Push HTTP ${response.status}: ${response.statusText}`);
        return;
    }

    /** @type {{ synced_ids?: number[], failed?: Array<{id: number, error: string}> }} */
    const data = await response.json();

    if (data.synced_ids?.length) {
        await DB.clearQueue(data.synced_ids);
        console.info(`[Sync] Push OK — cleared ${data.synced_ids.length} item(s).`);
    }

    if (data.failed?.length) {
        for (const { id, error } of data.failed) {
            await DB.markQueueError(id, error);
        }
        console.warn(`[Sync] Push — ${data.failed.length} item(s) failed on server.`);
    }
}

// ---------------------------------------------------------------------------
// Pull: server → local
// ---------------------------------------------------------------------------

/**
 * Fetches records changed on the server since last_sync_at and upserts
 * them locally. Updates last_sync_at on success.
 * @returns {Promise<void>}
 */
async function _pull() {
    const deviceId   = await DB.getMeta('device_id');
    const lastSyncAt = await DB.getMeta('last_sync_at');

    const params = new URLSearchParams({ device_id: deviceId });
    if (lastSyncAt) params.set('since', lastSyncAt);

    let response;
    try {
        response = await fetchWithTimeout(`${BASE_URL}/sync/pull?${params}`);
    } catch (err) {
        const msg = err.name === 'AbortError'
            ? '[Sync] Pull timed out after 15s.'
            : `[Sync] Pull network error: ${err.message}`;
        console.warn(msg);
        return;
    }

    if (!response.ok) {
        console.warn(`[Sync] Pull HTTP ${response.status}: ${response.statusText}`);
        return;
    }

    /**
     * @type {{
     *   records: { produk?: object[], pelanggan?: object[], transaksi?: object[] },
     *   server_time: string
     * }}
     */
    const data = await response.json();

    let upserted = 0;
    for (const table of ['produk', 'pelanggan', 'transaksi']) {
        const records = data.records?.[table] ?? [];
        for (const record of records) {
            try {
                await _upsert(table, record);
                upserted++;
            } catch (err) {
                console.warn(`[Sync] Failed to upsert ${table}/${record.id}: ${err.message}`);
            }
        }
    }

    if (data.server_time) {
        await DB.setMeta('last_sync_at', data.server_time);
    }

    console.info(`[Sync] Pull OK — upserted ${upserted} record(s). server_time=${data.server_time}`);
}

// ---------------------------------------------------------------------------
// Public SyncManager
// ---------------------------------------------------------------------------

export const SyncManager = {

    /**
     * Initialises the network listener and performs an immediate sync if online.
     * Call once after DB.init() in the app's deviceready handler.
     */
    async init() {
        try {
            // Register live listener
            await Network.addListener('networkStatusChange', status => {
                if (status.connected) {
                    console.info('[Sync] Network came online.');
                    _isOnline = true;
                    _scheduleSync();
                } else {
                    _isOnline = false;
                    console.info('[Sync] Network went offline.');
                }
            });

            // Resolve current state immediately
            const status = await Network.getStatus();
            _isOnline = status.connected;
            console.info(`[Sync] Initial network status: ${_isOnline ? 'online' : 'offline'}`);

            if (_isOnline) _scheduleSync();
        } catch (err) {
            throw new Error(`[Sync] init() failed: ${err.message}`);
        }
    },

    /**
     * Runs a full push → pull cycle. Guarded by _isSyncing to prevent
     * concurrent runs triggered by rapid network state changes.
     * @returns {Promise<void>}
     */
    async _onOnline() {
        if (_isSyncing) {
            console.info('[Sync] Sync already in progress — skipping.');
            return;
        }
        _isSyncing = true;
        try {
            await _push();
            await _pull();
        } catch (err) {
            console.error(`[Sync] Sync cycle error: ${err.message}`);
        } finally {
            _isSyncing = false;
        }
    },

    /**
     * Manually triggers a full sync cycle from the UI.
     * Silently skips if the device is currently offline.
     * @returns {Promise<void>}
     */
    async sync() {
        if (!_isOnline) {
            console.info('[Sync] Manual sync skipped — device is offline.');
            return;
        }
        await SyncManager._onOnline();
    },

    /**
     * Returns the current sync state for display in the UI.
     * @returns {Promise<{ isOnline: boolean, isSyncing: boolean, pendingCount: number }>}
     */
    async getStatus() {
        try {
            const queue = await DB.getPendingQueue();
            return {
                isOnline:     _isOnline,
                isSyncing:    _isSyncing,
                pendingCount: queue.length,
            };
        } catch (err) {
            throw new Error(`[Sync] getStatus() failed: ${err.message}`);
        }
    },

};
