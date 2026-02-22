import os
import io
import logging
from collections import defaultdict
import json
from http.server import BaseHTTPRequestHandler

from telegram import Update, BotCommand
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler, filters

from google import genai
from google.genai import types
from PIL import Image

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# Enable logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# Vercel Serverless environment is ephemeral. 
# We don't have a persistent 'styles' directory across container restarts.
# For a true DB-less serverless environment, we must use an external storage (like AWS S3) 
# OR use Telegram's file_ids as reference strings.
# Since we are moving to Vercel, saving PIL images to disk will reset every time Vercel scales down.
# Let's adapt this by saving Telegram File IDs in memory for the life of the instance,
# or ideally we'd need a DB. For this quick Vercel port, we'll keep the in-memory dictionary.
# NOTE: In production on Vercel, `chat_styles_file_ids` will wipe if the lambda goes cold.

# Chat ID -> list of Telegram file_ids (style references)
chat_styles_file_ids = defaultdict(list)
# Chat ID -> boolean (whether the user is currently expected to send style images)
chat_is_setting_style = defaultdict(bool)

if GEMINI_API_KEY:
    gemini_client = genai.Client(api_key=GEMINI_API_KEY)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = (
        "Benvenuto! Sono il tuo bot per il trasferimento di stile (Versione Vercel).\n"
        "Comandi disponibili:\n"
        "/set_style - Inizia a inviarmi immagini di referenza per impostare il 'mood'.\n"
        "/done_style - Termina l'inserimento delle referenze di stile.\n"
        "/clear_style - Cancella tutte le immagini di referenza salvate.\n"
        "/status - Controlla quante immagini di referenza hai salvato.\n\n"
        "Quando hai impostato il tuo stile, inviami la foto che desideri trasformare!"
    )
    await update.message.reply_text(message)

async def set_style(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.message.chat_id
    chat_is_setting_style[chat_id] = True
    await update.message.reply_text(
        "Modalità Set Style attivata! Ora inviami tutte le immagini che definiranno il 'Mood'.\n"
        "Quando hai finito, digita /done_style."
    )

async def done_style(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.message.chat_id
    chat_is_setting_style[chat_id] = False
    count = len(chat_styles_file_ids[chat_id])
    await update.message.reply_text(f"Modalità Set Style terminata. Hai {count} immagini di referenza salvate.\nOra inviami la foto (Soggetto) che vuoi trasformare.")

async def clear_style(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.message.chat_id
    chat_styles_file_ids[chat_id] = []
    await update.message.reply_text("Tutte le immagini di referenza sono state cancellate.")

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.message.chat_id
    count = len(chat_styles_file_ids[chat_id])
    await update.message.reply_text(f"Attualmente hai {count} immagini di referenza salvate in questa sessione server.")

async def handle_image(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.message.chat_id
    
    photo = update.message.photo[-1]
    file_id = photo.file_id
    
    caption_text = (update.message.caption or "").strip().lower()
    is_caption_asking_to_save = any(word in caption_text for word in ["add", "ref", "style", "salva", "mood"])
    
    if chat_is_setting_style[chat_id] or is_caption_asking_to_save:
        chat_styles_file_ids[chat_id].append(file_id)
        count = len(chat_styles_file_ids[chat_id])
        
        if is_caption_asking_to_save and not chat_is_setting_style[chat_id]:
           await update.message.reply_text(f"Didascalia riconosciuta! Immagine di referenza aggiunta rapidamente. Totale referenze: {count}")
        else:
           await update.message.reply_text(f"Immagine di referenza salvata con successo. Ne hai {count} salvate.")
    else:
        # Treat as subject
        ref_file_ids = chat_styles_file_ids[chat_id]
        if not ref_file_ids:
            await update.message.reply_text(
                "Non hai impostato alcuna immagine di referenza per lo stile. "
                "Per favore, usa /set_style e inviami alcune immagini da usare come moodboard."
            )
            return
        
        await update.message.reply_text("Ricevuto il soggetto. Scaricando temporaneamente le immagini per Gemini... Attendi.")
        
        try:
            # Download subject
            subject_file = await context.bot.get_file(file_id)
            subject_bytes = await subject_file.download_as_bytearray()
            subject_image = Image.open(io.BytesIO(subject_bytes))
            
            # Download references
            loaded_styles = []
            for r_file_id in ref_file_ids[:10]: # limit to 10 max
                 r_file = await context.bot.get_file(r_file_id)
                 r_bytes = await r_file.download_as_bytearray()
                 loaded_styles.append(Image.open(io.BytesIO(r_bytes)))

            prompt = (
                "Maintain the main subject, composition, and content of the first image perfectly intact. "
                "Apply the exact aesthetic, mood, lighting, color grading, and style of the supplementary reference images to the main subject."
            )
            
            contents = [prompt, subject_image] + loaded_styles
            
            response = gemini_client.models.generate_content(
                model="gemini-3-pro-image-preview",
                contents=contents,
                config=types.GenerateContentConfig(
                    response_modalities=["IMAGE"],
                    image_config=types.ImageConfig(image_size="1K")
                )
            )
            
            generated_image_bytes = None
            for part in response.parts:
                if hasattr(part, 'as_image') and (img := part.as_image()):
                    img_byte_arr = io.BytesIO()
                    img.save(img_byte_arr, format='PNG')
                    generated_image_bytes = img_byte_arr.getvalue()
                    break
            
            if generated_image_bytes:
                await update.message.reply_photo(photo=generated_image_bytes, caption="Ecco la tua immagine trasformata!")
            else:
                await update.message.reply_text("Generazione fallita.")
                
        except Exception as e:
            error_str = str(e)
            logging.error(f"Error calling Gemini: {error_str}")
            if "429" in error_str or "RESOURCE_EXHAUSTED" in error_str:
                await update.message.reply_text("❌ Errore 429: Hai esaurito la quota limite di Gemini. Riprova tra un minuto.")
            else:
                await update.message.reply_text(f"Errore: {e}")

# Build telegram application globally
app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("set_style", set_style))
app.add_handler(CommandHandler("done_style", done_style))
app.add_handler(CommandHandler("clear_style", clear_style))
app.add_handler(CommandHandler("status", status))
app.add_handler(MessageHandler(filters.PHOTO, handle_image))

# Setup logic for Vercel Serverless Function
class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        try:
            content_length = int(self.headers['Content-Length'])
            post_data = self.rfile.read(content_length)
            update_json = json.loads(post_data.decode('utf-8'))
            
            # Since Vercel executes this synchronously per request, we need an event loop 
            # to process the Telegram Update async object.
            import asyncio
            
            async def process_update():
                await app.initialize()
                update_obj = Update.de_json(update_json, app.bot)
                await app.process_update(update_obj)
            
            asyncio.run(process_update())
            
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"OK")
        except Exception as e:
            logging.error(f"Error in webhook: {e}")
            self.send_response(500)
            self.end_headers()

    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is definitely running on Vercel!")
