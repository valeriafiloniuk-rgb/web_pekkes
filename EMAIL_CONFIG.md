# 📧 Configurar Envío de Email en Mundo Pekkes

## Estado Actual
Actualmente el sistema de recuperación de contraseña funciona en modo **TESTING**:
- Genera un enlace de recuperación válido por 30 minutos
- Muestra el enlace en pantalla para que lo uses inmediatamente
- Si hay credenciales configuradas, intenta enviar por email

## 🚀 Para Habilitar Envío Real de Email

### Opción 1: Usando Gmail (Recomendado)

1. **Abre Gmail**
   - Ve a tu cuenta: https://myaccount.google.com/apppasswords

2. **Crea una "Contraseña de Aplicación"**
   - Selecciona "Mail" y "Windows" 
   - Genera una contraseña de 16 caracteres
   - Copia la contraseña (sin espacios)

3. **Crea el archivo `.env`**
   ```bash
   Copy .env.example to .env
   ```
   O crea el archivo manualmente en la raíz del proyecto:
   ```
   MAIL_SERVER=smtp.gmail.com
   MAIL_PORT=587
   MAIL_USE_TLS=True
   MAIL_USERNAME=tu-email@gmail.com
   MAIL_PASSWORD=tu-contraseña-app-generada
   MAIL_DEFAULT_SENDER=tu-email@gmail.com
   ```

4. **Reinicia Flask**
   ```bash
   Ctrl+C para detener el servidor
   python app.py
   ```

5. **Prueba**
   - Ingresa a / olvidé contraseña
   - Ingresa un email registrado
   - Recibirás un email con el enlace

### Opción 2: Usando Outlook/Office365

```env
MAIL_SERVER=smtp.office365.com
MAIL_PORT=587
MAIL_USE_TLS=True
MAIL_USERNAME=tu-email@outlook.com
MAIL_PASSWORD=tu-contraseña
MAIL_DEFAULT_SENDER=tu-email@outlook.com
```

### Opción 3: Usar un Servicio Profesional (SendGrid, etc)

Reemplaza los valores según la documentación del servicio.

---

## 🔒 Seguridad

**IMPORTANTE**: 
- Nunca hagas push del archivo `.env` a Git
- Está listado en `.gitignore`
- En producción, usa variables de entorno del servidor (Heroku, AWS, etc)

---

## ⚠️ Solución de Problemas

**Error: "SMTPAuthenticationError"**
- Verifica que la contraseña sea correcta
- Es la contraseña de APP, no la contraseña de Gmail

**Error: "SMTPServerDisconnected"**
- Verifica MAIL_SERVER y MAIL_PORT
- Gmail usa smtp.gmail.com:587

**No envía emails pero no hay error**
- Probablemente no tienes credenciales configuradas
- El sistema mostrará el enlace en pantalla (Testing mode)

---

## 📝 Pruebas en Local

Mientras configuras el email, el sistema en modo Testing:
1. Genera el token
2. Guarda en BD
3. Muestra enlace clickeable en pantalla
4. Es perfecto para desarrollo

Simplemente haz click en el enlace para resetear la contraseña.
