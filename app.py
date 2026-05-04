from flask import Flask, render_template, request, redirect, session, url_for, flash, send_file
import sqlite3
from functools import wraps
import secrets
from datetime import datetime, timedelta
import os
import unicodedata
from flask_mail import Mail, Message
from dotenv import load_dotenv
from io import BytesIO
from urllib.parse import urlencode
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
import openpyxl
from openpyxl.chart import BarChart, Reference
from openpyxl.chart.label import DataLabelList

# Cargar variables de entorno desde .env
load_dotenv()

# ------------------------------
# CONFIG
# ------------------------------
app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', secrets.token_hex(32))

# Configuración de email
app.config['MAIL_SERVER'] = os.environ.get('MAIL_SERVER', 'smtp.gmail.com')
app.config['MAIL_PORT'] = int(os.environ.get('MAIL_PORT', 587))
app.config['MAIL_USE_TLS'] = os.environ.get('MAIL_USE_TLS', True)
app.config['MAIL_USERNAME'] = os.environ.get('MAIL_USERNAME')
app.config['MAIL_PASSWORD'] = os.environ.get('MAIL_PASSWORD')
app.config['MAIL_DEFAULT_SENDER'] = os.environ.get('MAIL_DEFAULT_SENDER', 'noreply@mundopekkes.com')
# Datos de cuenta para donaciones en dinero (se pueden sobreescribir por .env)
app.config['DONACION_CUENTA_TITULAR'] = os.environ.get('DONACION_CUENTA_TITULAR', 'Merendero Pekkes')
app.config['DONACION_ALIAS'] = os.environ.get('DONACION_ALIAS', 'PEKKES.DONACIONES')
app.config['DONACION_CBU'] = os.environ.get('DONACION_CBU', '0000000000000000000000')
app.config['DONACION_BANCO'] = os.environ.get('DONACION_BANCO', 'Banco a confirmar')
app.config['MAX_CONTENT_LENGTH'] = int(os.environ.get('MAX_UPLOAD_BYTES', 5 * 1024 * 1024))
app.config['LOGIN_MAX_INTENTOS'] = int(os.environ.get('LOGIN_MAX_INTENTOS', 5))
app.config['LOGIN_BLOQUEO_MINUTOS'] = int(os.environ.get('LOGIN_BLOQUEO_MINUTOS', 15))

UPLOADS_DIR = os.path.join(app.root_path, 'uploads', 'comprobantes')
ALLOWED_COMPROBANTE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.webp', '.pdf'}
os.makedirs(UPLOADS_DIR, exist_ok=True)

ACOMPANAMOS_UPLOADS_DIR = os.path.join(app.root_path, 'uploads', 'acompanamos')
ALLOWED_ACOMPANAMOS_IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.webp'}
os.makedirs(ACOMPANAMOS_UPLOADS_DIR, exist_ok=True)


def guardar_comprobante(archivo):
    if archivo is None or not archivo.filename:
        return '', None

    nombre_seguro = secure_filename(archivo.filename)
    extension = os.path.splitext(nombre_seguro)[1].lower()
    if extension not in ALLOWED_COMPROBANTE_EXTENSIONS:
        return None, 'El comprobante debe ser JPG, PNG, WEBP o PDF'

    nombre_archivo = f"donacion_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{secrets.token_hex(4)}{extension}"
    ruta_destino = os.path.join(UPLOADS_DIR, nombre_archivo)
    archivo.save(ruta_destino)
    return nombre_archivo, None


def guardar_foto_acompanamos(archivo):
    if archivo is None or not archivo.filename:
        return '', None

    nombre_seguro = secure_filename(archivo.filename)
    extension = os.path.splitext(nombre_seguro)[1].lower()
    if extension not in ALLOWED_ACOMPANAMOS_IMAGE_EXTENSIONS:
        return None, 'La foto debe ser JPG, PNG o WEBP'

    nombre_archivo = f"acompanamos_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{secrets.token_hex(4)}{extension}"
    ruta_destino = os.path.join(ACOMPANAMOS_UPLOADS_DIR, nombre_archivo)
    archivo.save(ruta_destino)
    return nombre_archivo, None


mail = Mail(app)


def asegurar_esquema_usuarios():
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()

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

    # Migra contraseñas antiguas en texto plano a hash seguro.
    cursor.execute("SELECT id, password FROM usuarios")
    usuarios = cursor.fetchall()
    for usuario_id, password_guardada in usuarios:
        password_actual = password_guardada or ''
        if password_actual and not password_actual.startswith(('pbkdf2:', 'scrypt:')):
            cursor.execute(
                "UPDATE usuarios SET password = ? WHERE id = ?",
                (generate_password_hash(password_actual, method='pbkdf2:sha256', salt_length=16), usuario_id)
            )

    conn.commit()
    conn.close()


def asegurar_esquema_donaciones():
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS donaciones (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nombre_donante TEXT,
        tipo_donante TEXT DEFAULT 'persona',
        tipo_donacion TEXT DEFAULT 'dinero',
        monto REAL DEFAULT 0,
        detalle TEXT,
        fecha TEXT
    )
    """)

    cursor.execute("PRAGMA table_info(donaciones)")
    columnas = {columna[1] for columna in cursor.fetchall()}

    if 'tipo_donante' not in columnas:
        cursor.execute("ALTER TABLE donaciones ADD COLUMN tipo_donante TEXT DEFAULT 'persona'")
    if 'tipo_donacion' not in columnas:
        cursor.execute("ALTER TABLE donaciones ADD COLUMN tipo_donacion TEXT DEFAULT 'dinero'")
    if 'detalle' not in columnas:
        cursor.execute("ALTER TABLE donaciones ADD COLUMN detalle TEXT")
    if 'comprobante_archivo' not in columnas:
        cursor.execute("ALTER TABLE donaciones ADD COLUMN comprobante_archivo TEXT")

    conn.commit()
    conn.close()


def asegurar_esquema_voluntarios():
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()

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

    conn.commit()
    conn.close()


def asegurar_esquema_auditoria_login():
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()

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

    conn.commit()
    conn.close()


def asegurar_esquema_acompanamos():
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS acompanamos_jornadas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            titulo TEXT NOT NULL,
            resumen TEXT NOT NULL,
            fecha_jornada TEXT NOT NULL,
            foto_archivo TEXT,
            creado_por TEXT,
            creado_en TEXT NOT NULL
        )
        """
    )

    cursor.execute("PRAGMA table_info(acompanamos_jornadas)")
    columnas = {columna[1] for columna in cursor.fetchall()}
    if 'foto_archivo' not in columnas:
        cursor.execute("ALTER TABLE acompanamos_jornadas ADD COLUMN foto_archivo TEXT")

    conn.commit()
    conn.close()


def obtener_ip_cliente():
    forwarded_for = request.headers.get('X-Forwarded-For', '')
    if forwarded_for:
        return forwarded_for.split(',')[0].strip()
    return request.remote_addr or 'desconocida'


def registrar_auditoria_login(usuario_ingresado, usuario_id, resultado, detalle, ip_origen):
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO login_auditoria (usuario_ingresado, usuario_id, ip_origen, resultado, detalle, fecha)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            usuario_ingresado,
            usuario_id,
            ip_origen,
            resultado,
            detalle,
            datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        )
    )
    conn.commit()
    conn.close()


asegurar_esquema_donaciones()
asegurar_esquema_voluntarios()
asegurar_esquema_usuarios()
asegurar_esquema_auditoria_login()
asegurar_esquema_acompanamos()

# ==============================
# VALIDACIÓN DE CONTRASEÑA
# ==============================
import re


def validar_texto_claro(texto, nombre_campo, min_len=2, max_len=120, permitir_numeros=True):
    texto_limpio = ' '.join((texto or '').strip().split())

    if not texto_limpio:
        return False, f'Ingresa {nombre_campo}', texto_limpio

    if len(texto_limpio) < min_len:
        return False, f'El campo {nombre_campo} es demasiado corto', texto_limpio

    if len(texto_limpio) > max_len:
        return False, f'El campo {nombre_campo} no puede superar {max_len} caracteres', texto_limpio

    if '<' in texto_limpio or '>' in texto_limpio:
        return False, f'El campo {nombre_campo} contiene caracteres no permitidos', texto_limpio

    if re.search(r'https?://|www\.', texto_limpio, re.IGNORECASE):
        return False, f'El campo {nombre_campo} no debe contener enlaces', texto_limpio

    if permitir_numeros:
        patron = r"^[A-Za-zÁÉÍÓÚÜÑáéíóúüñ0-9\s\.,;:()\-/'\"%&]+$"
    else:
        patron = r"^[A-Za-zÁÉÍÓÚÜÑáéíóúüñ\s\.,;:()\-/'\"%&]+$"

    if not re.match(patron, texto_limpio):
        return False, f'El campo {nombre_campo} contiene símbolos no válidos', texto_limpio

    if not re.search(r'[A-Za-zÁÉÍÓÚÜÑáéíóúüñ]', texto_limpio):
        return False, f'El campo {nombre_campo} debe incluir al menos una letra', texto_limpio

    return True, '', texto_limpio

def validar_contrasena(password):
    """
    Valida que la contraseña cumpla con los requisitos:
    - Mínimo 12 caracteres
    - Al menos una mayúscula
    - Al menos una minúscula
    - Al menos un número
    - Al menos un carácter especial (!@#$%^&*-_=+)
    
    Retorna: (es_valida, mensaje_error)
    """
    errores = []
    
    password = password or ''

    if len(password) < 12:
        errores.append("mínimo 12 caracteres")
    
    if not re.search(r'[A-Z]', password):
        errores.append("al menos una mayúscula (A-Z)")
    
    if not re.search(r'[a-z]', password):
        errores.append("al menos una minúscula (a-z)")

    if not re.search(r'\d', password):
        errores.append("al menos un número (0-9)")
    
    if not re.search(r'[!@#$%^&*\-_=+]', password):
        errores.append("al menos un carácter especial (!@#$%^&*-_=+)")

    password_limpia = password.strip().lower()
    passwords_comunes = {
        'admin123456',
        'administrador123',
        'password123',
        'qwerty123',
        '123456789',
        '1234567890'
    }
    if password_limpia in passwords_comunes:
        errores.append("no usar una contraseña común")
    
    if errores:
        return False, "La contraseña debe tener: " + ", ".join(errores)
    
    return True, ""


def generar_hash_password(password):
    return generate_password_hash(password, method='pbkdf2:sha256', salt_length=16)


def verificar_password(password_guardada, password_ingresada):
    if not password_guardada:
        return False
    if password_guardada.startswith(('pbkdf2:', 'scrypt:')):
        return check_password_hash(password_guardada, password_ingresada or '')
    return password_guardada == (password_ingresada or '')


def validar_usuario(nombre_usuario, es_admin=False, permitir_reservado=False):
    usuario = (nombre_usuario or '').strip()

    if len(usuario) < 4 or len(usuario) > 30:
        return False, 'El usuario debe tener entre 4 y 30 caracteres', usuario

    if not re.match(r'^[A-Za-z][A-Za-z0-9_.-]{3,29}$', usuario):
        return False, 'El usuario debe empezar con letra y solo usar letras, números, punto, guion o guion bajo', usuario

    reservados = {'admin', 'administrador', 'root', 'superuser'}
    if not permitir_reservado and usuario.lower() in reservados:
        return False, 'Elegí un nombre de usuario menos predecible', usuario

    if es_admin and len(usuario) < 6:
        return False, 'Para administradores usá un usuario de al menos 6 caracteres', usuario

    return True, '', usuario


def validar_nombre_producto(producto):
    producto = (producto or '').strip()

    if not producto:
        return False, 'Ingresá el nombre del producto'

    if len(producto) > 100:
        return False, 'El nombre del producto no puede exceder 100 caracteres'

    if not re.search(r'[A-Za-zÁÉÍÓÚáéíóúÑñ]', producto):
        return False, 'El nombre del producto debe contener al menos una letra'

    return True, ''


def normalizar_texto_base(texto):
    texto = (texto or '').strip().lower()
    texto = ''.join(
        caracter for caracter in unicodedata.normalize('NFD', texto)
        if unicodedata.category(caracter) != 'Mn'
    )
    return ' '.join(texto.split())


def variantes_palabra(token):
    variantes = {token}

    if len(token) > 3 and token.endswith('s'):
        variantes.add(token[:-1])
    if len(token) > 4 and token.endswith('es'):
        variantes.add(token[:-2])
    if len(token) > 4 and token.endswith('ces'):
        variantes.add(token[:-3] + 'z')

    return {v for v in variantes if v}


def variantes_producto(producto):
    base = normalizar_texto_base(producto)
    if not base:
        return set()

    variantes = {base}

    # Variante sobre el nombre completo para casos simples: poroto/porotos.
    variantes.update(variantes_palabra(base))

    # Variante sobre el último término para frases: "salsa de tomates" / "salsa de tomate".
    tokens = base.split()
    if tokens:
        ultimo = tokens[-1]
        for v_ultimo in variantes_palabra(ultimo):
            nuevo = ' '.join(tokens[:-1] + [v_ultimo])
            variantes.add(nuevo)

    return variantes


def productos_equivalentes(producto_a, producto_b):
    variantes_a = variantes_producto(producto_a)
    variantes_b = variantes_producto(producto_b)
    return bool(variantes_a.intersection(variantes_b))


def buscar_producto_equivalente(cursor, producto, excluir_id=None):
    cursor.execute("SELECT id, producto, cantidad FROM stock")
    items = cursor.fetchall()

    for item in items:
        item_id = item[0]
        if excluir_id is not None and item_id == excluir_id:
            continue
        if productos_equivalentes(item[1], producto):
            return item

    return None

# ------------------------------
# DECORADOR LOGIN REQUIRED
# ------------------------------
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if "usuario" not in session or not session.get('acceso_privado'):
            session.clear()
            return redirect("/login")
        return f(*args, **kwargs)
    return decorated_function

# ------------------------------
# RUTAS PÚBLICAS
# ------------------------------
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/quienes-somos')
def quienes_somos():
    return render_template('quienes_somos.html')

@app.route('/que-hacemos')
def que_hacemos():
    return render_template('que_hacemos.html')


@app.route('/asi-acompanamos')
def asi_acompanamos():
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT id, titulo, resumen, fecha_jornada, foto_archivo
        FROM acompanamos_jornadas
        ORDER BY fecha_jornada DESC, id DESC
        """,
    )
    filas = cursor.fetchall()
    conn.close()

    jornadas = []
    for fila in filas:
        fecha_mostrar = fila[3]
        try:
            fecha_mostrar = datetime.strptime(fila[3], '%Y-%m-%d').strftime('%d/%m/%Y')
        except (TypeError, ValueError):
            pass
        jornadas.append({
            'id': fila[0],
            'titulo': fila[1],
            'resumen': fila[2],
            'fecha': fecha_mostrar,
            'foto_url': url_for('ver_foto_asi_acompanamos', jornada_id=fila[0]) if fila[4] else '',
        })

    return render_template('asi_acompanamos.html', jornadas=jornadas)


@app.route('/asi-acompanamos/foto/<int:jornada_id>')
def ver_foto_asi_acompanamos(jornada_id):
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    cursor.execute("SELECT foto_archivo FROM acompanamos_jornadas WHERE id = ?", (jornada_id,))
    fila = cursor.fetchone()
    conn.close()

    if not fila or not fila[0]:
        return render_template('no_encontrado.html'), 404

    nombre_archivo = os.path.basename(fila[0])
    ruta_archivo = os.path.join(ACOMPANAMOS_UPLOADS_DIR, nombre_archivo)
    if not os.path.exists(ruta_archivo):
        return render_template('no_encontrado.html'), 404

    return send_file(ruta_archivo, as_attachment=False)


@app.route('/voluntariado', methods=['GET', 'POST'])
def voluntariado():
    if request.method == 'POST':
        nombre = request.form.get('nombre', '').strip()
        email = request.form.get('email', '').strip()
        telefono = request.form.get('telefono', '').strip()
        disponibilidad = request.form.get('disponibilidad', '').strip()
        mensaje = request.form.get('mensaje', '').strip()

        if not nombre or not email or not mensaje:
            return render_template(
                'voluntariado.html',
                error='Completa nombre, email y contanos como te gustaria colaborar.',
                nombre=nombre,
                email=email,
                telefono=telefono,
                disponibilidad=disponibilidad,
                mensaje=mensaje
            )

        if not re.match(r'^[^@\s]+@[^@\s]+\.[^@\s]+$', email):
            return render_template(
                'voluntariado.html',
                error='Ingresa un email valido.',
                nombre=nombre,
                email=email,
                telefono=telefono,
                disponibilidad=disponibilidad,
                mensaje=mensaje
            )

        conn = sqlite3.connect('database.db')
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO voluntarios (nombre, email, telefono, disponibilidad, mensaje, fecha)
            VALUES (?, ?, ?, ?, ?, DATE('now'))
            """,
            (nombre, email, telefono, disponibilidad, mensaje)
        )
        conn.commit()
        conn.close()

        return render_template('voluntario_gracias.html', nombre=nombre)

    return render_template(
        'voluntariado.html',
        error=None,
        nombre='',
        email='',
        telefono='',
        disponibilidad='',
        mensaje=''
    )

@app.route('/donaciones', methods=['GET', 'POST'])
def donaciones():
    def contexto_donaciones(extra=None):
        contexto = {
            'cuenta_titular': app.config['DONACION_CUENTA_TITULAR'],
            'cuenta_alias': app.config['DONACION_ALIAS'],
            'cuenta_cbu': app.config['DONACION_CBU'],
            'cuenta_banco': app.config['DONACION_BANCO']
        }
        if extra:
            contexto.update(extra)
        return contexto

    if request.method == 'POST':
        nombre = request.form.get('nombre', '').strip()
        tipo_donante = request.form.get('tipo_donante', 'persona').strip().lower()
        tipo_donacion = request.form.get('tipo_donacion', 'dinero').strip().lower()
        monto_str = request.form.get('monto', '').strip()
        detalle = request.form.get('detalle', '').strip()
        comprobante = request.files.get('comprobante')

        tipos_donante_validos = {'persona', 'entidad'}
        tipos_donacion_validos = {'dinero', 'alimentos', 'articulos'}
        
        nombre_valido, error_nombre, nombre = validar_texto_claro(
            nombre,
            'un nombre claro',
            min_len=2,
            max_len=100,
            permitir_numeros=True
        )
        if not nombre_valido:
            return render_template(
                'donaciones.html',
                **contexto_donaciones({
                    'error': error_nombre,
                    'nombre': nombre,
                    'monto': monto_str,
                    'tipo_donante': tipo_donante,
                    'tipo_donacion': tipo_donacion,
                    'detalle': detalle
                })
            )

        if tipo_donante not in tipos_donante_validos:
            return render_template(
                'donaciones.html',
                **contexto_donaciones({
                    'error': 'Tipo de donante no valido',
                    'nombre': nombre,
                    'monto': monto_str,
                    'tipo_donante': 'persona',
                    'tipo_donacion': tipo_donacion,
                    'detalle': detalle
                })
            )

        if tipo_donacion not in tipos_donacion_validos:
            return render_template(
                'donaciones.html',
                **contexto_donaciones({
                    'error': 'Tipo de donacion no valido',
                    'nombre': nombre,
                    'monto': monto_str,
                    'tipo_donante': tipo_donante,
                    'tipo_donacion': 'dinero',
                    'detalle': detalle
                })
            )

        if tipo_donacion == 'dinero':
            try:
                monto = float(monto_str)
                if monto <= 0:
                    return render_template(
                        'donaciones.html',
                        **contexto_donaciones({
                            'error': 'El monto debe ser mayor a 0',
                            'nombre': nombre,
                            'monto': monto_str,
                            'tipo_donante': tipo_donante,
                            'tipo_donacion': tipo_donacion,
                            'detalle': detalle
                        })
                    )
            except ValueError:
                return render_template(
                    'donaciones.html',
                    **contexto_donaciones({
                        'error': 'Por favor ingresa un monto valido',
                        'nombre': nombre,
                        'monto': monto_str,
                        'tipo_donante': tipo_donante,
                        'tipo_donacion': tipo_donacion,
                        'detalle': detalle
                    })
                )

            if comprobante is None or not comprobante.filename:
                return render_template(
                    'donaciones.html',
                    **contexto_donaciones({
                        'error': 'Para donaciones en dinero debes adjuntar el comprobante',
                        'nombre': nombre,
                        'monto': monto_str,
                        'tipo_donante': tipo_donante,
                        'tipo_donacion': tipo_donacion,
                        'detalle': detalle
                    })
                )

            comprobante_archivo, error_archivo = guardar_comprobante(comprobante)
            if error_archivo:
                return render_template(
                    'donaciones.html',
                    **contexto_donaciones({
                        'error': error_archivo,
                        'nombre': nombre,
                        'monto': monto_str,
                        'tipo_donante': tipo_donante,
                        'tipo_donacion': tipo_donacion,
                        'detalle': detalle
                    })
                )
            detalle_guardado = ''
        else:
            monto = 0
            detalle_valido, error_detalle, detalle = validar_texto_claro(
                detalle,
                'un detalle claro de la donación',
                min_len=6,
                max_len=300,
                permitir_numeros=True
            )
            if not detalle_valido:
                return render_template(
                    'donaciones.html',
                    **contexto_donaciones({
                        'error': error_detalle,
                        'nombre': nombre,
                        'monto': monto_str,
                        'tipo_donante': tipo_donante,
                        'tipo_donacion': tipo_donacion,
                        'detalle': detalle
                    })
                )
            detalle_guardado = detalle
            comprobante_archivo = ''
        
        conn = sqlite3.connect('database.db')
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO donaciones (nombre_donante, tipo_donante, tipo_donacion, monto, detalle, comprobante_archivo, fecha)
            VALUES (?, ?, ?, ?, ?, ?, DATE('now'))
            """,
            (nombre, tipo_donante, tipo_donacion, monto, detalle_guardado, comprobante_archivo)
        )
        conn.commit()
        conn.close()
        
        fecha_actual = datetime.now().strftime('%d/%m/%Y')
        return render_template(
            'donacion_gracias.html',
            **contexto_donaciones({
                'nombre': nombre,
                'tipo_donante': tipo_donante,
                'tipo_donacion': tipo_donacion,
                'monto': monto,
                'detalle': detalle_guardado,
                'comprobante_archivo': comprobante_archivo,
                'fecha_actual': fecha_actual
            })
        )
    
    return render_template(
        'donaciones.html',
        **contexto_donaciones({
            'error': None,
            'nombre': '',
            'monto': '',
            'tipo_donante': 'persona',
            'tipo_donacion': 'dinero',
            'detalle': ''
        })
    )

# ------------------------------
# LOGIN / LOGOUT
# ------------------------------
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        session.clear() 
        usuario = request.form["usuario"].strip()
        password = request.form["password"]
        ip_origen = obtener_ip_cliente()

        conn = sqlite3.connect('database.db')
        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT id, usuario, password, acceso_privado, es_admin, intentos_fallidos, bloqueo_hasta
            FROM usuarios
            WHERE lower(usuario)=lower(?)
            """,
            (usuario,)
        )

        user = cursor.fetchone()
        ahora = datetime.now()

        if user and user[6]:
            try:
                bloqueo_hasta = datetime.fromisoformat(user[6])
                if bloqueo_hasta > ahora:
                    minutos_restantes = max(1, int((bloqueo_hasta - ahora).total_seconds() // 60) + 1)
                    registrar_auditoria_login(
                        usuario_ingresado=usuario,
                        usuario_id=user[0],
                        resultado='bloqueado',
                        detalle=f'Cuenta bloqueada, faltan {minutos_restantes} minuto(s)',
                        ip_origen=ip_origen
                    )
                    conn.close()
                    return render_template(
                        "login.html",
                        error=True,
                        error_msg=f'Cuenta bloqueada temporalmente. Intenta de nuevo en {minutos_restantes} minuto(s).'
                    )
                cursor.execute(
                    "UPDATE usuarios SET intentos_fallidos = 0, bloqueo_hasta = NULL WHERE id = ?",
                    (user[0],)
                )
                conn.commit()
                user = (user[0], user[1], user[2], user[3], user[4], 0, None)
            except ValueError:
                cursor.execute(
                    "UPDATE usuarios SET intentos_fallidos = 0, bloqueo_hasta = NULL WHERE id = ?",
                    (user[0],)
                )
                conn.commit()
                user = (user[0], user[1], user[2], user[3], user[4], 0, None)

        if not user or not verificar_password(user[2], password):
            if user:
                nuevos_intentos = int(user[5] or 0) + 1
                max_intentos = app.config['LOGIN_MAX_INTENTOS']
                bloqueo_minutos = app.config['LOGIN_BLOQUEO_MINUTOS']

                if nuevos_intentos >= max_intentos:
                    bloqueo_hasta = (ahora + timedelta(minutes=bloqueo_minutos)).isoformat()
                    cursor.execute(
                        "UPDATE usuarios SET intentos_fallidos = 0, bloqueo_hasta = ? WHERE id = ?",
                        (bloqueo_hasta, user[0])
                    )
                    conn.commit()
                    registrar_auditoria_login(
                        usuario_ingresado=usuario,
                        usuario_id=user[0],
                        resultado='bloqueado',
                        detalle=f'Demasiados intentos fallidos. Bloqueo por {bloqueo_minutos} minutos',
                        ip_origen=ip_origen
                    )
                    conn.close()
                    return render_template(
                        "login.html",
                        error=True,
                        error_msg=f'Demasiados intentos fallidos. Cuenta bloqueada por {bloqueo_minutos} minutos.'
                    )

                cursor.execute(
                    "UPDATE usuarios SET intentos_fallidos = ? WHERE id = ?",
                    (nuevos_intentos, user[0])
                )
                conn.commit()
                registrar_auditoria_login(
                    usuario_ingresado=usuario,
                    usuario_id=user[0],
                    resultado='fallido',
                    detalle='Contraseña incorrecta',
                    ip_origen=ip_origen
                )
            else:
                registrar_auditoria_login(
                    usuario_ingresado=usuario,
                    usuario_id=None,
                    resultado='fallido',
                    detalle='Usuario inexistente',
                    ip_origen=ip_origen
                )

            conn.close()
            return render_template("login.html", error=True, error_msg='Usuario o contraseña incorrectos')

        if int(user[3] or 0) != 1:
            registrar_auditoria_login(
                usuario_ingresado=usuario,
                usuario_id=user[0],
                resultado='denegado',
                detalle='Cuenta sin acceso privado habilitado',
                ip_origen=ip_origen
            )
            conn.close()
            return render_template("login.html", error=True, error_msg='Tu cuenta no tiene acceso a la parte privada')

        if user:
            if user[2] and not user[2].startswith(('pbkdf2:', 'scrypt:')):
                cursor.execute(
                    "UPDATE usuarios SET password = ? WHERE id = ?",
                    (generar_hash_password(password), user[0])
                )
            cursor.execute(
                "UPDATE usuarios SET intentos_fallidos = 0, bloqueo_hasta = NULL WHERE id = ?",
                (user[0],)
            )
            conn.commit()
            conn.close()
            session["usuario"] = user[1]
            session["usuario_id"] = user[0]
            session["acceso_privado"] = True
            session["es_admin"] = bool(user[4])
            registrar_auditoria_login(
                usuario_ingresado=usuario,
                usuario_id=user[0],
                resultado='exitoso',
                detalle='Inicio de sesión correcto',
                ip_origen=ip_origen
            )
            return redirect("/autogestion")

        conn.close()
        return render_template("login.html", error=True, error_msg='No fue posible iniciar sesión')

    return render_template("login.html", error=False, error_msg='')
@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")

# REGISTRO
@app.route('/registro', methods=['GET', 'POST'])
def registro():
    if request.method == 'POST':
        usuario_form = request.form.get('usuario', '')
        email = request.form.get('email', '').strip().lower()
        password = request.form['password']

        usuario_valido, mensaje_usuario, usuario = validar_usuario(usuario_form, es_admin=False)
        if not usuario_valido:
            return render_template('registro.html', error=mensaje_usuario)
        
        # Validar contraseña
        valida, mensaje = validar_contrasena(password)
        if not valida:
            return render_template('registro.html', error=mensaje)
        
        conn = sqlite3.connect('database.db')
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM usuarios WHERE usuario = ? OR email = ?", (usuario, email))
        if cursor.fetchone():
            conn.close()
            return render_template('registro.html', error='Usuario o email ya existe')

        cursor.execute(
            "INSERT INTO usuarios (usuario, email, password, acceso_privado, es_admin) VALUES (?, ?, ?, 0, 0)",
            (usuario, email, generar_hash_password(password))
        )
        conn.commit()
        conn.close()
        return render_template(
            'registro.html',
            error=None,
            exito='Registro creado. Un administrador debe habilitar tu acceso privado.'
        )
    return render_template('registro.html', error=None, exito=None)

# OLVIDE CONTRASEÑA
@app.route('/olvide-contrasena', methods=['GET', 'POST'])
def olvide_contrasena():
    if request.method == 'POST':
        email = request.form['email']
        conn = sqlite3.connect('database.db')
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM usuarios WHERE email = ?", (email,))
        user = cursor.fetchone()
        
        if user:
            token = secrets.token_urlsafe(32)
            expiry = (datetime.now() + timedelta(minutes=30)).isoformat()
            cursor.execute(
                "UPDATE usuarios SET reset_token=?, reset_token_expiry=? WHERE email=?",
                (token, expiry, email)
            )
            conn.commit()
            reset_link = f"http://127.0.0.1:5000/resetear-contrasena/{token}"
            
            # Intentar enviar email
            if app.config['MAIL_USERNAME'] and app.config['MAIL_PASSWORD']:
                try:
                    msg = Message(
                        subject='Recuperar contraseña - Mundo Pekkes',
                        recipients=[email],
                        html=f"""
                        <h2>Recupera tu contraseña</h2>
                        <p>Haz click en el enlace para resetear tu contraseña (válido 30 minutos):</p>
                        <p><a href="{reset_link}">{reset_link}</a></p>
                        <p>Si no solicitaste esto, ignora este email.</p>
                        """
                    )
                    mail.send(msg)
                    conn.close()
                    return render_template('recuperacion_enviada.html', reset_link=reset_link)
                except Exception as e:
                    # Si falla el envío, mostrar enlace en pantalla
                    conn.close()
                    return render_template('recuperacion_enviada.html', reset_link=reset_link)
            else:
                # Si no hay credenciales configuradas
                conn.close()
                return render_template('recuperacion_enviada.html', reset_link=reset_link)
        
        conn.close()
        return "Se ha enviado un enlace de recuperación si el email está registrado."
    return render_template('olvide_contrasena.html')

# RESETEAR CONTRASEÑA
@app.route('/resetear-contrasena/<token>', methods=['GET', 'POST'])
def resetear_contrasena(token):
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM usuarios WHERE reset_token=?", (token,))
    user = cursor.fetchone()
    
    if not user:
        conn.close()
        return render_template('error_recuperacion.html', titulo='Token inválido', mensaje='El enlace de recuperación no es válido. Solicita uno nuevo.')
    
    try:
        token_expiry = datetime.fromisoformat(user[5])
        if token_expiry < datetime.now():
            conn.close()
            return render_template('error_recuperacion.html', titulo='Enlace expirado', mensaje='El enlace de recuperación ha expirado. Solicita uno nuevo para continuar.')
    except:
        conn.close()
        return render_template('error_recuperacion.html', titulo='Token inválido', mensaje='El enlace de recuperación no es válido. Solicita uno nuevo.')
    
    if request.method == 'POST':
        password = request.form['password']
        
        # Validar contraseña
        valida, mensaje = validar_contrasena(password)
        if not valida:
            return render_template('resetear_contrasena.html', token=token, error=mensaje)
        
        cursor.execute(
            "UPDATE usuarios SET password=?, reset_token=NULL, reset_token_expiry=NULL WHERE id=?",
            (generar_hash_password(password), user[0])
        )
        conn.commit()
        conn.close()
        return render_template('contrasena_actualizada.html')
    
    conn.close()
    return render_template('resetear_contrasena.html', token=token, error=None)

# ------------------------------
# RUTAS PROTEGIDAS
# ------------------------------
@app.route('/autogestion')
@login_required
def autogestion():
     return render_template('autogestion.html')


@app.route('/autogestion/cambiar-contrasena', methods=['GET', 'POST'])
@login_required
def cambiar_contrasena_autogestion():
    usuario_id = session.get('usuario_id')
    usuario_actual = session.get('usuario', '')

    def render_cambiar(error=None, exito=None):
        return render_template(
            'cambiar_contrasena.html',
            error=error,
            exito=exito,
            usuario_actual=usuario_actual
        )

    if not usuario_id:
        session.clear()
        return redirect('/login')

    if request.method == 'POST':
        password_actual = request.form.get('password_actual', '')
        password_nueva = request.form.get('password_nueva', '')
        password_confirmacion = request.form.get('password_confirmacion', '')

        if not password_actual or not password_nueva or not password_confirmacion:
            return render_cambiar(error='Completa todos los campos para cambiar tu contraseña.')

        conn = sqlite3.connect('database.db')
        cursor = conn.cursor()
        cursor.execute("SELECT password FROM usuarios WHERE id = ?", (usuario_id,))
        fila = cursor.fetchone()

        if not fila:
            conn.close()
            session.clear()
            return redirect('/login')

        if not verificar_password(fila[0], password_actual):
            conn.close()
            return render_cambiar(error='La contraseña actual no es correcta.')

        if password_nueva != password_confirmacion:
            conn.close()
            return render_cambiar(error='La nueva contraseña y su confirmación no coinciden.')

        if password_nueva == password_actual:
            conn.close()
            return render_cambiar(error='La nueva contraseña debe ser diferente a la actual.')

        valida, mensaje = validar_contrasena(password_nueva)
        if not valida:
            conn.close()
            return render_cambiar(error=mensaje)

        cursor.execute(
            "UPDATE usuarios SET password = ? WHERE id = ?",
            (generar_hash_password(password_nueva), usuario_id)
        )
        conn.commit()
        conn.close()

        return render_cambiar(exito='Contraseña actualizada correctamente.')

    return render_cambiar()
    

# STOCK
@app.route('/autogestion/stock')
@login_required
def stock():
    busqueda = request.args.get('q', '').strip()
    estado = request.args.get('estado', '').strip().lower()
    pagina_str = request.args.get('pagina', '1').strip()

    try:
        pagina = int(pagina_str)
        if pagina < 1:
            raise ValueError
    except ValueError:
        pagina = 1

    por_pagina = 6

    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()

    condiciones = []
    parametros = []

    if busqueda:
        condiciones.append("LOWER(producto) LIKE ?")
        parametros.append(f"{busqueda.lower()}%")

    if estado == 'sin-stock':
        condiciones.append("cantidad <= 0")
    elif estado == 'bajo-stock':
        condiciones.append("cantidad BETWEEN 1 AND 5")
    elif estado == 'disponible':
        condiciones.append("cantidad > 5")
    else:
        estado = ''

    where_clause = ''
    if condiciones:
        where_clause = " WHERE " + " AND ".join(condiciones)

    cursor.execute(
        "SELECT COUNT(*) FROM stock" + where_clause,
        parametros
    )
    total_resultados = cursor.fetchone()[0]

    total_paginas = max(1, (total_resultados + por_pagina - 1) // por_pagina)
    if pagina > total_paginas:
        pagina = total_paginas

    offset = (pagina - 1) * por_pagina
    consulta = "SELECT * FROM stock" + where_clause + " ORDER BY LOWER(producto), id LIMIT ? OFFSET ?"

    parametros_paginados = list(parametros)
    parametros_paginados.extend([por_pagina, offset])

    cursor.execute(consulta, parametros_paginados)
    items = cursor.fetchall()
    conn.close()

    query_base = {}
    if busqueda:
        query_base['q'] = busqueda
    if estado:
        query_base['estado'] = estado

    def build_stock_page_url(numero_pagina):
        params = dict(query_base)
        params['pagina'] = numero_pagina
        return '/autogestion/stock?' + urlencode(params)

    inicio_resultado = offset + 1 if total_resultados > 0 else 0
    fin_resultado = min(offset + len(items), total_resultados)

    max_paginas_visibles = 6
    si_hay_muchas = total_paginas > max_paginas_visibles

    if si_hay_muchas:
        margen = max_paginas_visibles // 2
        pagina_inicio = max(1, pagina - margen)
        pagina_fin = min(total_paginas, pagina_inicio + max_paginas_visibles - 1)

        if pagina_fin - pagina_inicio + 1 < max_paginas_visibles:
            pagina_inicio = max(1, pagina_fin - max_paginas_visibles + 1)

        rango_paginas = range(pagina_inicio, pagina_fin + 1)
        muestra_primera = pagina_inicio > 1
        muestra_ultima = pagina_fin < total_paginas
    else:
        rango_paginas = range(1, total_paginas + 1)
        muestra_primera = False
        muestra_ultima = False

    return render_template(
        'stock.html',
        items=items,
        filtros={
            'q': busqueda,
            'estado': estado
        },
        total_resultados=total_resultados,
        paginacion={
            'pagina_actual': pagina,
            'total_paginas': total_paginas,
            'tiene_anterior': pagina > 1,
            'tiene_siguiente': pagina < total_paginas,
            'url_anterior': build_stock_page_url(pagina - 1) if pagina > 1 else '',
            'url_siguiente': build_stock_page_url(pagina + 1) if pagina < total_paginas else '',
            'inicio_resultado': inicio_resultado,
            'fin_resultado': fin_resultado,
            'por_pagina': por_pagina,
            'urls_paginas': [
                {
                    'numero': numero,
                    'url': build_stock_page_url(numero),
                    'actual': numero == pagina
                }
                for numero in rango_paginas
            ],
            'muestra_primera': muestra_primera,
            'muestra_ultima': muestra_ultima,
            'url_primera': build_stock_page_url(1),
            'url_ultima': build_stock_page_url(total_paginas)
        }
    )

@app.route('/autogestion/stock/agregar', methods=['GET', 'POST'])
@login_required
def agregar_stock():
    if request.method == 'POST':
        producto = request.form.get('producto', '').strip()
        cantidad_raw = request.form.get('cantidad', '').strip()

        producto_valido, mensaje_producto = validar_nombre_producto(producto)
        if not producto_valido:
            flash(mensaje_producto, 'error')
            return render_template('agregar_stock.html', form_data={'producto': producto, 'cantidad': cantidad_raw})

        try:
            if not cantidad_raw or str(int(cantidad_raw)) != cantidad_raw:
                raise ValueError
            cantidad = int(cantidad_raw)
            if cantidad < 1 or cantidad > 999999:
                flash('La cantidad debe ser un número entero entre 1 y 999999', 'error')
                return render_template('agregar_stock.html', form_data={'producto': producto, 'cantidad': cantidad_raw})
        except (ValueError, TypeError):
            flash('La cantidad debe ser un número entero entre 1 y 999999', 'error')
            return render_template('agregar_stock.html', form_data={'producto': producto, 'cantidad': cantidad_raw})

        conn = sqlite3.connect('database.db')
        cursor = conn.cursor()
        
        # Verificar si ya existe el mismo producto con variantes de escritura.
        existing = buscar_producto_equivalente(cursor, producto)
        
        if existing:
            # Producto existe, mostrar confirmación
            session['pending_product'] = existing[1]
            session['pending_quantity'] = cantidad
            session['existing_id'] = existing[0]
            session['existing_quantity'] = existing[2]
            conn.close()
            return render_template('confirmar_agregar_stock.html', 
                                 producto=existing[1], 
                                 cantidad_existente=existing[2], 
                                 cantidad_nueva=cantidad)
        else:
            # Producto nuevo, insertar directamente
            cursor.execute(
                "INSERT INTO stock (producto, cantidad) VALUES (?, ?)",
                (producto, cantidad)
            )
            conn.commit()
            conn.close()
            flash(f'Se agregó "{producto}" con cantidad {cantidad}', 'success')
            return redirect('/autogestion/stock')

    return render_template('agregar_stock.html', form_data={'producto': '', 'cantidad': ''})

@app.route('/autogestion/stock/confirmar_agregar', methods=['POST'])
@login_required
def confirmar_agregar_stock():
    action = request.form.get('action')

    if action == 'sumar':
        cantidad = session.get('pending_quantity')
        existing_id = session.get('existing_id')

        if existing_id is not None and cantidad is not None:
            conn = sqlite3.connect('database.db')
            cursor = conn.cursor()

            cursor.execute("SELECT producto, cantidad FROM stock WHERE id = ?", (existing_id,))
            fila = cursor.fetchone()

            if fila:
                producto = fila[0]
                cantidad_actual = int(fila[1] or 0)
                cantidad_a_sumar = int(cantidad)

                cursor.execute(
                    "UPDATE stock SET cantidad = cantidad + ? WHERE id = ?",
                    (cantidad_a_sumar, existing_id)
                )
                conn.commit()
                conn.close()

                total = cantidad_actual + cantidad_a_sumar
                flash(f'Se sumaron {cantidad_a_sumar} unidades a "{producto}". Total actual: {total}', 'success')
            else:
                conn.close()
                flash('No se pudo actualizar: el producto no existe', 'error')
        else:
            flash('No se encontraron datos pendientes para sumar stock', 'error')

    elif action == 'cancelar':
        flash('No se aplicaron cambios al stock', 'info')
    else:
        flash('Accion no valida', 'error')

    # Limpiar sesión
    session.pop('pending_product', None)
    session.pop('pending_quantity', None)
    session.pop('existing_id', None)
    session.pop('existing_quantity', None)

    return redirect('/autogestion/stock')

@app.route('/autogestion/stock/editar/<int:id>', methods=['GET', 'POST'])
@login_required
def editar_stock(id):
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM stock WHERE id = ?", (id,))
    item = cursor.fetchone()
    if not item:
        conn.close()
        return redirect('/autogestion/stock')

    if request.method == 'POST':
        producto = request.form.get('producto', '').strip()
        cantidad_raw = request.form.get('cantidad', '').strip()

        producto_valido, mensaje_producto = validar_nombre_producto(producto)
        if not producto_valido:
            flash(mensaje_producto, 'error')
            item = (item[0], producto, cantidad_raw)
            return render_template('editar_stock.html', item=item)

        try:
            if not cantidad_raw or str(int(cantidad_raw)) != cantidad_raw:
                raise ValueError
            cantidad = int(cantidad_raw)
            if cantidad < 1 or cantidad > 999999:
                flash('La cantidad debe ser un número entero entre 1 y 999999', 'error')
                item = (item[0], producto, cantidad_raw)
                return render_template('editar_stock.html', item=item)
        except (ValueError, TypeError):
            flash('La cantidad debe ser un número entero entre 1 y 999999', 'error')
            item = (item[0], producto, cantidad_raw)
            return render_template('editar_stock.html', item=item)

        duplicado = buscar_producto_equivalente(cursor, producto, excluir_id=id)
        if duplicado:
            flash(
                f'Ya existe un producto equivalente registrado como "{duplicado[1]}". ' \
                'Edita ese registro o cambia el nombre para evitar duplicados.',
                'error'
            )
            item = (item[0], producto, cantidad_raw)
            return render_template('editar_stock.html', item=item)
        
        cursor.execute(
            "UPDATE stock SET producto = ?, cantidad = ? WHERE id = ?",
            (producto, cantidad, id)
        )
        conn.commit()
        conn.close()
        flash(f'Se actualizó el producto "{producto}"', 'success')
        return redirect('/autogestion/stock')

    conn.close()
    return render_template('editar_stock.html', item=item)

@app.route('/autogestion/stock/eliminar/<int:id>')
@login_required
def eliminar_stock(id):
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    cursor.execute("SELECT producto FROM stock WHERE id = ?", (id,))
    fila = cursor.fetchone()
    cursor.execute("DELETE FROM stock WHERE id = ?", (id,))
    conn.commit()
    conn.close()
    if fila:
        flash(f'Se eliminó "{fila[0]}" del stock', 'success')
    else:
        flash('El producto ya no existe o no se encontró', 'info')
    return redirect('/autogestion/stock')


@app.route('/autogestion/stock/usar', methods=['POST'])
@login_required
def usar_stock():
    id_str = request.form.get('id', '').strip()
    cantidad_str = request.form.get('cantidad_usada', '').strip()

    if not id_str:
        flash('Producto inválido para descontar stock', 'error')
        return redirect('/autogestion/stock')

    try:
        item_id = int(id_str)
        if item_id < 1:
            raise ValueError
    except (ValueError, TypeError):
        flash('ID de producto inválido', 'error')
        return redirect('/autogestion/stock')

    if not cantidad_str:
        flash('Ingresá la cantidad a descontar', 'error')
        return redirect('/autogestion/stock')

    try:
        cantidad_usada = int(cantidad_str)
        if cantidad_usada < 1:
            raise ValueError
        if cantidad_usada > 999999:
            raise ValueError
    except (ValueError, TypeError):
        flash('La cantidad debe ser un número válido entre 1 y 999999', 'error')
        return redirect('/autogestion/stock')

    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    cursor.execute("SELECT producto, cantidad FROM stock WHERE id = ?", (item_id,))
    fila = cursor.fetchone()

    if not fila:
        conn.close()
        flash('El producto no existe o fue eliminado', 'error')
        return redirect('/autogestion/stock')

    producto = fila[0]
    cantidad_actual = int(fila[1] or 0)

    if cantidad_usada > cantidad_actual:
        conn.close()
        flash(
            f'No hay stock suficiente para "{producto}". Disponible: {cantidad_actual}',
            'error'
        )
        return redirect('/autogestion/stock')

    nueva_cantidad = cantidad_actual - cantidad_usada
    cursor.execute(
        "UPDATE stock SET cantidad = ? WHERE id = ?",
        (nueva_cantidad, item_id)
    )
    conn.commit()
    conn.close()

    flash(
        f'Se descontaron {cantidad_usada} unidades de "{producto}". Stock actual: {nueva_cantidad}',
        'success'
    )
    return redirect('/autogestion/stock')

# DONAR
@app.route('/autogestion/donar', methods=['POST'])
@login_required
def donar():
    nombre = request.form.get('nombre', '').strip()
    tipo_donante = request.form.get('tipo_donante', 'persona').strip().lower()
    tipo_donacion = request.form.get('tipo_donacion', 'dinero').strip().lower()
    monto_str = request.form.get('monto', '').strip()
    detalle = request.form.get('detalle', '').strip()
    comprobante = request.files.get('comprobante')

    nombre_valido, error_nombre, nombre = validar_texto_claro(
        nombre,
        'un nombre claro',
        min_len=2,
        max_len=100,
        permitir_numeros=True
    )
    if not nombre_valido:
        flash(error_nombre, 'error')
        return redirect('/autogestion/donaciones')

    if tipo_donacion == 'dinero':
        try:
            monto = float(monto_str)
            if monto <= 0:
                return redirect('/autogestion/donaciones')
        except ValueError:
            return redirect('/autogestion/donaciones')

        if comprobante is None or not comprobante.filename:
            flash('Para donaciones en dinero debes adjuntar el comprobante', 'error')
            return redirect('/autogestion/donaciones')

        comprobante_archivo, error_archivo = guardar_comprobante(comprobante)
        if error_archivo:
            flash(error_archivo, 'error')
            return redirect('/autogestion/donaciones')
        detalle_guardado = ''
    else:
        monto = 0
        detalle_valido, error_detalle, detalle = validar_texto_claro(
            detalle,
            'un detalle claro de la donación',
            min_len=6,
            max_len=300,
            permitir_numeros=True
        )
        if not detalle_valido:
            flash(error_detalle, 'error')
            return redirect('/autogestion/donaciones')
        detalle_guardado = detalle
        comprobante_archivo = ''

    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO donaciones (nombre_donante, tipo_donante, tipo_donacion, monto, detalle, comprobante_archivo, fecha)
        VALUES (?, ?, ?, ?, ?, ?, DATE('now'))
        """,
        (nombre, tipo_donante, tipo_donacion, monto, detalle_guardado, comprobante_archivo)
    )
    conn.commit()
    conn.close()
    return redirect('/autogestion/donaciones')

@app.route('/autogestion/donaciones')
@login_required
def autogestion_donaciones():
    try:
        pagina = max(1, int(request.args.get('pagina', 1)))
    except (ValueError, TypeError):
        pagina = 1

    por_pagina = 6
    busqueda = request.args.get('q', '').strip()
    filtro_tipo_donante = request.args.get('tipo_donante', '').strip().lower()
    filtro_tipo_donacion = request.args.get('tipo_donacion', '').strip().lower()

    # Compatibilidad con enlaces antiguos que usaban "empresa"
    if filtro_tipo_donante == 'empresa':
        filtro_tipo_donante = 'entidad'

    tipos_donante_validos = {'persona', 'entidad'}
    tipos_donacion_validos = {'dinero', 'alimentos', 'articulos'}
    if filtro_tipo_donante not in tipos_donante_validos:
        filtro_tipo_donante = ''
    if filtro_tipo_donacion not in tipos_donacion_validos:
        filtro_tipo_donacion = ''

    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT id, nombre_donante, tipo_donante, tipo_donacion, monto, detalle, comprobante_archivo, fecha
        FROM donaciones
        ORDER BY id DESC
        """
    )
    donaciones = cursor.fetchall()

    busqueda_normalizada = normalizar_texto_base(busqueda)
    if busqueda_normalizada:
        donaciones = [
            donacion for donacion in donaciones
            if busqueda_normalizada in normalizar_texto_base(donacion[1])
        ]

    if filtro_tipo_donante:
        donaciones = [
            donacion for donacion in donaciones
            if (
                donacion[2] == filtro_tipo_donante
                or (filtro_tipo_donante == 'entidad' and donacion[2] == 'empresa')
            )
        ]

    if filtro_tipo_donacion:
        donaciones = [
            donacion for donacion in donaciones
            if donacion[3] == filtro_tipo_donacion
        ]

    total_resultados = len(donaciones)
    total_paginas = max(1, -(-total_resultados // por_pagina))
    pagina = min(pagina, total_paginas)
    offset = (pagina - 1) * por_pagina

    inicio_resultado = offset + 1 if total_resultados > 0 else 0
    fin_resultado = min(offset + por_pagina, total_resultados)
    donaciones = donaciones[offset:offset + por_pagina]

    filtros_base = {}
    if busqueda:
        filtros_base['q'] = busqueda
    if filtro_tipo_donante:
        filtros_base['tipo_donante'] = filtro_tipo_donante
    if filtro_tipo_donacion:
        filtros_base['tipo_donacion'] = filtro_tipo_donacion

    def build_donaciones_page_url(num):
        params = {**filtros_base, 'pagina': num}
        return '/autogestion/donaciones?' + urlencode(params)

    rango_inicio = max(1, pagina - 3)
    rango_fin = min(total_paginas, rango_inicio + 5)
    if rango_fin - rango_inicio < 5:
        rango_inicio = max(1, rango_fin - 5)
    rango_paginas = range(rango_inicio, rango_fin + 1)

    cursor.execute(
        """
        SELECT
            COUNT(*),
            COALESCE(SUM(CASE WHEN tipo_donacion = 'dinero' THEN monto ELSE 0 END), 0),
            COALESCE(SUM(CASE WHEN tipo_donacion = 'alimentos' THEN 1 ELSE 0 END), 0),
            COALESCE(SUM(CASE WHEN tipo_donacion = 'articulos' THEN 1 ELSE 0 END), 0)
        FROM donaciones
        """
    )
    resumen = cursor.fetchone()
    total_donaciones = resumen[0]
    monto_total = resumen[1]
    total_alimentos = resumen[2]
    total_articulos = resumen[3]

    conn.close()

    return render_template(
        'autogestion_donaciones.html',
        donaciones=donaciones,
        total_donaciones=total_donaciones,
        monto_total=monto_total,
        total_alimentos=total_alimentos,
        total_articulos=total_articulos,
        busqueda=busqueda,
        filtro_tipo_donante=filtro_tipo_donante,
        filtro_tipo_donacion=filtro_tipo_donacion,
        total_resultados=total_resultados,
        paginacion={
            'pagina_actual': pagina,
            'total_paginas': total_paginas,
            'por_pagina': por_pagina,
            'tiene_anterior': pagina > 1,
            'tiene_siguiente': pagina < total_paginas,
            'url_anterior': build_donaciones_page_url(pagina - 1) if pagina > 1 else '',
            'url_siguiente': build_donaciones_page_url(pagina + 1) if pagina < total_paginas else '',
            'inicio_resultado': inicio_resultado,
            'fin_resultado': fin_resultado,
            'urls_paginas': [
                {'numero': n, 'url': build_donaciones_page_url(n), 'actual': n == pagina}
                for n in rango_paginas
            ],
            'muestra_primera': rango_inicio > 1,
            'muestra_ultima': rango_fin < total_paginas,
            'url_primera': build_donaciones_page_url(1),
            'url_ultima': build_donaciones_page_url(total_paginas),
        }
    )


@app.route('/autogestion/donaciones/comprobante/<int:donacion_id>')
@login_required
def ver_comprobante_donacion(donacion_id):
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    cursor.execute("SELECT comprobante_archivo FROM donaciones WHERE id = ?", (donacion_id,))
    fila = cursor.fetchone()
    conn.close()

    if not fila or not fila[0]:
        return render_template('no_encontrado.html'), 404

    nombre_archivo = os.path.basename(fila[0])
    ruta_archivo = os.path.join(UPLOADS_DIR, nombre_archivo)
    if not os.path.exists(ruta_archivo):
        return render_template('no_encontrado.html'), 404

    return send_file(ruta_archivo, as_attachment=False)


@app.route('/autogestion/voluntarios')
@login_required
def autogestion_voluntarios():
    try:
        pagina = max(1, int(request.args.get('pagina', 1)))
    except (ValueError, TypeError):
        pagina = 1

    por_pagina = 6
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()

    busqueda = request.args.get('q', '').strip()
    busqueda_normalizada = normalizar_texto_base(busqueda)

    cursor.execute(
        """
        SELECT id, nombre, email, telefono, disponibilidad, mensaje, fecha
        FROM voluntarios
        ORDER BY id DESC
        """
    )
    voluntarios = cursor.fetchall()

    if busqueda_normalizada:
        voluntarios = [
            voluntario for voluntario in voluntarios
            if busqueda_normalizada in normalizar_texto_base(voluntario[1])
        ]

    total_resultados = len(voluntarios)
    total_paginas = max(1, -(-total_resultados // por_pagina))
    pagina = min(pagina, total_paginas)
    offset = (pagina - 1) * por_pagina

    inicio_resultado = offset + 1 if total_resultados > 0 else 0
    fin_resultado = min(offset + por_pagina, total_resultados)
    voluntarios = voluntarios[offset:offset + por_pagina]

    filtros_base = {}
    if busqueda:
        filtros_base['q'] = busqueda

    def build_voluntarios_page_url(num):
        params = {**filtros_base, 'pagina': num}
        return '/autogestion/voluntarios?' + urlencode(params)

    rango_inicio = max(1, pagina - 3)
    rango_fin = min(total_paginas, rango_inicio + 5)
    if rango_fin - rango_inicio < 5:
        rango_inicio = max(1, rango_fin - 5)
    rango_paginas = range(rango_inicio, rango_fin + 1)

    cursor.execute("SELECT COUNT(*) FROM voluntarios")
    total_postulaciones = cursor.fetchone()[0]

    conn.close()

    return render_template(
        'autogestion_voluntarios.html',
        voluntarios=voluntarios,
        total_postulaciones=total_postulaciones,
        busqueda=busqueda,
        total_resultados=total_resultados,
        paginacion={
            'pagina_actual': pagina,
            'total_paginas': total_paginas,
            'por_pagina': por_pagina,
            'tiene_anterior': pagina > 1,
            'tiene_siguiente': pagina < total_paginas,
            'url_anterior': build_voluntarios_page_url(pagina - 1) if pagina > 1 else '',
            'url_siguiente': build_voluntarios_page_url(pagina + 1) if pagina < total_paginas else '',
            'inicio_resultado': inicio_resultado,
            'fin_resultado': fin_resultado,
            'urls_paginas': [
                {'numero': n, 'url': build_voluntarios_page_url(n), 'actual': n == pagina}
                for n in rango_paginas
            ],
            'muestra_primera': rango_inicio > 1,
            'muestra_ultima': rango_fin < total_paginas,
            'url_primera': build_voluntarios_page_url(1),
            'url_ultima': build_voluntarios_page_url(total_paginas),
        }
    )

# DASHBOARD
@app.route('/autogestion/dashboard')
@login_required
def dashboard():
    busqueda = request.args.get('q', '').strip()
    estado = request.args.get('estado', '').strip().lower()
    sin_pagina_str = request.args.get('sin_pagina', '1').strip()
    bajo_pagina_str = request.args.get('bajo_pagina', '1').strip()

    try:
        sin_pagina = int(sin_pagina_str)
        if sin_pagina < 1:
            raise ValueError
    except ValueError:
        sin_pagina = 1

    try:
        bajo_pagina = int(bajo_pagina_str)
        if bajo_pagina < 1:
            raise ValueError
    except ValueError:
        bajo_pagina = 1

    if estado not in ('', 'sin-stock', 'bajo-stock'):
        estado = ''

    por_pagina = 5

    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    cursor.execute("SELECT SUM(cantidad) FROM stock")
    total_stock = cursor.fetchone()[0] or 0

    cursor.execute("SELECT COALESCE(SUM(CASE WHEN tipo_donacion = 'dinero' THEN monto ELSE 0 END), 0) FROM donaciones")
    total_donaciones = cursor.fetchone()[0] or 0

    condiciones_base = []
    parametros_base = []
    if busqueda:
        condiciones_base.append("LOWER(producto) LIKE ?")
        parametros_base.append(f"%{busqueda.lower()}%")

    def construir_where(condicion_extra):
        condiciones = list(condiciones_base)
        condiciones.append(condicion_extra)
        return " WHERE " + " AND ".join(condiciones), list(parametros_base)

    # Productos sin stock
    if estado in ('', 'sin-stock'):
        where_sin_stock, parametros_sin_stock = construir_where("cantidad = 0")
        cursor.execute("SELECT COUNT(*) FROM stock" + where_sin_stock, parametros_sin_stock)
        total_sin_stock = cursor.fetchone()[0]
        total_paginas_sin_stock = max(1, (total_sin_stock + por_pagina - 1) // por_pagina)
        if sin_pagina > total_paginas_sin_stock:
            sin_pagina = total_paginas_sin_stock
        offset_sin_stock = (sin_pagina - 1) * por_pagina
        cursor.execute(
            "SELECT id, producto, cantidad FROM stock" + where_sin_stock + " ORDER BY producto LIMIT ? OFFSET ?",
            parametros_sin_stock + [por_pagina, offset_sin_stock]
        )
        sin_stock = cursor.fetchall()
    else:
        total_sin_stock = 0
        total_paginas_sin_stock = 1
        sin_stock = []

    # Productos con bajo stock (menos de 5)
    if estado in ('', 'bajo-stock'):
        where_bajo_stock, parametros_bajo_stock = construir_where("cantidad > 0 AND cantidad <= 5")
        cursor.execute("SELECT COUNT(*) FROM stock" + where_bajo_stock, parametros_bajo_stock)
        total_bajo_stock = cursor.fetchone()[0]
        total_paginas_bajo_stock = max(1, (total_bajo_stock + por_pagina - 1) // por_pagina)
        if bajo_pagina > total_paginas_bajo_stock:
            bajo_pagina = total_paginas_bajo_stock
        offset_bajo_stock = (bajo_pagina - 1) * por_pagina
        cursor.execute(
            "SELECT id, producto, cantidad FROM stock" + where_bajo_stock + " ORDER BY cantidad, producto LIMIT ? OFFSET ?",
            parametros_bajo_stock + [por_pagina, offset_bajo_stock]
        )
        bajo_stock = cursor.fetchall()
    else:
        total_bajo_stock = 0
        total_paginas_bajo_stock = 1
        bajo_stock = []

    # Todos los productos para gráfico
    where_grafico, parametros_grafico = construir_where("cantidad > 0 AND cantidad <= 5")
    cursor.execute(
        "SELECT producto, cantidad FROM stock" + where_grafico + " ORDER BY cantidad DESC LIMIT 20",
        parametros_grafico
    )
    productos_grafico = cursor.fetchall()

    conn.close()

    query_base = {}
    if busqueda:
        query_base['q'] = busqueda
    if estado:
        query_base['estado'] = estado

    def build_dashboard_url(sin_pagina_num=None, bajo_pagina_num=None):
        params = dict(query_base)
        params['sin_pagina'] = sin_pagina if sin_pagina_num is None else sin_pagina_num
        params['bajo_pagina'] = bajo_pagina if bajo_pagina_num is None else bajo_pagina_num
        return '/autogestion/dashboard?' + urlencode(params)

    def build_paginacion(pagina_actual, total_paginas, tipo):
        rango_inicio = max(1, pagina_actual - 2)
        rango_fin = min(total_paginas, pagina_actual + 2)

        def url_pagina(numero):
            if tipo == 'sin_stock':
                return build_dashboard_url(sin_pagina_num=numero)
            return build_dashboard_url(bajo_pagina_num=numero)

        return {
            'pagina_actual': pagina_actual,
            'total_paginas': total_paginas,
            'tiene_anterior': pagina_actual > 1,
            'tiene_siguiente': pagina_actual < total_paginas,
            'url_anterior': url_pagina(pagina_actual - 1) if pagina_actual > 1 else '',
            'url_siguiente': url_pagina(pagina_actual + 1) if pagina_actual < total_paginas else '',
            'urls_paginas': [
                {
                    'numero': numero,
                    'url': url_pagina(numero),
                    'actual': numero == pagina_actual
                }
                for numero in range(rango_inicio, rango_fin + 1)
            ],
            'muestra_primera': rango_inicio > 1,
            'muestra_ultima': rango_fin < total_paginas,
            'url_primera': url_pagina(1),
            'url_ultima': url_pagina(total_paginas)
        }

    return render_template(
        'dashboard.html',
        total_stock=total_stock,
        total_donaciones=total_donaciones,
        sin_stock=sin_stock,
        bajo_stock=bajo_stock,
        productos_grafico=productos_grafico,
        filtros={
            'q': busqueda,
            'estado': estado
        },
        sin_stock_total=total_sin_stock,
        bajo_stock_total=total_bajo_stock,
        sin_paginacion=build_paginacion(sin_pagina, total_paginas_sin_stock, 'sin_stock'),
        bajo_paginacion=build_paginacion(bajo_pagina, total_paginas_bajo_stock, 'bajo_stock')
    )

@app.route('/autogestion/dashboard/export_excel')
@login_required
def exportar_excel_dashboard():
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    cursor.execute("SELECT id, producto, cantidad FROM stock WHERE cantidad = 0 ORDER BY producto")
    sin_stock = cursor.fetchall()
    cursor.execute("SELECT id, producto, cantidad FROM stock WHERE cantidad > 0 AND cantidad <= 5 ORDER BY cantidad")
    bajo_stock = cursor.fetchall()
    conn.close()

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'Bajo Stock'
    ws.append(['Producto', 'Cantidad'])
    for item in bajo_stock:
        ws.append([item[1], item[2]])

    if bajo_stock:
        chart = BarChart()
        data = Reference(ws, min_col=2, min_row=1, max_row=len(bajo_stock) + 1)
        cats = Reference(ws, min_col=1, min_row=2, max_row=len(bajo_stock) + 1)
        chart.add_data(data, titles_from_data=True)
        chart.set_categories(cats)
        chart.title = 'Productos con Bajo Stock'
        chart.y_axis.title = 'Cantidad'
        chart.x_axis.title = 'Producto'
        chart.dLbls = DataLabelList()
        chart.dLbls.showVal = True
        ws.add_chart(chart, 'D2')

    ws2 = wb.create_sheet(title='Sin Stock')
    ws2.append(['Producto', 'Cantidad'])
    for item in sin_stock:
        ws2.append([item[1], item[2]])

    output = BytesIO()
    wb.save(output)
    output.seek(0)

    return send_file(
        output,
        as_attachment=True,
        download_name='dashboard_stock.xlsx',
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )


@app.route('/autogestion/asi-acompanamos', methods=['GET', 'POST'])
@login_required
def autogestion_asi_acompanamos():
    if not session.get('es_admin'):
        return render_template('acceso_denegado.html')

    if request.method == 'POST':
        titulo_raw = request.form.get('titulo', '')
        resumen_raw = request.form.get('resumen', '')
        fecha_jornada = request.form.get('fecha_jornada', '').strip()
        foto_jornada = request.files.get('foto_jornada')

        titulo_valido, error_titulo, titulo = validar_texto_claro(
            titulo_raw,
            'un título claro',
            min_len=4,
            max_len=120,
            permitir_numeros=True
        )
        if not titulo_valido:
            flash(error_titulo, 'error')
            return redirect('/autogestion/asi-acompanamos')

        resumen_valido, error_resumen, resumen = validar_texto_claro(
            resumen_raw,
            'un resumen claro de la jornada',
            min_len=12,
            max_len=600,
            permitir_numeros=True
        )
        if not resumen_valido:
            flash(error_resumen, 'error')
            return redirect('/autogestion/asi-acompanamos')

        try:
            datetime.strptime(fecha_jornada, '%Y-%m-%d')
        except ValueError:
            flash('Ingresá una fecha válida para la jornada', 'error')
            return redirect('/autogestion/asi-acompanamos')

        foto_archivo, error_foto = guardar_foto_acompanamos(foto_jornada)
        if error_foto:
            flash(error_foto, 'error')
            return redirect('/autogestion/asi-acompanamos')

        conn = sqlite3.connect('database.db')
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO acompanamos_jornadas (titulo, resumen, fecha_jornada, foto_archivo, creado_por, creado_en)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                titulo,
                resumen,
                fecha_jornada,
                foto_archivo,
                session.get('usuario', ''),
                datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            )
        )
        conn.commit()
        conn.close()

        flash('Jornada publicada correctamente', 'success')
        return redirect('/autogestion/asi-acompanamos')

    fecha_desde = request.args.get('fecha_desde', '').strip()
    fecha_hasta = request.args.get('fecha_hasta', '').strip()
    pagina_str = request.args.get('pagina', '1').strip()

    try:
        pagina = int(pagina_str)
        if pagina < 1:
            raise ValueError
    except ValueError:
        pagina = 1

    por_pagina = 6

    condiciones = []
    parametros = []

    if fecha_desde:
        try:
            datetime.strptime(fecha_desde, '%Y-%m-%d')
            condiciones.append('fecha_jornada >= ?')
            parametros.append(fecha_desde)
        except ValueError:
            fecha_desde = ''

    if fecha_hasta:
        try:
            datetime.strptime(fecha_hasta, '%Y-%m-%d')
            condiciones.append('fecha_jornada <= ?')
            parametros.append(fecha_hasta)
        except ValueError:
            fecha_hasta = ''

    where_clause = ''
    if condiciones:
        where_clause = ' WHERE ' + ' AND '.join(condiciones)

    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    cursor.execute(
        'SELECT COUNT(*) FROM acompanamos_jornadas' + where_clause,
        parametros
    )
    total_resultados = cursor.fetchone()[0]

    total_paginas = max(1, (total_resultados + por_pagina - 1) // por_pagina)
    if pagina > total_paginas:
        pagina = total_paginas

    offset = (pagina - 1) * por_pagina

    parametros_paginados = list(parametros)
    parametros_paginados.extend([por_pagina, offset])

    cursor.execute(
        """
        SELECT id, titulo, resumen, fecha_jornada, foto_archivo, creado_por, creado_en
        FROM acompanamos_jornadas
        """ + where_clause + """
        ORDER BY fecha_jornada DESC, id DESC
        LIMIT ? OFFSET ?
        """,
        parametros_paginados
    )
    filas = cursor.fetchall()
    conn.close()

    jornadas = []
    for fila in filas:
        fecha_jornada = fila[3]
        try:
            fecha_jornada = datetime.strptime(fila[3], '%Y-%m-%d').strftime('%d/%m/%Y')
        except (TypeError, ValueError):
            pass
        jornadas.append({
            'id': fila[0],
            'titulo': fila[1],
            'resumen': fila[2],
            'fecha_jornada': fecha_jornada,
            'foto_url': url_for('ver_foto_asi_acompanamos', jornada_id=fila[0]) if fila[4] else '',
            'creado_por': fila[5] or '-',
            'creado_en': fila[6],
        })

    query_base = {}
    if fecha_desde:
        query_base['fecha_desde'] = fecha_desde
    if fecha_hasta:
        query_base['fecha_hasta'] = fecha_hasta

    def build_page_url(numero_pagina):
        params = dict(query_base)
        params['pagina'] = numero_pagina
        return '/autogestion/asi-acompanamos?' + urlencode(params)

    inicio_resultado = offset + 1 if total_resultados > 0 else 0
    fin_resultado = min(offset + len(jornadas), total_resultados)

    max_paginas_visibles = 6
    if total_paginas > max_paginas_visibles:
        margen = max_paginas_visibles // 2
        pagina_inicio = max(1, pagina - margen)
        pagina_fin = min(total_paginas, pagina_inicio + max_paginas_visibles - 1)
        if pagina_fin - pagina_inicio + 1 < max_paginas_visibles:
            pagina_inicio = max(1, pagina_fin - max_paginas_visibles + 1)
        rango_paginas = range(pagina_inicio, pagina_fin + 1)
        muestra_primera = pagina_inicio > 1
        muestra_ultima = pagina_fin < total_paginas
    else:
        rango_paginas = range(1, total_paginas + 1)
        muestra_primera = False
        muestra_ultima = False

    return render_template(
        'autogestion_asi_acompanamos.html',
        jornadas=jornadas,
        filtros={
            'fecha_desde': fecha_desde,
            'fecha_hasta': fecha_hasta
        },
        total_resultados=total_resultados,
        paginacion={
            'pagina_actual': pagina,
            'total_paginas': total_paginas,
            'tiene_anterior': pagina > 1,
            'tiene_siguiente': pagina < total_paginas,
            'url_anterior': build_page_url(pagina - 1) if pagina > 1 else '',
            'url_siguiente': build_page_url(pagina + 1) if pagina < total_paginas else '',
            'inicio_resultado': inicio_resultado,
            'fin_resultado': fin_resultado,
            'urls_paginas': [
                {
                    'numero': numero,
                    'url': build_page_url(numero),
                    'actual': numero == pagina
                }
                for numero in rango_paginas
            ],
            'muestra_primera': muestra_primera,
            'muestra_ultima': muestra_ultima,
            'url_primera': build_page_url(1),
            'url_ultima': build_page_url(total_paginas)
        }
    )


@app.route('/autogestion/asi-acompanamos/editar/<int:jornada_id>', methods=['GET', 'POST'])
@login_required
def editar_asi_acompanamos(jornada_id):
    if not session.get('es_admin'):
        return render_template('acceso_denegado.html')

    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT id, titulo, resumen, fecha_jornada, foto_archivo
        FROM acompanamos_jornadas
        WHERE id = ?
        """,
        (jornada_id,)
    )
    fila = cursor.fetchone()

    if not fila:
        conn.close()
        flash('La jornada no existe o ya fue eliminada', 'error')
        return redirect('/autogestion/asi-acompanamos')

    if request.method == 'POST':
        titulo_raw = request.form.get('titulo', '')
        resumen_raw = request.form.get('resumen', '')
        fecha_jornada = request.form.get('fecha_jornada', '').strip()
        foto_jornada = request.files.get('foto_jornada')
        quitar_foto = request.form.get('quitar_foto') == '1'

        titulo_valido, error_titulo, titulo = validar_texto_claro(
            titulo_raw,
            'un título claro',
            min_len=4,
            max_len=120,
            permitir_numeros=True
        )
        if not titulo_valido:
            conn.close()
            flash(error_titulo, 'error')
            return redirect(f'/autogestion/asi-acompanamos/editar/{jornada_id}')

        resumen_valido, error_resumen, resumen = validar_texto_claro(
            resumen_raw,
            'un resumen claro de la jornada',
            min_len=12,
            max_len=600,
            permitir_numeros=True
        )
        if not resumen_valido:
            conn.close()
            flash(error_resumen, 'error')
            return redirect(f'/autogestion/asi-acompanamos/editar/{jornada_id}')

        try:
            datetime.strptime(fecha_jornada, '%Y-%m-%d')
        except ValueError:
            conn.close()
            flash('Ingresá una fecha válida para la jornada', 'error')
            return redirect(f'/autogestion/asi-acompanamos/editar/{jornada_id}')

        foto_actual = fila[4] or ''
        foto_nueva, error_foto = guardar_foto_acompanamos(foto_jornada)
        if error_foto:
            conn.close()
            flash(error_foto, 'error')
            return redirect(f'/autogestion/asi-acompanamos/editar/{jornada_id}')

        foto_guardar = foto_actual
        foto_anterior_para_borrar = ''

        if foto_nueva:
            foto_guardar = foto_nueva
            foto_anterior_para_borrar = foto_actual
        elif quitar_foto:
            foto_guardar = ''
            foto_anterior_para_borrar = foto_actual

        cursor.execute(
            """
            UPDATE acompanamos_jornadas
            SET titulo = ?, resumen = ?, fecha_jornada = ?, foto_archivo = ?
            WHERE id = ?
            """,
            (titulo, resumen, fecha_jornada, foto_guardar, jornada_id)
        )
        conn.commit()
        conn.close()

        if foto_anterior_para_borrar:
            ruta_foto_vieja = os.path.join(ACOMPANAMOS_UPLOADS_DIR, os.path.basename(foto_anterior_para_borrar))
            if os.path.exists(ruta_foto_vieja):
                try:
                    os.remove(ruta_foto_vieja)
                except OSError:
                    pass

        flash('Jornada actualizada correctamente', 'success')
        return redirect('/autogestion/asi-acompanamos')

    jornada = {
        'id': fila[0],
        'titulo': fila[1],
        'resumen': fila[2],
        'fecha_jornada': fila[3],
        'foto_url': url_for('ver_foto_asi_acompanamos', jornada_id=fila[0]) if fila[4] else '',
    }
    conn.close()

    return render_template('editar_asi_acompanamos.html', jornada=jornada)


@app.route('/autogestion/asi-acompanamos/eliminar/<int:jornada_id>')
@login_required
def eliminar_asi_acompanamos(jornada_id):
    if not session.get('es_admin'):
        return render_template('acceso_denegado.html')

    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    cursor.execute("SELECT id, foto_archivo FROM acompanamos_jornadas WHERE id = ?", (jornada_id,))
    fila = cursor.fetchone()

    if not fila:
        conn.close()
        flash('La jornada no existe o ya fue eliminada', 'error')
        return redirect('/autogestion/asi-acompanamos')

    cursor.execute("DELETE FROM acompanamos_jornadas WHERE id = ?", (jornada_id,))
    conn.commit()
    conn.close()

    foto_archivo = fila[1] or ''
    if foto_archivo:
        ruta_foto = os.path.join(ACOMPANAMOS_UPLOADS_DIR, os.path.basename(foto_archivo))
        if os.path.exists(ruta_foto):
            try:
                os.remove(ruta_foto)
            except OSError:
                pass

    flash('Jornada eliminada', 'success')
    return redirect('/autogestion/asi-acompanamos')

# USUARIOS
@app.route('/autogestion/usuarios')
@login_required
def usuarios():
    if not session.get('es_admin'):
        return render_template('acceso_denegado.html')

    try:
        pagina = max(1, int(request.args.get('pagina', 1)))
    except (ValueError, TypeError):
        pagina = 1

    por_pagina = 6
    busqueda = request.args.get('q', '').strip()
    busqueda_normalizada = normalizar_texto_base(busqueda)

    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()

    cursor.execute(
        "SELECT id, usuario, email, acceso_privado, es_admin FROM usuarios ORDER BY id ASC"
    )
    usuarios = cursor.fetchall()

    if busqueda_normalizada:
        usuarios = [
            usuario for usuario in usuarios
            if busqueda_normalizada in normalizar_texto_base(usuario[1])
        ]

    total_resultados = len(usuarios)
    total_paginas = max(1, -(-total_resultados // por_pagina))
    pagina = min(pagina, total_paginas)
    offset = (pagina - 1) * por_pagina

    inicio_resultado = offset + 1 if total_resultados > 0 else 0
    fin_resultado = min(offset + por_pagina, total_resultados)
    usuarios = usuarios[offset:offset + por_pagina]

    filtros_base = {}
    if busqueda:
        filtros_base['q'] = busqueda

    def build_usuarios_page_url(num):
        params = {**filtros_base, 'pagina': num}
        return '/autogestion/usuarios?' + urlencode(params)

    rango_inicio = max(1, pagina - 3)
    rango_fin = min(total_paginas, rango_inicio + 5)
    if rango_fin - rango_inicio < 5:
        rango_inicio = max(1, rango_fin - 5)
    rango_paginas = range(rango_inicio, rango_fin + 1)

    conn.close()
    return render_template(
        'usuarios.html',
        usuarios=usuarios,
        busqueda=busqueda,
        total_resultados=total_resultados,
        paginacion={
            'pagina_actual': pagina,
            'total_paginas': total_paginas,
            'por_pagina': por_pagina,
            'tiene_anterior': pagina > 1,
            'tiene_siguiente': pagina < total_paginas,
            'url_anterior': build_usuarios_page_url(pagina - 1) if pagina > 1 else '',
            'url_siguiente': build_usuarios_page_url(pagina + 1) if pagina < total_paginas else '',
            'inicio_resultado': inicio_resultado,
            'fin_resultado': fin_resultado,
            'urls_paginas': [
                {'numero': n, 'url': build_usuarios_page_url(n), 'actual': n == pagina}
                for n in rango_paginas
            ],
            'muestra_primera': rango_inicio > 1,
            'muestra_ultima': rango_fin < total_paginas,
            'url_primera': build_usuarios_page_url(1),
            'url_ultima': build_usuarios_page_url(total_paginas),
        }
    )

@app.route('/autogestion/usuarios/agregar', methods=['GET', 'POST'])
@login_required
def agregar_usuario():
    if not session.get('es_admin'):
        return render_template('acceso_denegado.html')
    if request.method == 'POST':
        usuario_form = request.form.get('usuario', '')
        email = request.form.get('email', '').strip().lower()
        password = request.form['password']
        acceso_privado = 1 if request.form.get('acceso_privado') == 'on' else 0
        es_admin = 1 if request.form.get('es_admin') == 'on' else 0

        usuario_valido, mensaje_usuario, usuario = validar_usuario(usuario_form, es_admin=bool(es_admin))
        if not usuario_valido:
            return render_template('agregar_usuario.html', error=mensaje_usuario)
        
        # Validar contraseña
        valida, mensaje = validar_contrasena(password)
        if not valida:
            return render_template('agregar_usuario.html', error=mensaje)
        
        conn = sqlite3.connect('database.db')
        cursor = conn.cursor()
        try:
            cursor.execute(
                "INSERT INTO usuarios (usuario, email, password, acceso_privado, es_admin) VALUES (?, ?, ?, ?, ?)",
                (usuario, email, generar_hash_password(password), acceso_privado, es_admin)
            )
        except sqlite3.IntegrityError:
            conn.close()
            return render_template('agregar_usuario.html', error='Usuario o email ya existe')
        conn.commit()
        conn.close()
        return redirect('/autogestion/usuarios')
    return render_template('agregar_usuario.html', error=None)

@app.route('/autogestion/usuarios/editar/<int:id>', methods=['GET', 'POST'])
@login_required
def editar_usuario(id):
    if not session.get('es_admin'):
        return render_template('acceso_denegado.html')
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, usuario, email, password, acceso_privado, es_admin FROM usuarios WHERE id = ?",
        (id,)
    )
    usuario = cursor.fetchone()
    
    if not usuario:
        conn.close()
        return render_template('no_encontrado.html')
    
    if request.method == 'POST':
        nuevo_usuario_form = request.form.get('usuario', '')
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        acceso_privado = 1 if request.form.get('acceso_privado') == 'on' else 0
        es_admin = 1 if request.form.get('es_admin') == 'on' else 0

        permite_reservado = nuevo_usuario_form.strip().lower() == str(usuario[1]).strip().lower()
        usuario_valido, mensaje_usuario, nuevo_usuario = validar_usuario(
            nuevo_usuario_form,
            es_admin=bool(es_admin),
            permitir_reservado=permite_reservado
        )
        if not usuario_valido:
            conn.close()
            return render_template('editar_usuario.html', usuario=usuario, error=mensaje_usuario)
        
        # Si proporciona contraseña, validarla
        if password != "":
            valida, mensaje = validar_contrasena(password)
            if not valida:
                conn.close()
                return render_template('editar_usuario.html', usuario=usuario, error=mensaje)
            password = generar_hash_password(password)
        else:
            # Si no proporciona contraseña, mantener la actual
            password = usuario[3]

        cursor.execute("SELECT COUNT(*) FROM usuarios WHERE es_admin = 1")
        total_admins = cursor.fetchone()[0]
        if int(usuario[5] or 0) == 1 and es_admin == 0 and total_admins <= 1:
            conn.close()
            return render_template('editar_usuario.html', usuario=usuario, error='No se puede quitar el rol al último administrador')

        try:
            cursor.execute(
                "UPDATE usuarios SET usuario=?, email=?, password=?, acceso_privado=?, es_admin=? WHERE id=?",
                (nuevo_usuario, email, password, acceso_privado, es_admin, id)
            )
        except sqlite3.IntegrityError:
            conn.close()
            return render_template('editar_usuario.html', usuario=usuario, error='Usuario o email ya existe')
        conn.commit()
        conn.close()
        return redirect('/autogestion/usuarios')
    
    conn.close()
    return render_template('editar_usuario.html', usuario=usuario, error=None)

@app.route('/autogestion/usuarios/eliminar/<int:id>')
@login_required
def eliminar_usuario(id):
    if not session.get('es_admin'):
        return render_template('acceso_denegado.html')

    usuario_actual_id = session.get('usuario_id')
    if usuario_actual_id == id:
        flash('No puedes eliminar tu propia cuenta desde esta sesión', 'error')
        return redirect('/autogestion/usuarios')

    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()

    cursor.execute("SELECT es_admin FROM usuarios WHERE id = ?", (id,))
    fila = cursor.fetchone()
    if not fila:
        conn.close()
        flash('El usuario no existe', 'error')
        return redirect('/autogestion/usuarios')

    if int(fila[0] or 0) == 1:
        cursor.execute("SELECT COUNT(*) FROM usuarios WHERE es_admin = 1")
        total_admins = cursor.fetchone()[0]
        if total_admins <= 1:
            conn.close()
            flash('No se puede eliminar el último administrador', 'error')
            return redirect('/autogestion/usuarios')

    cursor.execute("DELETE FROM usuarios WHERE id = ?", (id,))
    conn.commit()
    conn.close()
    return redirect('/autogestion/usuarios')


@app.route('/autogestion/auditoria-login')
@login_required
def auditoria_login():
    if not session.get('es_admin'):
        return render_template('acceso_denegado.html')

    filtro_usuario = request.args.get('usuario', '').strip()
    filtro_resultado = request.args.get('resultado', '').strip().lower()
    filtro_ip = request.args.get('ip', '').strip()
    filtro_desde = request.args.get('desde', '').strip()
    filtro_hasta = request.args.get('hasta', '').strip()

    try:
        pagina = max(1, int(request.args.get('pagina', 1)))
    except (ValueError, TypeError):
        pagina = 1

    resultados_validos = {'exitoso', 'fallido', 'bloqueado', 'denegado'}
    if filtro_resultado and filtro_resultado not in resultados_validos:
        filtro_resultado = ''

    condiciones = []
    parametros = []

    if filtro_usuario:
        condiciones.append("LOWER(usuario_ingresado) LIKE ?")
        parametros.append(f"%{filtro_usuario.lower()}%")

    if filtro_resultado:
        condiciones.append("resultado = ?")
        parametros.append(filtro_resultado)

    if filtro_ip:
        condiciones.append("ip_origen LIKE ?")
        parametros.append(f"%{filtro_ip}%")

    try:
        if filtro_desde:
            datetime.strptime(filtro_desde, '%Y-%m-%d')
            condiciones.append("DATE(fecha) >= DATE(?)")
            parametros.append(filtro_desde)
    except ValueError:
        filtro_desde = ''

    try:
        if filtro_hasta:
            datetime.strptime(filtro_hasta, '%Y-%m-%d')
            condiciones.append("DATE(fecha) <= DATE(?)")
            parametros.append(filtro_hasta)
    except ValueError:
        filtro_hasta = ''

    where_clause = (" WHERE " + " AND ".join(condiciones)) if condiciones else ""

    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()

    cursor.execute(f"SELECT COUNT(*) FROM login_auditoria{where_clause}", parametros)
    total_eventos = cursor.fetchone()[0]

    por_pagina = 8
    total_paginas = max(1, -(-total_eventos // por_pagina))
    pagina = min(pagina, total_paginas)
    offset = (pagina - 1) * por_pagina
    inicio_resultado = offset + 1 if total_eventos > 0 else 0
    fin_resultado = min(offset + por_pagina, total_eventos)

    consulta = f"""
        SELECT id, usuario_ingresado, ip_origen, resultado, detalle, fecha
        FROM login_auditoria{where_clause}
        ORDER BY id DESC LIMIT ? OFFSET ?
    """
    cursor.execute(consulta, parametros + [por_pagina, offset])
    eventos = cursor.fetchall()
    conn.close()

    filtros_base = {k: v for k, v in {
        'usuario': filtro_usuario,
        'resultado': filtro_resultado,
        'ip': filtro_ip,
        'desde': filtro_desde,
        'hasta': filtro_hasta,
    }.items() if v}

    def build_audit_page_url(num):
        params = {**filtros_base, 'pagina': num}
        return '/autogestion/auditoria-login?' + urlencode(params)

    rango_inicio = max(1, pagina - 3)
    rango_fin = min(total_paginas, rango_inicio + 5)
    if rango_fin - rango_inicio < 5:
        rango_inicio = max(1, rango_fin - 5)
    rango_paginas = range(rango_inicio, rango_fin + 1)

    paginacion = {
        'pagina_actual': pagina,
        'total_paginas': total_paginas,
        'tiene_anterior': pagina > 1,
        'tiene_siguiente': pagina < total_paginas,
        'url_anterior': build_audit_page_url(pagina - 1) if pagina > 1 else '',
        'url_siguiente': build_audit_page_url(pagina + 1) if pagina < total_paginas else '',
        'inicio_resultado': inicio_resultado,
        'fin_resultado': fin_resultado,
        'urls_paginas': [
            {'numero': n, 'url': build_audit_page_url(n), 'actual': n == pagina}
            for n in rango_paginas
        ],
        'muestra_primera': rango_inicio > 1,
        'muestra_ultima': rango_fin < total_paginas,
        'url_primera': build_audit_page_url(1),
        'url_ultima': build_audit_page_url(total_paginas),
    }

    return render_template(
        'autogestion_auditoria_login.html',
        eventos=eventos,
        filtro_usuario=filtro_usuario,
        filtro_resultado=filtro_resultado,
        filtro_ip=filtro_ip,
        filtro_desde=filtro_desde,
        filtro_hasta=filtro_hasta,
        total_eventos=total_eventos,
        paginacion=paginacion,
    )

# ------------------------------
# EJECUCIÓN
# ------------------------------
if __name__ == '__main__':
    debug = os.environ.get('FLASK_DEBUG', 'false').lower() == 'true'
    app.run(debug=debug)