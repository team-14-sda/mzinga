import os
import time
import smtplib
import logging
from dotenv import load_dotenv
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pymongo import MongoClient, ReturnDocument
from bson.objectid import ObjectId

load_dotenv()
MONGODB_URI = os.getenv("MONGODB_URI", "mongodb://localhost:27017/mzinga")
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL_SECONDS", "5"))
SMTP_HOST = os.getenv("SMTP_HOST", "localhost")
SMTP_PORT = int(os.getenv("SMTP_PORT", "1025"))

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
client = MongoClient(MONGODB_URI)
db = client.get_database()


def slate_to_html(nodes):
    if not nodes:
        return ""

    html = []
    for node in nodes:
        # Handle leaf nodes (text)
        if "text" in node:
            text = node["text"]
            if node.get("bold"):
                text = f"<b>{text}</b>"
            if node.get("italic"):
                text = f"<i>{text}</i>"
            html.append(text)
            continue

        # Recursive call for element nodes
        children_html = slate_to_html(node.get("children", []))
        node_type = node.get("type")

        if node_type == "h1":
            html.append(f"<h1>{children_html}</h1>")
        elif node_type == "h2":
            html.append(f"<h2>{children_html}</h2>")
        elif node_type == "paragraph":
            html.append(f"<p>{children_html}</p>")
        elif node_type == "ul":
            html.append(f"<ul>{children_html}</ul>")
        elif node_type == "li":
            html.append(f"<li>{children_html}</li>")
        elif node_type == "link":
            url = node.get("url", "#")
            html.append(f'<a href="{url}">{children_html}</a>')
        else:
            html.append(children_html)

    return "".join(html)


def resolve_emails(refs):
    emails = []
    for ref in refs:
        user_id = ref.get("value")
        if user_id:
            user = db.users.find_one({"_id": ObjectId(user_id)}, {"email": 1})
            if user and user.get("email"):
                emails.append(user["email"])
    return emails

def resolve_addrs_and_send_emails(doc, html_body):
    to_addrs = resolve_emails(doc.get("tos", []))
    cc_addrs = resolve_emails(doc.get("ccs", []))
    bcc_addrs = resolve_emails(doc.get("bccs", []))

    if not to_addrs:
        raise ValueError("No recipient email addresses found.")
    send_email(to_addrs, cc_addrs, bcc_addrs, doc, html_body)


def send_email(to_emails, cc_emails, bcc_emails, doc, html_body):
    msg = MIMEMultipart()
    msg["Subject"] = doc.get("subject", "(No Subject)")
    msg["From"] = "noreply@mzinga.com"
    msg["To"] = ", ".join(to_emails)
    if cc_emails:
        msg["Cc"] = ", ".join(cc_emails)

    msg.attach(MIMEText(html_body, "html"))

    # Combine all recipients for the SMTP envelope
    all_recipients = to_emails + cc_emails + bcc_emails

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.sendmail(msg["From"], all_recipients, msg.as_string())


def run_worker():
    logging.info("Worker started. Polling for pending communications...")

    while True:
        # Find one pending and set to processing immediately
        doc = db.communications.find_one_and_update(
            {"status": "pending"},
            {"$set": {"status": "processing"}},
            return_document=ReturnDocument.AFTER
        )

        if not doc:
            time.sleep(POLL_INTERVAL)
            continue

        logging.info(f"Processing document {doc['_id']}")

        try:
            html_content = slate_to_html(doc.get("body", []))

            # Resolve email addresses and send emails
            resolve_addrs_and_send_emails(doc, html_content)

            # Success write-back
            db.communications.update_one(
                {"_id": doc["_id"]},
                {"$set": {"status": "sent", "error": None}}
            )
            logging.info(f"Successfully sent document {doc['_id']}")

        except Exception as e:
            logging.error(f"Failed to process {doc['_id']}: {str(e)}")
            # Error write-back
            db.communications.update_one(
                {"_id": doc["_id"]},
                {"$set": {"status": "failed", "error": str(e)}}
            )


if __name__ == "__main__":
    run_worker()