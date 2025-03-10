import os
import time
from dotenv import load_dotenv
from openai import OpenAI
from rich.console import Console
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackContext
import logging
import asyncio
from concurrent.futures import ThreadPoolExecutor
from openai.types.beta.threads.text_content_block import TextContentBlock
import re
from minio import Minio
from datetime import timedelta
import base64

# Load environment variables from .env
load_dotenv()

console = Console()

client = OpenAI()
OpenAI.api_key = os.getenv('OPENAI_API_KEY')

assistant_id = os.getenv('OPENAI_ASSISTANT_ID')

# Configuración de MinIO en Railway
MINIO_ENDPOINT = "bucket-production-fabf.up.railway.app"
MINIO_ACCESS_KEY = os.getenv("MINIO_ROOT_USER")
MINIO_SECRET_KEY = os.getenv("MINIO_ROOT_PASSWORD")
BUCKET_NAME = "telegram-bot-images"
MINIO_PORT = 443

minio_client = Minio(
    MINIO_ENDPOINT,
    access_key=MINIO_ACCESS_KEY,
    secret_key=MINIO_SECRET_KEY,
    secure=True  # Railway usa HTTPS
)

# Verificar conexión
print("Conectado a MinIO en Railway.")

# Asegurar que el bucket existe
if not minio_client.bucket_exists(BUCKET_NAME):
    minio_client.make_bucket(BUCKET_NAME)
    print(f"Bucket {BUCKET_NAME} creado.")
else:
    print(f"El bucket {BUCKET_NAME} ya existe.")

# Crear un nuevo thread
executor = ThreadPoolExecutor()

async def create_thread():
    """Crear un nuevo hilo de OpenAI de forma asíncrona"""
    loop = asyncio.get_running_loop()
    try:
        my_thread = await loop.run_in_executor(executor, client.beta.threads.create)
        return my_thread.id
    except Exception as e:
        console.print(f"Failed to create thread: {e}", style="bold red")
        return None

async def get_assistant_response_with_file_upload(local_file_path):
    """
    Envía la imagen a OpenAI subiéndola como archivo para obtener un file_id.
    Luego envía un mensaje con ese file_id para análisis de imagen.
    """
    try:
        # Subir la imagen a OpenAI para obtener el file_id
        with open(local_file_path, "rb") as image_file:
            response_file = client.File.create(
                file=image_file,
                purpose="vision"
            )
        file_id = response_file["id"]
        logger.info(f"Imagen subida a OpenAI con file_id: {file_id}")

        # Enviar el mensaje usando el file_id
        response = client.chat.completions.create(
            model="gpt-4-vision-preview",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Analiza esta imagen y dime qué contiene."},
                        {"type": "image_file", "image_file": {"file_id": file_id, "detail": "low"}}
                    ]
                }
            ],
            max_tokens=300
        )
        return [response.choices[0].message.content]
    except Exception as e:
        logger.error(f"Error al procesar la imagen con OpenAI usando file upload: {e}")
        return None

async def get_assistant_response_with_base64(local_file_path):
    """Enviar una imagen en formato Base64 a OpenAI."""
    try:
        with open(local_file_path, "rb") as image_file:
            base64_image = base64.b64encode(image_file.read()).decode("utf-8")

        response = client.chat.completions.create(
            model="gpt-4-vision-preview",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Analiza esta imagen y dime qué contiene."},
                        {"type": "image_base64", "image_base64": base64_image}
                    ]
                }
            ],
            max_tokens=300
        )
        return [response.choices[0].message.content]
    except Exception as e:
        logger.error(f"Error al procesar la imagen con OpenAI usando Base64: {e}")
        return ["Hubo un error al procesar la imagen."]

async def handle_message(update: Update, context: CallbackContext):
    user_message = update.message.text
    thread_id = context.user_data.get('thread_id')

    if not thread_id:
        thread_id = await create_thread()
        if thread_id:
            context.user_data['thread_id'] = thread_id
        else:
            await update.message.reply_text('Error iniciando la conversación con el asistente.')
            return

    # Aquí se procesa el mensaje de texto normal
    response = client.chat.completions.create(
        model="gpt-4-vision-preview",
        messages=[{"role": "user", "content": [{"type": "text", "text": user_message}]}],
        max_tokens=300
    )
    for text in [response.choices[0].message.content]:
        await update.message.reply_text(text)

async def handle_photo(update: Update, context: CallbackContext):
    """Manejar imágenes: descargar, subir a MinIO, enviar a OpenAI y responder al usuario."""
    logger.info("Recibí una imagen del usuario.")
    try:
        photo = update.message.photo[-1]  # Tomar la imagen con mejor resolución
        file = await context.bot.get_file(photo.file_id)
        local_file_path = f"downloads/{photo.file_id}.jpg"

        os.makedirs("downloads", exist_ok=True)

        # Descargar la imagen
        await file.download_to_drive(local_file_path)
        logger.info(f"Imagen guardada en {local_file_path}")

        # Subir a MinIO
        minio_object_name = f"images/{photo.file_id}.jpg"
        minio_client.fput_object(BUCKET_NAME, minio_object_name, local_file_path)

        # Generar URL firmada para acceso (opcional, si se requiere)
        image_url = minio_client.presigned_get_object(BUCKET_NAME, minio_object_name, expires=timedelta(days=7))
        logger.info(f"Imagen subida a MinIO: {image_url}")

        # Obtener el thread del usuario o crear uno nuevo
        thread_id = context.user_data.get('thread_id')
        if not thread_id:
            thread_id = await create_thread()
            if thread_id:
                context.user_data['thread_id'] = thread_id
            else:
                await update.message.reply_text("Error iniciando la conversación con el asistente.")
                return

        # Primero, intentamos enviar la imagen usando la subida a OpenAI para obtener file_id
        response = await get_assistant_response_with_file_upload(local_file_path)

        # Si falla el métod file upload, se usa la conversión a Base64
        if response is None:
            logger.info("La subida de archivo a OpenAI falló, probando con Base64.")
            response = await get_assistant_response_with_base64(local_file_path)

        # Enviar la respuesta del asistente al usuario
        for text in response:
            await update.message.reply_text(text)

    except Exception as e:
        logger.error(f"Error al manejar la imagen: {e}")
        await update.message.reply_text("Hubo un error al procesar la imagen.")

async def start(update: Update, context: CallbackContext):
    """Comando /start del bot"""
    await update.message.reply_text('Hola, puedes enviar tus dudas o imágenes para análisis.')

def main():
    """Función principal para ejecutar el bot"""
    TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # Agregar handlers para comandos y mensajes
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(MessageHandler(filters.PHOTO, handle_photo))

    application.run_polling()

logging.basicConfig(
    format='%(asctime)s - %(levelname)s - %(message)s',
    level=logging.DEBUG
)

logger = logging.getLogger(__name__)

if __name__ == '__main__':
    main()
