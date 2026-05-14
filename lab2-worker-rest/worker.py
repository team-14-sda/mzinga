import os
import sys

import requests
from dotenv import load_dotenv
import time
import importlib

project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

if project_root not in sys.path:
    sys.path.append(project_root)

lab1_worker = importlib.import_module("lab1-worker.worker")
slate_to_html, send_email = lab1_worker.slate_to_html, lab1_worker.send_email

load_dotenv()
poll_time = int(os.getenv("POLL_INTERVAL_SECONDS"))
email = os.getenv("MZINGA_EMAIL", "a@a.com")
password = os.getenv("MZINGA_PASSWORD", "aaa")
url = os.getenv("MZINGA_URL", "http://localhost:3000")

def login():
    post_url = url + "/api/users/login"
    data = {"email": email, "password": password}
    post_response = requests.post(url=post_url, json=data)
    return post_response.json()["token"]

def authorization_header(token):
    return {"Authorization": "Bearer " + token}

def run_worker():
    token = login()
    header = authorization_header(token)
    get_url = url + "/api/communications?where[status][equals]=pending&depth=1"
    patch_processing_url = url + "/api/communications/"

    while True:
        get_response = requests.get(url=get_url, headers=header)
        if get_response.status_code == 401:
            token = login()
            header = authorization_header(token)
            continue
        docs = get_response.json()["docs"]
        if not docs:
            time.sleep(poll_time)
        else:
            for doc in docs:
                doc_id = doc["id"]
                status = {"status": "processing"}
                patch_processing_response = requests.patch(url=patch_processing_url+doc_id, json=status, headers=header)
                print("Processing started")
                if patch_processing_response.status_code == 401:
                    token = login()
                    header = authorization_header(token)
                    continue
                to_emails = [to["value"]["email"] for to in doc.get("tos", [])]
                cc_emails = [cc["value"]["email"] for cc in doc.get("ccs", [])]
                bcc_emails = [bcc["value"]["email"] for bcc in doc.get("bccs", [])]
                try:
                    print(to_emails, cc_emails, bcc_emails)
                    html_content = slate_to_html(doc.get("body"))
                    send_email(to_emails, cc_emails, bcc_emails, doc, html_content)
                    status = {"status": "sent"}
                    patch_sent_response = requests.patch(url=patch_processing_url+doc_id, json=status, headers=header)
                    print("Sending email")
                    if patch_sent_response.status_code == 401:
                        token = login()
                        header = authorization_header(token)
                        continue
                except Exception as e:
                    print("Exception " + str(e))
                    status = {"status": "failed", "error": str(e)}
                    try:
                        requests.patch(url=patch_processing_url + doc_id, json=status, headers=header)
                    except Exception as e:
                        print("Critical: could not update status to failed." + str(e))

if __name__ == "__main__":
    run_worker()
