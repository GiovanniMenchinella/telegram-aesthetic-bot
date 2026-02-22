import os
import io
import logging
from collections import defaultdict
import glob
from dotenv import load_dotenv
from dotenv import load_dotenv

from telegram import Update, BotCommand
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler, filters

from google import genai
from google.genai import types
from PIL import Image

# Load environment variables
load_dotenv()
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if not TELEGRAM_BOT_TOKEN or not GEMINI_API_KEY:
    raise ValueError("Missing TELEGRAM_BOT_TOKEN or GEMINI_API_KEY in .env file.")

# Enable logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# Initialize Gemini Client
gemini_client = genai.Client(api_key=GEMINI_API_KEY)

# We will store style images on disk in a 'styles' folder to persist them across reboots
STYLES_DIR = "styles"
os.makedirs(STYLES_DIR, exist_ok=True)

# Chat ID -> boolean (whether the user is currently expected to send style images)
chat_is_setting_style = defaultdict(bool)

def get_style_images(chat_id: int):
    """Retrieve all saved style images for a given chat_id from disk."""
    patterns = [os.path.join(STYLES_DIR, f"{chat_id}_*.jpg"), 
                os.path.join(STYLES_DIR, f"{chat_id}_*.png")]
    files = []
    for p in patterns:
        files.extend(glob.glob(p))
    return sorted(files)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send a message when the command /start is issued."""
    message = (
        "Benvenuto! Sono il tuo bot per il trasferimento di stile.\n"
        "Comandi disponibili:\n"
        "/set_style - Inizia a inviarmi immagini di referenza per impostare il 'mood'.\n"
        "/done_style - Termina l'inserimento delle referenze di stile.\n"
        "/clear_style - Cancella tutte le immagini di referenza salvate.\n"
        "/status - Controlla quante immagini di referenza hai salvato.\n\n"
        "Quando hai impostato il tuo stile, inviami la foto che desideri trasformare!"
    )
    await update.message.reply_text(message)

async def set_style(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Start receiving style reference images."""
    chat_id = update.message.chat_id
    chat_is_setting_style[chat_id] = True
    await update.message.reply_text(
        "Modalità Set Style attivata! Ora inviami tutte le immagini che definiranno il 'Mood' in numero illimitato.\n"
        "Verranno salvate permanentemente.\n"
        "Quando hai finito, digita /done_style."
    )

async def done_style(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Stop receiving style reference images."""
    chat_id = update.message.chat_id
    chat_is_setting_style[chat_id] = False
    count = len(get_style_images(chat_id))
    await update.message.reply_text(f"Modalità Set Style terminata. Hai {count} immagini di referenza salvate pronte per essere usate sempre.\nOra inviami la foto (Soggetto) che vuoi trasformare.")

async def clear_style(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Clears all saved style images."""
    chat_id = update.message.chat_id
    files = get_style_images(chat_id)
    for f in files:
        try:
            os.remove(f)
        except Exception as e:
            logging.error(f"Errore rimozione file {f}: {e}")
    await update.message.reply_text("Tutte le immagini di referenza permanenti sono state cancellate. Non ne hai più salvate in memoria.")

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Check how many style images are saved."""
    chat_id = update.message.chat_id
    count = len(get_style_images(chat_id))
    if count == 0:
         await update.message.reply_text(f"Attualmente NON hai immagini di referenza salvate in memoria. Usa /set_style prima di inviare foto da alterare.")
    else:
         await update.message.reply_text(f"Attualmente hai {count} immagini di referenza salvate permanenti.")

async def handle_image(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle incoming photos to either save them as style or process them as subject."""
    chat_id = update.message.chat_id
    
    # Get the image as a byte array
    photo_file = await update.message.photo[-1].get_file()
    photo_bytes = await photo_file.download_as_bytearray()
    
    # Convert to PIL Image
    image = Image.open(io.BytesIO(photo_bytes))
    
    caption_text = (update.message.caption or "").strip().lower()
    is_caption_asking_to_save = any(word in caption_text for word in ["add", "ref", "style", "salva", "mood"])
    
    if chat_is_setting_style[chat_id] or is_caption_asking_to_save:
        # Save as style reference
        import time
        ts = int(time.time() * 1000)
        filename = os.path.join(STYLES_DIR, f"{chat_id}_{ts}.jpg")
        image.save(filename, format='JPEG')
        
        count = len(get_style_images(chat_id))
        
        if is_caption_asking_to_save and not chat_is_setting_style[chat_id]:
           await update.message.reply_text(f"Didascalia riconosciuta! Immagine di referenza aggiunta rapidamente. Totale referenze: {count}")
        else:
           await update.message.reply_text(f"Immagine di referenza salvata con successo. Ne hai {count} salvate permanenti.")
    else:
        # Treat as subject, apply style
        style_files = get_style_images(chat_id)
        if not style_files:
            await update.message.reply_text(
                "Non hai impostato alcuna immagine di referenza per lo stile. "
                "Per favore, usa /set_style e inviami alcune immagini da usare come moodboard, "
                "poi chiudi con /done_style prima di inviarmi la foto che vuoi modificare."
            )
            return
        
        await update.message.reply_text("Ricevuto il soggetto. Sto generando la nuova immagine con l'API Gemini. Attendi per favore...")
        
        try:
            # Prepare Gemini request
            prompt = (
                "Maintain the main subject, composition, and content of the first image perfectly intact. "
                "Apply the exact aesthetic, mood, lighting, color grading, and style of the supplementary reference images to the main subject."
            )
            
            # The order usually dictates how Gemini interprets them depending on the prompt logic. 
            # We explicitly tell it: first image = subject, rest = reference.
            loaded_styles = [Image.open(f) for f in style_files]
            
            # Gemini has a 14 reference images hardcoded limit in Python API arrays. Let's cap max styles injected here to 14.
            # Otherwise we'll hit another error. The prompt states "Up to 14 reference images" in the documentation.
            if len(loaded_styles) > 10: # leave room for subject (1) + breathing room
               loaded_styles = loaded_styles[:10]
               await update.message.reply_text("Hai più di 10 referenze salvate. Uso le prime 10 per non sovraccaricare il modello Gemini, tranquillo il mood è preservato.")

            contents = [prompt, image] + loaded_styles
            
            # Call Gemini
            response = gemini_client.models.generate_content(
                model="gemini-3-pro-image-preview",
                contents=contents,
                config=types.GenerateContentConfig(
                    response_modalities=["IMAGE"], # we only want image back
                    image_config=types.ImageConfig(
                        # aspect_ratio="1:1",  # let it infer from subject if possible, or omit
                        image_size="1K" # 1K resolution to avoid taking too much time
                    )
                )
            )
            
            generated_image_bytes = None
            for part in response.parts:
                if hasattr(part, 'as_image') and (img := part.as_image()):
                    # Save the image to bytes
                    img_byte_arr = io.BytesIO()
                    img.save(img_byte_arr, format='PNG')
                    generated_image_bytes = img_byte_arr.getvalue()
                    break
            
            if generated_image_bytes:
                await update.message.reply_photo(photo=generated_image_bytes, caption="Ecco la tua immagine trasformata!")
            else:
                await update.message.reply_text("Generazione fallita: Non è stata restituita alcuna immagine.")
                
        except Exception as e:
            error_str = str(e)
            logging.error(f"Error calling Gemini: {error_str}")
            
            if "429" in error_str or "RESOURCE_EXHAUSTED" in error_str:
                await update.message.reply_text(
                    "❌ Errore 429: Hai esaurito la quota limite gratuita del modello Gemini 3 Pro (Resource Exhausted).\n\n"
                    "Cosa significa: Google ha limitato quante immagini puoi fare al minuto/giornalmente con questo modello gratuito.\n"
                    "Soluzioni: \n"
                    "1. Attendere circa 1 o 2 minuti e riprovare (i limiti di solito sono calcolati sui minuti/ore).\n"
                    "2. Se continua, hai superato il massimale di oggi."
                )
            else:
                await update.message.reply_text(f"Si è verificato un errore durante la generazione dell'immagine: {e}")

async def post_init(application) -> None:
    await application.bot.set_my_commands([
        BotCommand("start", "Avvia il bot e mostra le istruzioni"),
        BotCommand("set_style", "Imposta le immagini di referenza per il mood"),
        BotCommand("done_style", "Termina l'inserimento delle referenze"),
        BotCommand("clear_style", "Cancella le immagini di referenza salvate"),
        BotCommand("status", "Controlla quante referenze hai salvato"),
    ])

def main() -> None:
    """Start the bot."""
    # Create the Application and pass it your bot's token.
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).post_init(post_init).build()

    # on different commands - answer in Telegram
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("set_style", set_style))
    app.add_handler(CommandHandler("done_style", done_style))
    app.add_handler(CommandHandler("clear_style", clear_style))
    app.add_handler(CommandHandler("status", status))

    # on non command i.e message - echo the message on Telegram
    app.add_handler(MessageHandler(filters.PHOTO, handle_image))

    # Run the bot until the user presses Ctrl-C
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
