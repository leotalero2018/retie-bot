import os
import time
import logging
import asyncio
import re
import base64
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from dotenv import load_dotenv
from openai import OpenAI
from rich.console import Console
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackContext
from openai.types.beta.threads.text_content_block import TextContentBlock
from pymongo import MongoClient
from minio import Minio

# Load environment variables from .env
load_dotenv()

# Configuración de logging
logging.basicConfig(
    format='%(asctime)s - %(levelname)s - %(message)s',
    level=logging.DEBUG
)
logger = logging.getLogger(__name__)

console = Console()

# OpenAI API client
client = OpenAI()
OpenAI.api_key = os.getenv('OPENAI_API_KEY')
assistant_id = os.getenv('OPENAI_ASSISTANT_ID')

# MongoDB Connection
mongo_uri = os.getenv('MONGO_URI')
client_mongo = MongoClient(mongo_uri, tls=True, tlsAllowInvalidCertificates=True)
db = client_mongo["telegram_bot"]
collection = db["conversations"]

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

# Verificar conexión y creación del bucket
try:
    if not minio_client.bucket_exists(BUCKET_NAME):
        minio_client.make_bucket(BUCKET_NAME)
        logger.info(f"Bucket {BUCKET_NAME} creado.")
    else:
        logger.info(f"El bucket {BUCKET_NAME} ya existe.")
    logger.info("Conectado a MinIO en Railway.")
except Exception as e:
    logger.error(f"Error al conectar con MinIO: {e}")

# Thread Executor
executor = ThreadPoolExecutor()


async def create_thread():
    """Create a new OpenAI thread asynchronously by running synchronous code in an executor."""
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
        # Add user message to the thread using an executor for synchronous calls
        await loop.run_in_executor(executor, lambda: client.beta.threads.messages.create(
            thread_id=thread_id,
            role="user",
            content=user_message
        ))

        # Run the assistant
        my_run = await loop.run_in_executor(executor, lambda: client.beta.threads.runs.create(
            thread_id=thread_id,
            assistant_id=assistant_id,
            instructions="Reply in Spanish."
        ))

        # Wait for the run to complete
        while True:
            run_status = await loop.run_in_executor(executor, lambda: client.beta.threads.runs.retrieve(
                thread_id=thread_id,
                run_id=my_run.id
            ))
            if run_status.status == "completed":
                break
            await asyncio.sleep(1)

        # Retrieve the messages added by the assistant to the thread
        all_messages = await loop.run_in_executor(executor,
                                                  lambda: client.beta.threads.messages.list(thread_id=thread_id))
        responses = []

        console.print(all_messages.data)

        latest_message_time = max(
            msg.created_at for msg in all_messages.data if msg.role == 'assistant')  # Get latest time

        for message in all_messages.data:
            if message.role == 'assistant' and message.created_at == latest_message_time:  # Filter for the latest assistant message only
                for content_block in message.content:
                    if isinstance(content_block, TextContentBlock):
                        print("------------------------------------- RESPONSE", content_block.text.value)
                        responses.append(content_block.text.value)

        return responses
    except Exception as e:
        console.print(f"Failed to get response: {e}", style="bold red")
        return ["Error al obtener respuesta del asistente."]

async def process_text_with_gpt(user_message):
    """Procesar mensaje de texto directamente con GPT-4."""
    try:
        response = client.chat.completions.create(
            model="gpt-4-vision-preview",
            messages=[{"role": "user", "content": [{"type": "text", "text": user_message}]}],
            max_tokens=300
        )
        return [response.choices[0].message.content]
    except Exception as e:
        logger.error(f"Error al procesar texto con GPT-4: {e}")
        return ["Hubo un error al procesar tu mensaje."]


async def get_assistant_response_with_image_url(image_url):
    """Enviar una imagen a OpenAI usando `image_url`."""
    try:
        response = client.chat.completions.create(
            model="gpt-4-vision-preview",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Analiza esta imagen y dime qué contiene."},
                        {"type": "image_url", "image_url": image_url}
                    ]
                }
            ],
            max_tokens=300
        )
        return [response.choices[0].message.content]
    except Exception as e:
        logger.error(f"Error al procesar la imagen con OpenAI usando URL: {e}")
        return None  # Retorna None si falla


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
                        {"type": "image_url", "image_url": f"data:image/jpeg;base64,{base64_image}"}
                    ]
                }
            ],
            max_tokens=300
        )
        return [response.choices[0].message.content]
    except Exception as e:
        logger.error(f"Error al procesar la imagen con OpenAI usando Base64: {e}")
        return ["Error al procesar la imagen."]


async def get_assistant_response_with_file_upload(local_file_path):
    """
    Envía la imagen a OpenAI subiéndola como archivo para obtener un file_id.
    Luego envía un mensaje con ese file_id para análisis de imagen.
    """
    try:
        # Este método no está presente en el código original pero se menciona,
        # por lo que se deja como un placeholder
        # La implementación correcta dependería de la API específica que se esté usando
        logger.error("Método get_assistant_response_with_file_upload no implementado")
        return None
    except Exception as e:
        logger.error(f"Error al subir archivo a OpenAI: {e}")
        return None


def format_message(text):
    """Format the response text before sending."""
    text = text.encode('utf-8').decode('utf-8')
    text = re.sub(r'【[\d:†source\】]+', '', text)
    text = text.replace('\\n', '\n')
    return text


async def handle_message(update: Update, context: CallbackContext):
    """Handle incoming text messages from users."""
    user_message = update.message.text if update.message.text else ""
    user_id = update.message.from_user.id
    user_name = update.message.from_user.username
    chat_id = update.message.chat_id
    timestamp = update.message.date
    thread_id = context.user_data.get('thread_id')

    # Crear thread si no existe
    if not thread_id:
        thread_id = await create_thread()
        if thread_id:
            context.user_data['thread_id'] = thread_id
        else:
            await update.message.reply_text('Error initializing conversation with assistant.')
            return

    # Intentar primero con el asistente de OpenAI
    try:
        response = await get_assistant_response(thread_id, user_message)
    except Exception as e:
        logger.error(f"Error con el asistente de OpenAI: {e}")
        # Si falla, usar GPT-4 directamente
        response = await process_text_with_gpt(user_message)

    # Save conversation to MongoDB
    conversation_data = {
        "user_id": user_id,
        "username": user_name,
        "chat_id": chat_id,
        "thread_id": thread_id,
        "timestamp": timestamp,
        "user_message": user_message,
        "bot_responses": response
    }

    collection.insert_one(conversation_data)

    # Format each part of the response before sending
    formatted_responses = [format_message(text) for text in response]
    for text in formatted_responses:
        await update.message.reply_text(text)


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

        # Combinamos ambas implementaciones:
        # 1. Intentamos usar el método unificado de get_assistant_response
        response = await get_assistant_response(thread_id, image_url=image_url)
        
        # Si no hay respuesta o hay algún problema, intentamos los métodos en cascada
        if not response or response[0] == "Error al obtener respuesta del asistente.":
            logger.info("Intentando métodos alternativos para procesar la imagen...")
            
            # Estrategia en cascada para procesar la imagen:
            # 1. Primero intentamos usar la subida de archivos (método más nuevo y recomendado)
            response = await get_assistant_response_with_file_upload(local_file_path)

            # 2. Si falla, intentamos con la URL de MinIO
            if response is None:
                logger.info("La subida de archivo a OpenAI falló, probando con URL.")
                response = await get_assistant_response_with_image_url(image_url)

            # 3. Si ambos métodos fallan, usamos Base64 como último recurso
            if response is None:
                logger.info("El método URL también falló, probando con Base64.")
                response = await get_assistant_response_with_base64(local_file_path)

        # Guardar la conversación en MongoDB
        user_id = update.message.from_user.id
        user_name = update.message.from_user.username
        chat_id = update.message.chat_id
        timestamp = update.message.date
        
        conversation_data = {
            "user_id": user_id,
            "username": user_name,
            "chat_id": chat_id,
            "thread_id": thread_id,
            "timestamp": timestamp,
            "user_message": "[IMAGEN]",
            "image_url": image_url,
            "bot_responses": response
        }

        collection.insert_one(conversation_data)

        # Enviar la respuesta del asistente al usuario
        formatted_responses = [format_message(text) for text in response]
        for text in formatted_responses:
            await update.message.reply_text(text)

    except Exception as e:
        logger.error(f"Error al manejar la imagen: {e}")
        await update.message.reply_text("Hubo un error al procesar la imagen.")


async def start(update: Update, context: CallbackContext):
    """Comando /start del bot"""
    await update.message.reply_text('Hola, puedes enviar tus dudas sobre RETIE o imágenes para análisis.')


def main():
    """Main function to run the Telegram bot."""
    TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # Add handlers for commands and messages
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(MessageHandler(filters.PHOTO, handle_photo))  # Añadir handler para fotos

    application.run_polling()


if __name__ == '__main__':
    main()