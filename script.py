import os
import re
import json
import smtplib
import hashlib
import requests
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Optional
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv()

APPLICANT = {
    "salutation":    os.getenv("APPLICANT_SALUTATION",    ""),
    "first_name":    os.getenv("APPLICANT_FIRST_NAME",    ""),
    "last_name":     os.getenv("APPLICANT_LAST_NAME",     ""),
    "email":         os.getenv("APPLICANT_EMAIL",         ""),
    "phone":         os.getenv("APPLICANT_PHONE",         ""),
    "birth_date":    os.getenv("APPLICANT_BIRTH_DATE",    ""),
    "nationality":   os.getenv("APPLICANT_NATIONALITY",   ""),
    "semester":      os.getenv("APPLICANT_SEMESTER",      ""),
    "year":          os.getenv("APPLICANT_YEAR",          ""),
    "num_semesters": os.getenv("APPLICANT_NUM_SEMESTERS", ""),
    "university":    os.getenv("APPLICANT_UNIVERSITY",    ""),
    "wheelchair":    False,
    "message":       os.getenv("APPLICANT_MESSAGE",       ""),
}

LISTINGS_URL = "https://www.stwdo.de/en/living-houses-application/current-housing-offers"
WH_BASE      = "https://app.wohnungshelden.de"
STATE_FILE   = os.path.join(os.path.dirname(__file__), "stwdo_state.json")

SMTP_HOST     = os.getenv("SMTP_HOST",     "smtp.gmail.com")
SMTP_PORT     = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER     = os.getenv("SMTP_USER",     "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
ALERT_TO      = os.getenv("ALERT_TO",      "")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    )
}


def fetch_listings() -> list:
    resp = requests.get(LISTINGS_URL, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    listings = []
    for card in soup.select("div.teaser.js-link-area[data-href]"):
        path = card["data-href"]
        m = re.search(r"/r/f/(.+)$", path)
        if not m:
            continue

        raw_id     = m.group(1)
        listing_id = requests.utils.quote(raw_id, safe="")

        title = card.select_one("h5")
        city  = card.select_one("span.subheader-5")
        facts = card.select("span.headline-4")

        listings.append({
            "id":         hashlib.md5(path.encode()).hexdigest()[:10],
            "title":      title.get_text(strip=True) if title else "Unknown",
            "city":       city.get_text(strip=True).replace("\xa0", " ").strip() if city else "Unknown",
            "rent":       facts[0].get_text(strip=True) if len(facts) > 0 else "?",
            "size":       facts[1].get_text(strip=True) if len(facts) > 1 else "?",
            "available":  facts[2].get_text(strip=True) if len(facts) > 2 else "?",
            "detail_url": f"https://www.stwdo.de{path}",
            "listing_id": listing_id,
        })
    return listings


def fetch_company_id(detail_url: str) -> Optional[str]:
    resp = requests.get(detail_url, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    iframe = soup.find("iframe", id="bewerben")
    if not iframe:
        return None
    m = re.search(r"[?&]c=([0-9a-f-]{36})", iframe.get("src", ""))
    return m.group(1) if m else None


def apply_to_listing(listing: dict) -> bool:
    company_id = fetch_company_id(listing["detail_url"])
    if not company_id:
        print(f"  [⚠️ ] Could not find company ID for '{listing['title']}'.")
        return False

    url = (
        f"{WH_BASE}/api/applicationFormEndpoint/3.0/form/create-application"
        f"/{company_id}/{listing['listing_id']}"
    )

    payload = {
        "publicApplicationCreationTO": {
            "applicantMessage":             APPLICANT["message"],
            "email":                        APPLICANT["email"],
            "firstName":                    APPLICANT["first_name"],
            "lastName":                     APPLICANT["last_name"],
            "phoneNumber":                  None,
            "salutation":                   APPLICANT["salutation"],
            "street":                       None,
            "houseNumber":                  None,
            "zipCode":                      None,
            "city":                         None,
            "additionalAddressInformation": None,
        },
        "saveFormDataTO": {
            "formData": {
                "$$_mobile_number_$$":                            APPLICANT["phone"],
                "$$_date_of_birth_$$":                            APPLICANT["birth_date"],
                "nationality":                                    APPLICANT["nationality"],
                "startOfSemester":                                APPLICANT["semester"],
                "year":                                           APPLICANT["year"],
                "numberOfSemester":                               APPLICANT["num_semesters"],
                "stwdo_university":                               APPLICANT["university"],
                "stwdo_angewiesen_auf_rollstuhlgerechte_wohnung": APPLICANT["wheelchair"],
                "stwdo_immatrikulation":                          True,
                "stwdo_datenschutzhinweis_bestaetigt":            True,
            },
            "files": [],
        },
        "recaptchaToken": None,
    }

    resp = requests.post(
        url,
        json=payload,
        headers={
            **HEADERS,
            "Content-Type": "application/json",
            "Origin":        WH_BASE,
            "Referer": (
                f"{WH_BASE}/public/listings/{listing['listing_id']}"
                f"/application?c={company_id}"
            ),
        },
        timeout=20,
    )

    body = resp.text.strip()

    if resp.status_code == 200 and body == "true":
        print(f"  [✅] Applied successfully!")
        return True
    elif resp.status_code == 409:
        print(f"  [⚠️ ] Already applied — skipping.")
        return True
    else:
        print(f"  [❌] Failed (HTTP {resp.status_code}): {body[:200]}")
        return False


def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {"applied_ids": [], "last_check": None}


def save_state(state: dict):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def _fmt(listing: dict) -> str:
    return (
        f"  {listing['title']} — {listing['city']}\n"
        f"  Rent: {listing['rent']}  |  Size: {listing['size']}  "
        f"|  Available: {listing['available']}\n"
        f"  {listing['detail_url']}"
    )


def _send_email(subject: str, lines: list):
    if not all([SMTP_USER, SMTP_PASSWORD, ALERT_TO]):
        print("  [⚠️ ] Email not configured — skipping.")
        return
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = SMTP_USER
    msg["To"]      = ALERT_TO
    msg.attach(MIMEText("\n".join(lines), "plain"))
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as s:
        s.ehlo()
        s.starttls()
        s.login(SMTP_USER, SMTP_PASSWORD)
        s.sendmail(SMTP_USER, ALERT_TO, msg.as_string())
    print(f"  [✉️ ] Email sent → {ALERT_TO}")


def send_alert_email(new_listings: list):
    lines = [
        f"Detected at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "Auto-apply is starting — you can also apply manually:",
        "",
        "─" * 50,
    ]
    for listing in new_listings:
        lines += [_fmt(listing), ""]
    lines += ["─" * 50, f"Listings page: {LISTINGS_URL}"]
    _send_email(
        f"🏠 {len(new_listings)} new listing(s) on stwdo.de — applying now!",
        lines,
    )


def send_summary_email(applied: list, failed: list):
    lines = [f"Auto-apply finished: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", ""]
    if applied:
        lines += [f"✅ Successfully applied to {len(applied)} listing(s):", ""]
        for listing in applied:
            lines += [_fmt(listing), ""]
    if failed:
        lines += [f"❌ Auto-apply FAILED for {len(failed)} — apply manually:", ""]
        for listing in failed:
            lines += [_fmt(listing), ""]
    lines += ["─" * 50, LISTINGS_URL]
    subject = f"🤖 stwdo: {len(applied)} applied" + (
        f", {len(failed)} failed ⚠️" if failed else " ✅"
    )
    _send_email(subject, lines)


def main():
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now}] Checking {LISTINGS_URL} …")

    try:
        listings = fetch_listings()
    except Exception as e:
        print(f"[ERROR] Could not fetch listings: {e}")
        return

    print(f"[ℹ️ ] {len(listings)} listing(s) found on page.")

    if not listings:
        print("[⏳] No listings right now.")
        return

    state       = load_state()
    applied_ids = set(state.get("applied_ids", []))

    new_listings = [l for l in listings if l["id"] not in applied_ids]
    print(f"[ℹ️ ] {len(new_listings)} new (not yet applied to).")

    if not new_listings:
        print("[✅] Nothing new to apply to.")
        return

    try:
        send_alert_email(new_listings)
    except Exception as e:
        print(f"[ERROR] Alert email failed: {e}")

    applied, failed = [], []
    for listing in new_listings:
        print(
            f"\n[🆕] {listing['title']} | {listing['city']} | "
            f"{listing['rent']} | from {listing['available']}"
        )
        if apply_to_listing(listing):
            applied.append(listing)
            applied_ids.add(listing["id"])
        else:
            failed.append(listing)

    try:
        send_summary_email(applied, failed)
    except Exception as e:
        print(f"[ERROR] Summary email failed: {e}")

    state["applied_ids"] = list(applied_ids)
    state["last_check"]  = now
    save_state(state)
    print("\n[💾] State saved.")
    print(f"[📊] Applied: {len(applied)}  |  Failed: {len(failed)}")


if __name__ == "__main__":
    main()