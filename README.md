# Telegram Bot per Aesthetic Moods

Questo bot Telegram utilizza l'API di Google Gemini (`gemini-3-pro-image-preview`) per trasferire lo stile di alcune immagini di riferimento (referenze) ad una foto indicata dall'utente, mantenendo il soggetto originale intatto.

## Requisiti

- Python 3.9+
- Un Token per un Bot Telegram (ottenibile tramite [BotFather](https://t.me/botfather) su Telegram)
- Una API Key di Google Gemini (ottenibile su [Google AI Studio](https://aistudio.google.com/))

## Installazione

1. Assicurati di essere nella cartella corretta:
   ```bash
   cd "/Users/giovannimenchinella/Telegram Bot per aestetich"
   ```

2. Crea un ambiente virtuale (opzionale ma consigliato):
   ```bash
   python -m venv .venv
   source .venv/bin/activate
   ```

3. Installa le dipendenze:
   ```bash
   pip install -r requirements.txt
   ```

4. Configura le chiavi API:
   Apri il file `.env` e inserisci le tue chiavi:
   ```
   TELEGRAM_BOT_TOKEN="il_tuo_token_telegram_qui"
   GEMINI_API_KEY="la_tua_api_key_gemini_qui"
   ```

## Avvio del Bot

Per avviare il bot, esegui:
```bash
python main.py
```

## Come usarlo

Cerca il tuo bot su Telegram e avvia la chat:
1. Digita `/start` per vedere il messaggio di benvenuto.
2. Digita `/set_style` per entrare in modalità "moodboard".
3. Invia dal cellulare/PC fino a 10 immagini che definiscono il tuo aesthetic desiderato (es. le foto vintage o scure che mi avevi mostrato).
4. Digita `/done_style` per terminare l'inserimento.
5. Infine, invia la foto **soggetto** che desideri modificare.
6. Il bot elaborerà l'immagine con Gemini e ti risponderà con i risultati!
