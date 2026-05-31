/**
 * db.js — Local SQLite wrapper for UMKM Pro (Capacitor Android)
 * Package : com.yekritu.umkmpro
 * Plugin  : @capacitor-community/sqlite
 * Schema  : migrations/offline_sync/003_sqlite_schema.sql
 */

import { CapacitorSQLite, SQLiteConnection } from '@capacitor-community/sqlite';

const DB_NAME    = 'umkmpro';
const DB_VERSION = 1;

// Tables that participate in offline sync (write ops are queued)
const SYNC_TABLES = new Set(['produk', 'pelanggan', 'transaksi']);

const sqlite = new SQLiteConnection(CapacitorSQLite);

/** @type {import('@capacitor-community/sqlite').SQLiteDBConnection | null} */
let _db = null;

/** @type {string | null} */
let _deviceId = null;

// ---------------------------------------------------------------------------
// Internal helpers
// ---------------------------------------------------------------------------

/**
 * Returns the open DB connection, throwing if init() was not called first.
 * @returns {import('@capacitor-community/sqlite').SQLiteDBConnection}
 */
function getDb() {
    if (!_db) throw new Error('[DB] Database not initialised. Call DB.init() first.');
    return _db;
}

/**
 * Builds a parameterised SET clause and its value array from a plain object.
 * e.g. { nama: 'A', harga: 5000 } → { clause: 'nama = ?, harga = ?', values: ['A', 5000] }
 * @param {Record<string, unknown>} data
 * @returns {{ clause: string, values: unknown[] }}
 */
function buildSet(data) {
    const keys   = Object.keys(data);
    const clause = keys.map(k => `${k} = ?`).join(', ');
    const values = keys.map(k => data[k]);
    return { clause, values };
}

/**
 * Enqueues a write operation in sync_queue so it can be pushed to Supabase later.
 * @param {'INSERT'|'UPDATE'|'DELETE'} operation
 * @param {string} tableName
 * @param {string} recordId   UUID of the affected row
 * @param {Record<string, unknown>} payload  Full row snapshot
 */
async function enqueue(operation, tableName, recordId, payload) {
    const db = getDb();
    await db.run(
        `INSERT INTO sync_queue (table_name, record_id, operation, payload)
         VALUES (?, ?, ?, ?)`,
        [tableName, recordId, operation, JSON.stringify(payload)]
    );
}

// ---------------------------------------------------------------------------
// Public DB object
// ---------------------------------------------------------------------------

export const DB = {

    // -----------------------------------------------------------------------
    // Lifecycle
    // -----------------------------------------------------------------------

    /**
     * Opens (or creates) the SQLite database and executes the full schema SQL.
     * Must be awaited once before any other DB call, typically in deviceready.
     */
    async init() {
        try {
            const consistent = await sqlite.checkConnectionsConsistency();
            const isConn     = (await sqlite.isConnection(DB_NAME, false)).result;

            if (consistent.result && isConn) {
                _db = await sqlite.retrieveConnection(DB_NAME, false);
            } else {
                _db = await sqlite.createConnection(
                    DB_NAME, false, 'no-encryption', DB_VERSION, false
                );
            }

            await _db.open();

            // Run schema (all CREATE TABLE IF NOT EXISTS — safe to re-run)
            const schemaRes = await fetch('./migrations/offline_sync/003_sqlite_schema.sql');
            if (!schemaRes.ok) throw new Error('[DB] Failed to load schema SQL file.');
            const schemaSql = await schemaRes.text();
            await _db.execute(schemaSql);

            // Resolve or create device_id
            _deviceId = await DB.getMeta('device_id');
            if (!_deviceId) {
                _deviceId = crypto.randomUUID();
                await DB.setMeta('device_id', _deviceId);
            }
        } catch (err) {
            throw new Error(`[DB] init() failed: ${err.message}`);
        }
    },

    // -----------------------------------------------------------------------
    // Generic CRUD
    // -----------------------------------------------------------------------

    /**
     * Returns all non-deleted rows from a table.
     * @param {string} table  Table name (e.g. 'produk')
     * @returns {Promise<Record<string, unknown>[]>}
     */
    async getAll(table) {
        try {
            const res = await getDb().query(
                `SELECT * FROM ${table} WHERE deleted_at IS NULL`
            );
            return res.values ?? [];
        } catch (err) {
            throw new Error(`[DB] getAll(${table}) failed: ${err.message}`);
        }
    },

    /**
     * Returns a single non-deleted row by UUID, or null if not found.
     * @param {string} table
     * @param {string} id  UUID
     * @returns {Promise<Record<string, unknown> | null>}
     */
    async getById(table, id) {
        try {
            const res = await getDb().query(
                `SELECT * FROM ${table} WHERE id = ? AND deleted_at IS NULL`,
                [id]
            );
            return res.values?.[0] ?? null;
        } catch (err) {
            throw new Error(`[DB] getById(${table}, ${id}) failed: ${err.message}`);
        }
    },

    /**
     * Inserts a new row, auto-assigning id, device_id, and synced = 0.
     * Enqueues an INSERT entry in sync_queue for synced tables.
     * @param {string} table
     * @param {Record<string, unknown>} data  Column values (id is optional; generated if absent)
     * @returns {Promise<string>}  The UUID of the inserted row
     */
    async insert(table, data) {
        try {
            const id  = data.id ?? crypto.randomUUID();
            const row = {
                ...data,
                id,
                device_id:  _deviceId,
                synced:     0,
                deleted_at: null,
            };

            const cols        = Object.keys(row);
            const placeholders = cols.map(() => '?').join(', ');
            const values      = cols.map(k => row[k]);

            await getDb().run(
                `INSERT INTO ${table} (${cols.join(', ')}) VALUES (${placeholders})`,
                values
            );

            if (SYNC_TABLES.has(table)) {
                await enqueue('INSERT', table, id, row);
            }

            return id;
        } catch (err) {
            throw new Error(`[DB] insert(${table}) failed: ${err.message}`);
        }
    },

    /**
     * Updates an existing row by id. Sets synced = 0 to mark it dirty.
     * Enqueues an UPDATE entry in sync_queue for synced tables.
     * @param {string} table
     * @param {string} id  UUID
     * @param {Record<string, unknown>} data  Columns to update (id excluded automatically)
     */
    async update(table, id, data) {
        try {
            const { id: _ignored, ...safeData } = data;
            const patch = { ...safeData, synced: 0 };

            const { clause, values } = buildSet(patch);
            await getDb().run(
                `UPDATE ${table} SET ${clause} WHERE id = ? AND deleted_at IS NULL`,
                [...values, id]
            );

            if (SYNC_TABLES.has(table)) {
                // Fetch current row snapshot for the queue payload
                const row = await DB.getById(table, id);
                await enqueue('UPDATE', table, id, row ?? { id, ...patch });
            }
        } catch (err) {
            throw new Error(`[DB] update(${table}, ${id}) failed: ${err.message}`);
        }
    },

    /**
     * Soft-deletes a row by setting deleted_at to the current UTC timestamp.
     * Enqueues a DELETE entry in sync_queue for synced tables.
     * The row is never physically removed from SQLite.
     * @param {string} table
     * @param {string} id  UUID
     */
    async delete(table, id) {
        try {
            const now = new Date().toISOString();
            await getDb().run(
                `UPDATE ${table} SET deleted_at = ?, synced = 0 WHERE id = ? AND deleted_at IS NULL`,
                [now, id]
            );

            if (SYNC_TABLES.has(table)) {
                await enqueue('DELETE', table, id, { id, deleted_at: now });
            }
        } catch (err) {
            throw new Error(`[DB] delete(${table}, ${id}) failed: ${err.message}`);
        }
    },

    // -----------------------------------------------------------------------
    // app_meta key-value store
    // -----------------------------------------------------------------------

    /**
     * Reads a value from app_meta. Returns null if the key does not exist.
     * @param {string} key
     * @returns {Promise<string | null>}
     */
    async getMeta(key) {
        try {
            const res = await getDb().query(
                `SELECT value FROM app_meta WHERE key = ?`,
                [key]
            );
            return res.values?.[0]?.value ?? null;
        } catch (err) {
            throw new Error(`[DB] getMeta(${key}) failed: ${err.message}`);
        }
    },

    /**
     * Inserts or updates a key-value pair in app_meta.
     * @param {string} key
     * @param {string} value
     */
    async setMeta(key, value) {
        try {
            await getDb().run(
                `INSERT INTO app_meta (key, value) VALUES (?, ?)
                 ON CONFLICT(key) DO UPDATE SET value = excluded.value`,
                [key, value]
            );
        } catch (err) {
            throw new Error(`[DB] setMeta(${key}) failed: ${err.message}`);
        }
    },

    // -----------------------------------------------------------------------
    // Sync queue helpers
    // -----------------------------------------------------------------------

    /**
     * Returns all rows from sync_queue ordered oldest-first.
     * Use this to fetch the batch to push to Supabase.
     * @returns {Promise<Array<{id: number, table_name: string, record_id: string, operation: string, payload: string, created_at: string, retry_count: number, last_error: string|null}>>}
     */
    async getPendingQueue() {
        try {
            const res = await getDb().query(
                `SELECT * FROM sync_queue ORDER BY id ASC`
            );
            return res.values ?? [];
        } catch (err) {
            throw new Error(`[DB] getPendingQueue() failed: ${err.message}`);
        }
    },

    /**
     * Removes successfully synced entries from sync_queue and marks the
     * corresponding rows in their source tables as synced = 1.
     * @param {number[]} ids  Array of sync_queue.id values to remove
     */
    async clearQueue(ids) {
        if (!ids.length) return;
        try {
            const db          = getDb();
            const placeholders = ids.map(() => '?').join(', ');

            // Fetch entries before deleting so we can flip synced flag
            const res = await db.query(
                `SELECT table_name, record_id FROM sync_queue WHERE id IN (${placeholders})`,
                ids
            );
            const entries = res.values ?? [];

            await db.run(
                `DELETE FROM sync_queue WHERE id IN (${placeholders})`,
                ids
            );

            // Mark source rows as synced = 1 (best-effort; ignore unknown tables)
            for (const { table_name, record_id } of entries) {
                if (SYNC_TABLES.has(table_name)) {
                    await db.run(
                        `UPDATE ${table_name} SET synced = 1 WHERE id = ?`,
                        [record_id]
                    );
                }
            }
        } catch (err) {
            throw new Error(`[DB] clearQueue() failed: ${err.message}`);
        }
    },

    /**
     * Increments retry_count and records the last error message for a queue entry.
     * Call this when a sync attempt fails for a specific item.
     * @param {number} queueId  sync_queue.id
     * @param {string} errorMessage
     */
    async markQueueError(queueId, errorMessage) {
        try {
            await getDb().run(
                `UPDATE sync_queue
                 SET retry_count = retry_count + 1, last_error = ?
                 WHERE id = ?`,
                [errorMessage, queueId]
            );
        } catch (err) {
            throw new Error(`[DB] markQueueError(${queueId}) failed: ${err.message}`);
        }
    },

    // -----------------------------------------------------------------------
    // Raw access for sync.js (bypasses enqueue — internal use only)
    // -----------------------------------------------------------------------

    /**
     * Executes a raw SQL statement with bound parameters.
     * Used by sync.js to upsert server records without touching sync_queue.
     * @param {string} sql
     * @param {unknown[]} [params]
     */
    async _rawRun(sql, params = []) {
        try {
            await getDb().run(sql, params);
        } catch (err) {
            throw new Error(`[DB] _rawRun() failed: ${err.message}`);
        }
    },

    /**
     * Returns the first row matching a raw SELECT, or null if no rows found.
     * Used by sync.js to check row existence including soft-deleted rows.
     * @param {string} sql
     * @param {unknown[]} [params]
     * @returns {Promise<Record<string, unknown> | null>}
     */
    async _queryOne(sql, params = []) {
        try {
            const res = await getDb().query(sql, params);
            return res.values?.[0] ?? null;
        } catch (err) {
            throw new Error(`[DB] _queryOne() failed: ${err.message}`);
        }
    },

};
