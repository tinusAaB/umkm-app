"""
routes/sync.py — Offline-first sync endpoints for UMKM Pro
Blueprint  : sync_bp  (url_prefix='/sync')
Database   : Supabase PostgreSQL via SQLAlchemy
Strategy   : last-write-wins — incoming record wins only when its
             updated_at is strictly newer than the server's copy.

Prerequisites before this blueprint works:
  1. Run migrations/offline_sync/001_supabase_add_sync_columns.sql
     (adds updated_at, deleted_at, device_id to transaksi/produk/pelanggan)
  2. Run migrations/offline_sync/002_supabase_create_sync_queue.sql
     (creates sync_queue_log + v_sync_status)
  3. Move `db = SQLAlchemy(app)` to extensions.py so it can be imported here:
       # extensions.py
       from flask_sqlalchemy import SQLAlchemy
       db = SQLAlchemy()
       # app.py: from extensions import db; db.init_app(app)
  4. Ensure produk/pelanggan/transaksi.id columns are UUID (TEXT) type on the
     server. The existing app uses INTEGER PKs — a schema migration to UUID is
     required for mobile-generated records to sync correctly.
"""

from flask import Blueprint, request, jsonify
from sqlalchemy import text
from datetime import datetime, timezone
import logging

from extensions import db  # see prerequisite note above

log = logging.getLogger(__name__)

sync_bp = Blueprint('sync_bp', __name__, url_prefix='/sync')

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ALLOWED_TABLES = frozenset({'transaksi', 'produk'})

PULL_LIMIT = 1000

# Per-table column whitelists used by _safe_upsert to prevent SQL injection
# when building dynamic INSERT statements from client payloads.
ALLOWED_COLUMNS = {
    'produk': ['id', 'nama', 'harga', 'modal', 'stok', 'katagori',
               'dibuat', 'satuan', 'updated_at', 'deleted_at', 'device_id'],
    'transaksi': ['id', 'pelanggan', 'kasir', 'sub_total', 'ppn', 'total',
                  'item', 'tanggal', 'update_at', 'delete_at', 'device_id'],
}

# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def _serialize_row(row) -> dict:
    """
    Converts a SQLAlchemy Row (or RowMapping) to a plain dict.
    datetime → ISO 8601 string, UUID objects → str, None stays None.
    """
    d = {}
    mapping = row._mapping if hasattr(row, '_mapping') else dict(row)
    for key, val in mapping.items():
        if isinstance(val, datetime):
            d[key] = val.isoformat()
        else:
            d[key] = str(val) if (val is not None and not isinstance(val, (str, int, float, bool))) else val
    return d


def _safe_upsert(table_name: str, payload: dict, device_id: str) -> str:
    """
    Inserts or updates one row using PostgreSQL INSERT … ON CONFLICT (id) DO UPDATE.
    The UPDATE only executes when the incoming updated_at is strictly newer
    than the stored value (last-write-wins).

    Column names are validated against ALLOWED_COLUMNS to prevent SQL injection.
    Values are passed as bound parameters via SQLAlchemy text().

    Returns 'ok' or raises an Exception with a descriptive message.
    """
    if table_name not in ALLOWED_TABLES:
        raise ValueError(f"Table '{table_name}' is not allowed.")

    allowed = ALLOWED_COLUMNS[table_name]
    cols = [c for c in payload if c in allowed]

    if not cols:
        raise ValueError(f"Payload for '{table_name}' contains no recognised columns.")

    if 'id' not in cols:
        raise ValueError(f"Payload for '{table_name}' is missing required field 'id'.")

    # Merge device_id from the request (authoritative) into the row
    if 'device_id' in allowed:
        cols = list(dict.fromkeys([*cols, 'device_id']))  # deduplicate, preserve order

    values = {col: payload.get(col) for col in cols}
    values['device_id'] = device_id

    col_list   = ', '.join(cols)
    ph_list    = ', '.join(f':{c}' for c in cols)
    update_set = ', '.join(
        f'{c} = EXCLUDED.{c}'
        for c in cols
        if c != 'id'
    )

    # Guard: if updated_at / update_at is missing from payload, skip the WHERE
    # guard so the upsert always proceeds (better than silently losing the record).
    # transaksi uses 'update_at'; all other tables use 'updated_at'.
    ts_col = 'update_at' if table_name == 'transaksi' else 'updated_at'
    if ts_col in cols:
        where_clause = f'WHERE EXCLUDED.{ts_col} > {table_name}.{ts_col}'
    else:
        where_clause = ''

    sql = text(f"""
        INSERT INTO {table_name} ({col_list})
        VALUES ({ph_list})
        ON CONFLICT (id) DO UPDATE SET {update_set}
        {where_clause}
    """)

    db.session.execute(sql, values)
    return 'ok'


def _safe_delete(table_name: str, record_id: str) -> str:
    """
    Soft-deletes a row by setting deleted_at = NOW().
    Does not raise if the record does not exist (idempotent).
    Returns 'ok' or raises an Exception.
    """
    if table_name not in ALLOWED_TABLES:
        raise ValueError(f"Table '{table_name}' is not allowed.")

    # transaksi uses 'delete_at'; all other tables use 'deleted_at'.
    del_col = 'delete_at' if table_name == 'transaksi' else 'deleted_at'
    sql = text(f"""
        UPDATE {table_name}
        SET {del_col} = NOW()
        WHERE id = :record_id
          AND {del_col} IS NULL
    """)
    db.session.execute(sql, {'record_id': record_id})
    return 'ok'


def _log_to_queue(
    device_id: str,
    table_name: str,
    record_id: str,
    operation: str,
    payload: dict,
    server_ver: str | None,
) -> None:
    """
    Appends one entry to sync_queue_log for audit/monitoring purposes.
    Failures here are non-fatal and are caught by the caller.
    """
    import json
    sql = text("""
        INSERT INTO sync_queue_log
            (device_id, table_name, record_id, operation, payload, synced_at, server_ver)
        VALUES
            (:device_id, :table_name, :record_id, :operation, :payload, NOW(), :server_ver)
    """)
    db.session.execute(sql, {
        'device_id':  device_id,
        'table_name': table_name,
        'record_id':  record_id,
        'operation':  operation,
        'payload':    json.dumps(payload) if payload else None,
        'server_ver': server_ver,
    })


# ---------------------------------------------------------------------------
# POST /sync/push — mobile → server
# ---------------------------------------------------------------------------

@sync_bp.route('/push', methods=['POST'])
def push():
    """
    Receives a batch of pending operations from one device and applies them
    to the server database.

    Request body:
        {
            "device_id": "<uuid>",
            "items": [
                {
                    "id":         <int>  — sync_queue.id on the device,
                    "table_name": "produk" | "transaksi",
                    "record_id":  "<uuid>",
                    "operation":  "INSERT" | "UPDATE" | "DELETE",
                    "payload":    { ...row fields... },
                    "created_at": "<ISO 8601>"
                },
                ...
            ]
        }

    Response:
        200  { "synced_ids": [...], "failed": [{"id": ..., "error": "..."}] }
        400  { "error": "..." }  — invalid body
        500  { "error": "..." }  — fatal server error
    """
    try:
        body = request.get_json(silent=True)
        if not body or 'device_id' not in body or 'items' not in body:
            return jsonify({'error': 'Body harus berisi device_id dan items.'}), 400

        device_id: str = str(body['device_id'])
        items: list    = body['items']

        if not isinstance(items, list):
            return jsonify({'error': 'Field items harus berupa array.'}), 400

        synced_ids: list[int] = []
        failed: list[dict]    = []

        for item in items:
            item_id    = item.get('id')
            table_name = item.get('table_name', '')
            record_id  = str(item.get('record_id', ''))
            operation  = str(item.get('operation', '')).upper()
            payload    = item.get('payload') or {}
            created_at = item.get('created_at')

            # --- per-item validation ---
            if table_name not in ALLOWED_TABLES:
                failed.append({'id': item_id, 'error': f"table_name '{table_name}' tidak diizinkan."})
                continue

            if operation not in ('INSERT', 'UPDATE', 'DELETE'):
                failed.append({'id': item_id, 'error': f"operation '{operation}' tidak valid."})
                continue

            # --- per-item transaction (rollback single item, not the whole batch) ---
            savepoint = db.session.begin_nested()
            try:
                if operation in ('INSERT', 'UPDATE'):
                    _safe_upsert(table_name, payload, device_id)
                else:
                    _safe_delete(table_name, record_id)

                # Log to audit table (non-fatal if it fails)
                try:
                    server_ver = datetime.now(timezone.utc).isoformat()
                    _log_to_queue(device_id, table_name, record_id, operation, payload, server_ver)
                except Exception as log_err:
                    log.warning('[Sync/push] sync_queue_log insert failed: %s', log_err)

                savepoint.commit()
                synced_ids.append(item_id)

            except Exception as item_err:
                savepoint.rollback()
                log.error(
                    '[Sync/push] item id=%s table=%s op=%s error=%s',
                    item_id, table_name, operation, item_err,
                )
                failed.append({'id': item_id, 'error': str(item_err)})

        # Flush the successful upserts in one go
        try:
            db.session.commit()
        except Exception as commit_err:
            db.session.rollback()
            log.error('[Sync/push] Final commit failed: %s', commit_err)
            return jsonify({'error': f'Commit gagal: {commit_err}'}), 500

        return jsonify({'synced_ids': synced_ids, 'failed': failed})

    except Exception as fatal:
        db.session.rollback()
        log.error('[Sync/push] Fatal error: %s', fatal)
        return jsonify({'error': str(fatal)}), 500


# ---------------------------------------------------------------------------
# GET /sync/pull — server → mobile
# ---------------------------------------------------------------------------

@sync_bp.route('/pull', methods=['GET'])
def pull():
    """
    Returns records that changed on the server since the device's last sync.

    Query params:
        since     (optional) ISO 8601 timestamp — only return records newer than this.
                  Omit for a full pull (capped at PULL_LIMIT rows per table).
        device_id (required) UUID string — records owned by this device are excluded
                  to avoid echoing data the device already has.

    Response:
        200 {
              "records": {
                  "produk":    [...],
                  "transaksi": [...]
              },
              "server_time": "<ISO 8601 UTC now>"
            }
        500 { "error": "..." }
    """
    try:
        device_id = request.args.get('device_id', '')
        since_raw = request.args.get('since', '').strip()

        since: datetime | None = None
        if since_raw:
            try:
                since = datetime.fromisoformat(since_raw.replace('Z', '+00:00'))
            except ValueError:
                return jsonify({'error': f"Parameter 'since' tidak valid: '{since_raw}'."}), 400

        records: dict[str, list] = {}

        for table in ('produk', 'transaksi'):
            try:
                # transaksi uses 'update_at' / 'delete_at'; others use 'updated_at' / 'deleted_at'.
                upd_col = 'update_at' if table == 'transaksi' else 'updated_at'
                del_col = 'delete_at' if table == 'transaksi' else 'deleted_at'
                if since:
                    sql = text(f"""
                        SELECT * FROM {table}
                        WHERE (
                            {upd_col} > :since
                            OR ({del_col} IS NOT NULL AND {del_col} > :since)
                        )
                        AND (device_id IS NULL OR device_id != :device_id)
                        LIMIT :lim
                    """)
                    params = {'since': since, 'device_id': device_id, 'lim': PULL_LIMIT}
                else:
                    sql = text(f"""
                        SELECT * FROM {table}
                        WHERE (device_id IS NULL OR device_id != :device_id)
                        LIMIT :lim
                    """)
                    params = {'device_id': device_id, 'lim': PULL_LIMIT}

                rows = db.session.execute(sql, params).fetchall()
                records[table] = [_serialize_row(r) for r in rows]

            except Exception as tbl_err:
                log.error('[Sync/pull] Query on table %s failed: %s', table, tbl_err)
                records[table] = []

        server_time = datetime.now(timezone.utc).isoformat()
        return jsonify({'records': records, 'server_time': server_time})

    except Exception as fatal:
        log.error('[Sync/pull] Fatal error: %s', fatal)
        return jsonify({'error': str(fatal)}), 500


# ---------------------------------------------------------------------------
# Blueprint registration (add to app.py or __init__.py)
# ---------------------------------------------------------------------------

# Di app.py atau __init__.py tambahkan:
# from routes.sync import sync_bp
# app.register_blueprint(sync_bp)
