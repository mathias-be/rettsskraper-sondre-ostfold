import requests
import json
import os
import smtplib
from email.message import EmailMessage
from datetime import datetime, timedelta
from pathlib import Path

API_URL = "https://www.domstol.no/api/episerver/v3/beramming"
CACHE_FILE = Path("cache.json")

DOMSTOL_NAVN = "Søndre Østfold tingrett"

EMAIL_TO = "mathias.eidissen@amedia.no"
EMAIL_FROM = os.environ.get("EMAIL_FROM")
SMTP_USERNAME = os.environ.get("SMTP_USERNAME")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD")

VARSEL_DAGER = 14


def les_cache():
    if CACHE_FILE.exists():
        with open(CACHE_FILE, "r") as f:
            return json.load(f)
    return {}


def skriv_cache(cache):
    with open(CACHE_FILE, "w") as f:
        json.dump(cache, f)


def hent_saker():
    today = datetime.now()

    params = {
        "fraDato": (today - timedelta(days=14)).strftime("%Y-%m-%d"),
        "tilDato": (today + timedelta(days=365)).strftime("%Y-%m-%d"),
        "sortTerm": "rettsmoete",
        "pageSize": "1000"
    }

    r = requests.get(API_URL, params=params)
    r.raise_for_status()

    return r.json()["hits"]


def send_email(sak):

    msg = EmailMessage()

    msg["Subject"] = f"Ny rettssak: {sak['saksnr']}"
    msg["From"] = EMAIL_FROM
    msg["To"] = EMAIL_TO

    msg.set_content(f"""
Ny sak funnet

Domstol: {sak['domstol']}
Saksnummer: {sak['saksnr']}
Dato: {sak['dato']}

Saken gjelder:
{sak['saken_gjelder']}

Parter:
{sak['parter']}
""")

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(SMTP_USERNAME, SMTP_PASSWORD)
        smtp.send_message(msg)


def main():

    cache = les_cache()

    saker = hent_saker()

    idag = datetime.now()
    grense = idag + timedelta(days=VARSEL_DAGER)

    for sak in saker:

        domstol = sak.get("domstol")

        if domstol != DOMSTOL_NAVN:
            continue

        saksnr = sak.get("saksnummer")
        sak_id = sak.get("sakId")

        key = f"{sak_id}-{saksnr}"

        if key in cache:
            continue

        dato = sak.get("startdato")

        if not dato:
            continue

        sak_dato = datetime.strptime(dato[:10], "%Y-%m-%d")

        if not (idag <= sak_dato <= grense):
            continue

        sakinfo = {
            "domstol": domstol,
            "saksnr": saksnr,
            "dato": dato,
            "saken_gjelder": sak.get("sakenGjelder", ""),
            "parter": sak.get("parter", "")
        }

        send_email(sakinfo)

        cache[key] = True

    skriv_cache(cache)


if __name__ == "__main__":
    main()
