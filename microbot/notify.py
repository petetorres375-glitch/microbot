"""
notify.py
---------
Where the bot "asks you." By default it just prints to the console, which is
fine when you run the engine yourself. To get pinged when the bot runs
unattended on a schedule, fill in one of the stubs below (email / Telegram /
Pushover). Keep it simple — a one-line message with a link/cue to go approve.
"""
from __future__ import annotations


def notify(message: str):
    print(f"\n🔔 {message}\n")

    # --- Email (uncomment + configure) ---
    # import smtplib, ssl, os
    # from email.message import EmailMessage
    # msg = EmailMessage(); msg["Subject"] = "microbot: trades awaiting approval"
    # msg["From"] = os.environ["NOTIFY_FROM"]; msg["To"] = os.environ["NOTIFY_TO"]
    # msg.set_content(message)
    # with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ssl.create_default_context()) as s:
    #     s.login(os.environ["NOTIFY_FROM"], os.environ["NOTIFY_APP_PASSWORD"])
    #     s.send_message(msg)

    # --- Telegram (uncomment + configure) ---
    # import os, urllib.request, urllib.parse
    # token, chat = os.environ["TG_TOKEN"], os.environ["TG_CHAT_ID"]
    # data = urllib.parse.urlencode({"chat_id": chat, "text": message}).encode()
    # urllib.request.urlopen(f"https://api.telegram.org/bot{token}/sendMessage", data)
