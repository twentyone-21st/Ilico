# Instalación y ejecución local

## Requisitos previos

- Python 3.10 o superior
- Cuenta de Google con Gmail habilitado
- Credenciales OAuth2 de Google Cloud Console (`credentials.json`)

---

## 1. Clonar el repositorio

```bash
git clone https://github.com/twentyone-21st/Ilico.git
cd Ilico
```

## 2. Instalar dependencias

```bash
pip install -r requirements.txt
```

## 3. Configurar credenciales de Google

1. Ve a [Google Cloud Console](https://console.cloud.google.com)
2. Crea un proyecto y habilita la **Gmail API**
3. Genera credenciales OAuth2 de tipo "Aplicación de escritorio"
4. Descarga el archivo como `credentials.json` y colócalo en la raíz del proyecto

## 4. Configurar variables de entorno

Crea un archivo `.env` en la raíz con:

```env
SECRET_KEY=tu_clave_secreta_aqui
```

Para producción en Railway, agrega también:

```env
GOOGLE_CREDENTIALS_JSON={"web":{"client_id":"...","client_secret":"...",...}}
RAILWAY_PUBLIC_DOMAIN=tu-dominio.railway.app
```

## 5. Ejecutar el servidor

```bash
python app.py
```

Abre el navegador en [http://localhost:8080](http://localhost:8080).

---

## Notas sobre el modelo

Al arrancar por primera vez, el servidor entrena automáticamente el clasificador con el dataset interno. Esto toma entre 5 y 30 segundos.

Si tienes el archivo `modelo_spam.pkl` de una ejecución anterior, el servidor lo carga directamente sin reentrenar.
