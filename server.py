#!/usr/bin/env python3
import json
import os
import sqlite3
import threading
from datetime import datetime
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).parent
DB_PATH = ROOT / "data.db"
STATIC = ROOT / "index.html"
MENU_PATH = ROOT / "menu.json"
PORT = int(os.environ.get("PORT", "3000"))
HOST = os.environ.get("HOST", "0.0.0.0")
SESSION_TIMEOUT_SECONDS = int(os.environ.get("SESSION_TIMEOUT_SECONDS", str(6 * 3600)))

_db_lock = threading.Lock()


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    with db() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                status TEXT NOT NULL CHECK(status IN ('open','closed')),
                created_at TEXT NOT NULL,
                closed_at TEXT
            );
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                shop_id TEXT NOT NULL,
                shop TEXT NOT NULL,
                product_id TEXT NOT NULL,
                product TEXT NOT NULL,
                selections TEXT,
                quantity INTEGER NOT NULL DEFAULT 1,
                notes TEXT,
                created_at TEXT NOT NULL
            );
            """
        )


def load_menu():
    with open(MENU_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def find_shop(menu, shop_id):
    for s in menu.get("shops", []):
        if s["id"] == shop_id:
            return s
    return None


def find_product(shop, product_id):
    for p in shop.get("products", []):
        if p["id"] == product_id:
            return p
    return None


def find_option(group, option_id):
    for o in group.get("options", []):
        if o["id"] == option_id:
            return o
    return None


def _expire_if_idle(conn):
    row = conn.execute(
        "SELECT id, created_at FROM sessions WHERE status='open' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if not row:
        return
    last_order = conn.execute(
        "SELECT MAX(created_at) AS ts, COUNT(*) AS c FROM orders WHERE session_id=?",
        (row["id"],),
    ).fetchone()
    last_ts_str = last_order["ts"] if last_order and last_order["ts"] else row["created_at"]
    try:
        last_ts = datetime.fromisoformat(last_ts_str)
    except ValueError:
        return
    if (datetime.now() - last_ts).total_seconds() < SESSION_TIMEOUT_SECONDS:
        return
    now = datetime.now().isoformat(timespec="seconds")
    if last_order and last_order["c"]:
        conn.execute(
            "UPDATE sessions SET status='closed', closed_at=? WHERE id=?",
            (now, row["id"]),
        )
    else:
        conn.execute("DELETE FROM sessions WHERE id=?", (row["id"],))


def ensure_open_session():
    with _db_lock, db() as conn:
        _expire_if_idle(conn)
        row = conn.execute(
            "SELECT * FROM sessions WHERE status='open' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if row:
            return dict(row)
        now = datetime.now().isoformat(timespec="seconds")
        cur = conn.execute(
            "INSERT INTO sessions(status, created_at) VALUES('open', ?)", (now,)
        )
        return {"id": cur.lastrowid, "status": "open", "created_at": now, "closed_at": None}


def get_state():
    session = ensure_open_session()
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM orders WHERE session_id=? ORDER BY id",
            (session["id"],),
        ).fetchall()
    orders = []
    for r in rows:
        d = dict(r)
        d["selections"] = json.loads(d["selections"]) if d["selections"] else []
        orders.append(d)
    return {"session": session, "orders": orders}


def build_order_from_payload(data, menu):
    name = (data.get("name") or "").strip()
    if not name:
        raise ValueError("Διάλεξε όνομα")
    if len(name) > 40:
        raise ValueError("Το όνομα είναι πολύ μεγάλο")

    shop_id = data.get("shop_id") or ""
    shop = find_shop(menu, shop_id)
    if not shop:
        raise ValueError("Διάλεξε μαγαζί")

    raw_qty = data.get("quantity")
    if raw_qty in ("", None):
        quantity = 1
    else:
        try:
            quantity = int(raw_qty)
        except (TypeError, ValueError):
            raise ValueError("Μη έγκυρη ποσότητα")
    if quantity < 1 or quantity > 99:
        raise ValueError("Η ποσότητα πρέπει να είναι 1-99")

    notes = (data.get("notes") or "").strip() or None

    if shop.get("freeform"):
        free_product = (data.get("free_product") or "").strip()
        if not free_product:
            raise ValueError("Γράψε την παραγγελία σου")
        if len(free_product) > 500:
            raise ValueError("Το κείμενο είναι πολύ μεγάλο (όριο 500 χαρακτήρες)")
        return {
            "name": name,
            "shop_id": shop["id"],
            "shop": shop["name"],
            "product_id": "__freeform__",
            "product": free_product,
            "selections": [],
            "quantity": quantity,
            "notes": notes,
        }

    product_id = data.get("product_id") or ""
    product = find_product(shop, product_id)
    if not product:
        raise ValueError("Διάλεξε προϊόν")

    raw_selections = data.get("selections") or {}
    display_selections = []

    for group in product.get("groups", []):
        gid = group["id"]
        gtype = group.get("type", "multi")
        required = bool(group.get("required"))
        raw = raw_selections.get(gid)

        if gtype == "single":
            if raw in ("", None):
                if required:
                    raise ValueError(f"Συμπλήρωσε: {group['name']}")
                continue
            if isinstance(raw, list):
                raise ValueError(f"{group['name']}: πρέπει να είναι μία επιλογή")
            opt = find_option(group, raw)
            if not opt:
                raise ValueError(f"{group['name']}: μη έγκυρη επιλογή")
            display_selections.append({
                "group_id": gid, "group": group["name"],
                "option_ids": [opt["id"]], "values": [opt["name"]],
            })
        else:
            if raw in ("", None):
                raw = []
            if not isinstance(raw, list):
                raise ValueError(f"{group['name']}: περιμένω λίστα επιλογών")
            if required and not raw:
                raise ValueError(f"Συμπλήρωσε: {group['name']}")
            chosen_names = []
            chosen_ids = []
            for oid in raw:
                opt = find_option(group, oid)
                if not opt:
                    raise ValueError(f"{group['name']}: μη έγκυρη επιλογή")
                chosen_names.append(opt["name"])
                chosen_ids.append(opt["id"])
            if chosen_names:
                display_selections.append({
                    "group_id": gid, "group": group["name"],
                    "option_ids": chosen_ids, "values": chosen_names,
                })

    return {
        "name": name,
        "shop_id": shop["id"],
        "shop": shop["name"],
        "product_id": product["id"],
        "product": product["name"],
        "selections": display_selections,
        "quantity": quantity,
        "notes": notes,
    }


def _check_shop_lock(conn, session_id, new_shop_id, exclude_order_id=None):
    if exclude_order_id is not None:
        row = conn.execute(
            "SELECT shop_id, shop FROM orders WHERE session_id=? AND id!=? ORDER BY id LIMIT 1",
            (session_id, exclude_order_id),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT shop_id, shop FROM orders WHERE session_id=? ORDER BY id LIMIT 1",
            (session_id,),
        ).fetchone()
    if row and row["shop_id"] != new_shop_id:
        raise ValueError(
            f"Η παραγγελία έχει κλειδωθεί στο μαγαζί: {row['shop']}. "
            "Όλοι παραγγέλνουν από το ίδιο."
        )


def add_order(data):
    menu = load_menu()
    order = build_order_from_payload(data, menu)
    session = ensure_open_session()
    now = datetime.now().isoformat(timespec="seconds")
    with _db_lock, db() as conn:
        _check_shop_lock(conn, session["id"], order["shop_id"])
        cur = conn.execute(
            "INSERT INTO orders(session_id, name, shop_id, shop, product_id, product, selections, quantity, notes, created_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                session["id"],
                order["name"],
                order["shop_id"],
                order["shop"],
                order["product_id"],
                order["product"],
                json.dumps(order["selections"], ensure_ascii=False),
                order["quantity"],
                order["notes"],
                now,
            ),
        )
        return cur.lastrowid


def update_order(order_id, data):
    menu = load_menu()
    order = build_order_from_payload(data, menu)
    session = ensure_open_session()
    with _db_lock, db() as conn:
        row = conn.execute(
            "SELECT id FROM orders WHERE id=? AND session_id=?",
            (order_id, session["id"]),
        ).fetchone()
        if not row:
            raise ValueError("Η παραγγελία δεν βρέθηκε ή έχει κλείσει")
        _check_shop_lock(conn, session["id"], order["shop_id"], exclude_order_id=order_id)
        conn.execute(
            "UPDATE orders SET name=?, shop_id=?, shop=?, product_id=?, product=?,"
            " selections=?, quantity=?, notes=? WHERE id=?",
            (
                order["name"],
                order["shop_id"],
                order["shop"],
                order["product_id"],
                order["product"],
                json.dumps(order["selections"], ensure_ascii=False),
                order["quantity"],
                order["notes"],
                order_id,
            ),
        )


def delete_order(order_id):
    session = ensure_open_session()
    with _db_lock, db() as conn:
        conn.execute(
            "DELETE FROM orders WHERE id=? AND session_id=?",
            (order_id, session["id"]),
        )


def close_session():
    with _db_lock, db() as conn:
        row = conn.execute(
            "SELECT id FROM sessions WHERE status='open' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if not row:
            return
        now = datetime.now().isoformat(timespec="seconds")
        conn.execute(
            "UPDATE sessions SET status='closed', closed_at=? WHERE id=?",
            (now, row["id"]),
        )


def history():
    with db() as conn:
        rows = conn.execute(
            "SELECT s.*, COUNT(o.id) AS order_count "
            "FROM sessions s LEFT JOIN orders o ON o.session_id=s.id "
            "WHERE s.status='closed' "
            "GROUP BY s.id ORDER BY s.id DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def history_detail(session_id):
    with db() as conn:
        session = conn.execute(
            "SELECT * FROM sessions WHERE id=?", (session_id,)
        ).fetchone()
        if not session:
            return None
        rows = conn.execute(
            "SELECT * FROM orders WHERE session_id=? ORDER BY id",
            (session_id,),
        ).fetchall()
    orders = []
    for r in rows:
        d = dict(r)
        d["selections"] = json.loads(d["selections"]) if d["selections"] else []
        orders.append(d)
    return {"session": dict(session), "orders": orders}


class Handler(BaseHTTPRequestHandler):
    server_version = "FoodVoting/2.1"

    def _send_json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self):
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            raise ValueError("Μη έγκυρο JSON")

    def do_GET(self):
        path = urlparse(self.path).path
        try:
            if path in ("/", "/index.html"):
                self._serve_static()
                return
            if path == "/api/menu":
                self._send_json(200, load_menu())
                return
            if path == "/api/state":
                self._send_json(200, get_state())
                return
            if path == "/api/history":
                self._send_json(200, history())
                return
            if path.startswith("/api/history/"):
                sid = int(path.rsplit("/", 1)[1])
                detail = history_detail(sid)
                if detail is None:
                    self._send_json(404, {"error": "not found"})
                    return
                self._send_json(200, detail)
                return
            self._send_json(404, {"error": "not found"})
        except ValueError as e:
            self._send_json(400, {"error": str(e)})
        except Exception as e:
            self._send_json(500, {"error": str(e)})

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            if path == "/api/orders":
                data = self._read_json()
                oid = add_order(data)
                self._send_json(201, {"id": oid})
                return
            if path == "/api/close":
                close_session()
                ensure_open_session()
                self._send_json(200, {"ok": True})
                return
            self._send_json(404, {"error": "not found"})
        except ValueError as e:
            self._send_json(400, {"error": str(e)})
        except Exception as e:
            self._send_json(500, {"error": str(e)})

    def do_PUT(self):
        path = urlparse(self.path).path
        try:
            if path.startswith("/api/orders/"):
                oid = int(path.rsplit("/", 1)[1])
                data = self._read_json()
                update_order(oid, data)
                self._send_json(200, {"ok": True})
                return
            self._send_json(404, {"error": "not found"})
        except ValueError as e:
            self._send_json(400, {"error": str(e)})
        except Exception as e:
            self._send_json(500, {"error": str(e)})

    def do_DELETE(self):
        path = urlparse(self.path).path
        try:
            if path.startswith("/api/orders/"):
                oid = int(path.rsplit("/", 1)[1])
                delete_order(oid)
                self._send_json(200, {"ok": True})
                return
            self._send_json(404, {"error": "not found"})
        except ValueError as e:
            self._send_json(400, {"error": str(e)})
        except Exception as e:
            self._send_json(500, {"error": str(e)})

    def _serve_static(self):
        try:
            body = STATIC.read_bytes()
        except FileNotFoundError:
            self._send_json(404, {"error": "index.html missing"})
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        print(f"[{self.log_date_time_string()}] {self.address_string()} {fmt % args}")


def main():
    init_db()
    ensure_open_session()
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"Food Voting running on http://{HOST}:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
