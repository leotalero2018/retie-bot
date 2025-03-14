import os
import asyncio
import requests
import tempfile
from dotenv import load_dotenv
from openai import OpenAI
from rich.console import Console
from telegram import Update, InputFile
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackContext
from concurrent.futures import ThreadPoolExecutor
from openai.types.beta.threads.text_content_block import TextContentBlock
import re
from minio import Minio
from datetime import timedelta

# Cargar variables de entorno
load_dotenv()


console = Console()
client = OpenAI()

# Claves API
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
ASSISTANT_ID = os.getenv('OPENAI_ASSISTANT_ID')

executor = ThreadPoolExecutor()
async def transcribe_audio(audio_path):
    """Convierte audio a texto usando OpenAI Whisper."""
=======
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
        with open(audio_path, "rb") as audio_file:
            response = client.audio.transcriptions.create(
                model="whisper-1",
                file=audio_file,
                language="es"
            )
        return response.text  # Accede directamente a la propiedad `text`
    except Exception as e:
        console.print(f"Error transcribiendo audio: {e}", style="bold red")
        return None



async def handle_audio_message(update: Update, context: CallbackContext):
    """Maneja mensajes de voz en Telegram: los transcribe y responde en texto."""
    voice = update.message.voice
    file = await context.bot.get_file(voice.file_id)

    # Obtener la URL del archivo
    file_url = file.file_path

    # Crear un archivo temporal para almacenar el audio descargado
    with tempfile.NamedTemporaryFile(delete=False, suffix=".ogg") as temp_audio:
        file_path = temp_audio.name

    # Descargar el archivo manualmente
    response = requests.get(file_url)
    with open(file_path, "wb") as audio_file:
        audio_file.write(response.content)

    console.print(f"Audio descargado: {file_path}", style="bold green")

    # Transcribir el audio
    transcript = await transcribe_audio(file_path)

    # Eliminar el archivo temporal después de usarlo
    os.remove(file_path)

    if transcript:
        await update.message.reply_text(f"Texto transcrito: {transcript}")

        # Obtener respuesta del asistente
        response_text = await get_assistant_response(transcript)

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



        # Enviar respuesta en texto
        await update.message.reply_text(response_text)
    else:
        await update.message.reply_text("No pude transcribir el audio.")



async def get_assistant_response(user_message):
    """Envía mensaje a OpenAI y obtiene respuesta del asistente."""
    try:
        # Crear un nuevo thread en OpenAI si no existe
        thread = client.beta.threads.create()

        # Agregar el mensaje del usuario al thread
        client.beta.threads.messages.create(
            thread_id=thread.id,
            role="user",
            content=user_message
        )

        # Ejecutar el asistente con el thread recién creado
        run = client.beta.threads.runs.create(
            thread_id=thread.id,
            assistant_id=ASSISTANT_ID
        )

        # Esperar a que la ejecución se complete
        while True:
            run_status = client.beta.threads.runs.retrieve(thread_id=thread.id, run_id=run.id)
            if run_status.status == "completed":
                break
            await asyncio.sleep(1)  # Esperar antes de volver a consultar el estado

        # Obtener los mensajes de respuesta del asistente
        messages = client.beta.threads.messages.list(thread_id=thread.id)

        # Filtrar el último mensaje del asistente
        for message in reversed(messages.data):  # Orden inverso: del más reciente al más antiguo
            if message.role == "assistant":
                return message.content[0].text.value  # Devuelve la respuesta en texto

        return "El asistente no generó una respuesta."
    except Exception as e:
        console.print(f"Error obteniendo respuesta de OpenAI: {e}", style="bold red")
        return "Error al obtener la respuesta del asistente."


async def handle_text_message(update: Update, context: CallbackContext):
    """Maneja mensajes de texto en Telegram."""
    user_message = update.message.text
    response_text = await get_assistant_response(user_message)

    # Enviar respuesta en texto y en voz
    await update.message.reply_text(response_text)

    audio_response = await generate_voice(response_text)
    if audio_response:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as temp_audio_file:
            temp_audio_file.write(audio_response.content)
            temp_audio_path = temp_audio_file.name

        # Enviar audio de respuesta
        with open(temp_audio_path, "rb") as audio_file:
            await update.message.reply_voice(voice=InputFile(audio_file), caption="Aquí está la respuesta en voz.")

        os.remove(temp_audio_path)

async def start(update: Update, context: CallbackContext):
    """Comando de inicio."""
    await update.message.reply_text("¡Hola! Puedes enviarme texto o audios y responderé con voz.")

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

    """Ejecuta el bot de Telegram."""
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # Agregar manejadores
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_message))
    application.add_handler(MessageHandler(filters.VOICE, handle_audio_message))

    """Función principal para ejecutar el bot"""
    TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # Agregar handlers para comandos y mensajes
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(MessageHandler(filters.PHOTO, handle_photo))


    # Iniciar bot
    application.run_polling()

logging.basicConfig(
    format='%(asctime)s - %(levelname)s - %(message)s',
    level=logging.DEBUG
)

logger = logging.getLogger(__name__)

if __name__ == '__main__':
    main()
