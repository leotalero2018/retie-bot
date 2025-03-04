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

# Load environment variables from .env
load_dotenv()

console = Console()

client = OpenAI()
OpenAI.api_key = os.getenv('OPENAI_API_KEY')

assistant_id = os.getenv('OPENAI_ASSISTANT_ID')

# Create a new thread
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


#my_thread = client.beta.threads.create()

async def get_assistant_response(thread_id, user_message):
    """Send user message to OpenAI and return the assistant's response asynchronously."""
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
        return "An error occurred while getting a response from the assistant."



async def handle_message(update: Update, context: CallbackContext):
    user_message = update.message.text
    thread_id = context.user_data.get('thread_id')

    if not thread_id:
        thread_id = await create_thread()
        if thread_id:
            context.user_data['thread_id'] = thread_id
        else:
            await update.message.reply_text('Error initializing conversation with assistant.')
            return

    response = await get_assistant_response(thread_id, user_message)

    # Format each part of the response before sending
    formatted_responses = [format_message(text) for text in response]
    for text in formatted_responses:
        await update.message.reply_text(text)


def format_message(text):
    # Replace Unicode escape sequences with actual Unicode characters
    text = text.encode('utf-8').decode('utf-8')

    # Optional: Remove source citations or other unwanted parts of the text
    text = re.sub(r'【[\d:†source\】]+', '', text)

    # Ensure newline characters are handled correctly (usually they are, this is just for completeness)
    text = text.replace('\\n', '\n')

    return text


async def start(update: Update, context: CallbackContext):
    """Start command for Telegram bot."""
    await update.message.reply_text('Hola, puedes enviar tus dudas sobre RETIE')

def main():
    """Main function to run the Telegram bot."""
    TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # Add handlers for commands and messages
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    application.run_polling()

if __name__ == '__main__':
    main()