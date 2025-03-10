import os
from dotenv import load_dotenv
from openai import OpenAI

# Cargar variables de entorno desde .env
load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

if not OPENAI_API_KEY:
    raise ValueError("❌ ERROR: La API Key de OpenAI no está configurada correctamente.")

print(f"🔑 Clave API Cargada: {OPENAI_API_KEY[:10]}********")

client = OpenAI(api_key=OPENAI_API_KEY)

response = client.chat.completions.create(
    model="gpt-4-turbo",
    messages=[
        {"role": "system", "content": "Describe esta imagen."},
        {"role": "user", "content": [{"type": "image_url", "image_url": "https://upload.wikimedia.org/wikipedia/commons/3/3a/Cat03.jpg"}]}
    ],
    max_tokens=300
)

print(response.choices[0].message.content)
