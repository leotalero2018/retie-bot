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

def main():
    """Ejecuta el bot de Telegram."""
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # Agregar manejadores
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_message))
    application.add_handler(MessageHandler(filters.VOICE, handle_audio_message))

    # Iniciar bot
    application.run_polling()

if __name__ == '__main__':
    main()
