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

async def get_assistant_response(thread_id, user_message=None, image_url=None):
    """Enviar un mensaje (texto + imagen) al asistente de OpenAI y devolver la respuesta"""
    loop = asyncio.get_running_loop()
    try:
        content = []

        # Agregar el texto si el usuario lo proporciona
        if user_message:
            content.append({"type": "text", "text": user_message})

        # Agregar la imagen si existe
        if image_url:
            content.append({"type": "image_url", "image_url": {"url": image_url}})

        # Asegurarse de que hay contenido antes de enviar
        if not content:
            console.print("No hay contenido para enviar al asistente.", style="bold red")
            return ["No se detectó texto ni imagen para procesar."]

        # Enviar mensaje al asistente
        await loop.run_in_executor(executor, lambda: client.beta.threads.messages.create(
            thread_id=thread_id,
            role="user",
            content=content
        ))

        # Ejecutar el asistente con instrucciones para que solo responda la pregunta
        my_run = await loop.run_in_executor(executor, lambda: client.beta.threads.runs.create(
            thread_id=thread_id,
            assistant_id=assistant_id,
            instructions="Responde únicamente la pregunta presente en la imagen o en el texto, sin agregar información extra."
        ))

        # Esperar respuesta
        while True:
            run_status = await loop.run_in_executor(executor, lambda: client.beta.threads.runs.retrieve(
                thread_id=thread_id,
                run_id=my_run.id
            ))
            if run_status.status == "completed":
                break
            await asyncio.sleep(1)

        # Obtener la respuesta
        all_messages = await loop.run_in_executor(executor, lambda: client.beta.threads.messages.list(thread_id=thread_id))
        responses = []

        latest_message_time = max(
            msg.created_at for msg in all_messages.data if msg.role == 'assistant')

        for message in all_messages.data:
            if message.role == 'assistant' and message.created_at == latest_message_time:
                for content_block in message.content:
                    if isinstance(content_block, TextContentBlock):
                        responses.append(content_block.text.value)

        return responses
    except Exception as e:
        console.print(f"Failed to get response: {e}", style="bold red")
        return ["Error al obtener respuesta del asistente."]




async def handle_message(update: Update, context: CallbackContext):
    user_message = update.message.text if update.message.text else ""
    thread_id = context.user_data.get('thread_id')

    if not thread_id:
        thread_id = await create_thread()
        if thread_id:
            context.user_data['thread_id'] = thread_id
        else:
            await update.message.reply_text('Error iniciando la conversación con el asistente.')
            return

    image_url = None

    # Verificar si el mensaje incluye una imagen
    if update.message.photo:
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

            # Generar URL firmada para acceso
            image_url = minio_client.presigned_get_object(BUCKET_NAME, minio_object_name, expires=timedelta(days=7))
            logger.info(f"Imagen subida a MinIO: {image_url}")

        except Exception as e:
            logger.error(f"Error al manejar la imagen: {e}")
            await update.message.reply_text("Hubo un error al procesar la imagen.")

    # Enviar texto e imagen (si está disponible) al asistente
    response = await get_assistant_response(thread_id, user_message, image_url)

    for text in response:
        await update.message.reply_text(text)

async def start(update: Update, context: CallbackContext):
    """Comando /start del bot"""
    await update.message.reply_text('Hola, puedes enviar tus dudas sobre RETIE')

async def handle_photo(update: Update, context: CallbackContext):
    """Manejar imágenes y enviarlas al asistente de OpenAI para responder preguntas dentro de ellas."""
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

        # Generar URL pública para la imagen
        image_url = minio_client.presigned_get_object(BUCKET_NAME, minio_object_name, expires=timedelta(days=7))
        logger.info(f"Imagen subida a MinIO y accesible en: {image_url}")

        # Obtener el thread_id del usuario
        thread_id = context.user_data.get('thread_id')
        if not thread_id:
            thread_id = await create_thread()
            if thread_id:
                context.user_data['thread_id'] = thread_id
            else:
                await update.message.reply_text('Error iniciando la conversación con el asistente.')
                return

        # Enviar solo la imagen al asistente sin texto adicional
        response = await get_assistant_response(thread_id, image_url=image_url)

        # Enviar la respuesta al usuario
        for text in response:
            await update.message.reply_text(text)

    except Exception as e:
        logger.error(f"Error al manejar la imagen: {e}")
        await update.message.reply_text("Hubo un error al procesar la imagen.")



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