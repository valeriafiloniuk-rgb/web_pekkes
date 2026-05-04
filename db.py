import sqlite3
import os

conn = sqlite3.connect("database.db")
cursor = conn.cursor()

# Tabla stock
cursor.execute("""
CREATE TABLE IF NOT EXISTS stock (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    producto TEXT,
    cantidad INTEGER
)
""")

# Tabla donaciones
cursor.execute("""
CREATE TABLE IF NOT EXISTS donaciones (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre_donante TEXT,
    tipo_donante TEXT DEFAULT 'persona',
    tipo_donacion TEXT DEFAULT 'dinero',
    monto REAL,
    detalle TEXT,
    fecha TEXT
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS voluntarios (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre TEXT,
    email TEXT,
    telefono TEXT,
    disponibilidad TEXT,
    mensaje TEXT,
    fecha TEXT
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS login_auditoria (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    usuario_ingresado TEXT,
    usuario_id INTEGER,
    ip_origen TEXT,
    resultado TEXT,
    detalle TEXT,
    fecha TEXT
)
""")

# Tabla usuarios
cursor.execute("""
CREATE TABLE IF NOT EXISTS usuarios (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    usuario TEXT UNIQUE,
    email TEXT UNIQUE,
    password TEXT,
    reset_token TEXT,
    reset_token_expiry TEXT,
    acceso_privado INTEGER DEFAULT 0,
    es_admin INTEGER DEFAULT 0,
    intentos_fallidos INTEGER DEFAULT 0,
    bloqueo_hasta TEXT
)
""")

cursor.execute("PRAGMA table_info(usuarios)")
columnas = {columna[1] for columna in cursor.fetchall()}

if 'acceso_privado' not in columnas:
    cursor.execute("ALTER TABLE usuarios ADD COLUMN acceso_privado INTEGER DEFAULT 0")
if 'es_admin' not in columnas:
    cursor.execute("ALTER TABLE usuarios ADD COLUMN es_admin INTEGER DEFAULT 0")
if 'intentos_fallidos' not in columnas:
    cursor.execute("ALTER TABLE usuarios ADD COLUMN intentos_fallidos INTEGER DEFAULT 0")
if 'bloqueo_hasta' not in columnas:
    cursor.execute("ALTER TABLE usuarios ADD COLUMN bloqueo_hasta TEXT")

# Usuario inicial si no existe
cursor.execute("SELECT id FROM usuarios WHERE usuario = ?", ('admin',))
if cursor.fetchone() is None:
    cursor.execute(
        """
        INSERT INTO usuarios (usuario, email, password, acceso_privado, es_admin)
        VALUES (?, ?, ?, 1, 1)
        """,
        ('admin', 'admin@example.com', '1234')
    )
else:
    cursor.execute(
        "UPDATE usuarios SET acceso_privado = 1, es_admin = 1 WHERE usuario = ?",
        ('admin',)
    )

conn.commit()
conn.close()

print("Base de datos creada")