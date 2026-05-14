import os
import sys

import requests
from dotenv import load_dotenv
import importlib
import json
import asyncio
import aio_pika

project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

if project_root not in sys.path:
    sys.path.append(project_root)

lab1_worker = importlib.import_module("lab1-worker.worker")
slate_to_html, send_email = lab1_worker.slate_to_html, lab1_worker.send_email

load_dotenv()
email = os.getenv("MZINGA_EMAIL", "a@a.com")
password = os.getenv("MZINGA_PASSWORD", "aaa")
url = os.getenv("MZINGA_URL", "http://localhost:3000")

rabbitmq_url = os.getenv("RABBITMQ_URL")
routing_key = os.getenv("ROUTING_KEY")
exchange_name = os.getenv("EXCHANGE_NAME")
queue_name = os.getenv("QUEUE_NAME")

def login():
    post_url = url + "/api/users/login"
    data = {"email": email, "password": password}
    post_response = requests.post(url=post_url, json=data)
    return post_response.json()["token"]

def authorization_header(token):
    return {"Authorization": "Bearer " + token}

def request_with_auth(token, method, request_url, **kwargs):
    response = requests.request(
        method,
        url=request_url,
        headers=authorization_header(token),
        **kwargs,
    )

    if response.status_code == 401:
        token = login()
        response = requests.request(
            method,
            url=request_url,
            headers=authorization_header(token),
            **kwargs,
        )

    response.raise_for_status()
    return response, token

def fetch_doc(token, doc_id):
    get_url = url + "/api/communications/" + doc_id

    get_response, token = request_with_auth(
        token,
        "GET",
        get_url,
        params={"depth": 1},
    )

    return get_response.json(), token

def update_status(token, doc_id, status):
    patch_url = url + "/api/communications/" + doc_id

    patch_response, token = request_with_auth(
        token,
        "PATCH",
        patch_url,
        json=status,
    )

    return token

def extract_emails(relationship_list):
    emails = []

    for relation in relationship_list or []:
        value = relation.get("value") or {}

        if isinstance(value, dict) and value.get("email"):
            emails.append(value["email"])

    return emails

def process(token, doc):
    doc_id = doc["id"]

    if doc.get("status") in ["sent", "processing"]:
        print("Skipping communication " + doc_id + " because status is already " + doc.get("status"))
        return token

    print("Processing communication " + doc_id)

    status = {"status": "processing"}
    token = update_status(token, doc_id, status)

    try:
        to_emails = extract_emails(doc.get("tos"))
        cc_emails = extract_emails(doc.get("ccs"))
        bcc_emails = extract_emails(doc.get("bccs"))

        if not to_emails:
            raise ValueError("No valid 'to' email addresses found")

        print(to_emails, cc_emails, bcc_emails)

        html_content = slate_to_html(doc.get("body"))
        send_email(to_emails, cc_emails, bcc_emails, doc, html_content)

        status = {"status": "sent"}
        token = update_status(token, doc_id, status)

        print("Sending email")

    except Exception as e:
        print("Exception " + str(e))

        status = {"status": "failed", "error": str(e)}

        try:
            token = update_status(token, doc_id, status)
        except Exception as e:
            print("Critical: could not update status to failed." + str(e))

    return token

async def run_worker():
    token = login()

    connection = await aio_pika.connect_robust(rabbitmq_url)

    async with connection:
        channel = await connection.channel()
        await channel.set_qos(prefetch_count=1)

        exchange = await channel.declare_exchange(
            exchange_name,
            aio_pika.ExchangeType.TOPIC,
            durable=True,
            internal=True,
            auto_delete=False,
        )

        queue = await channel.declare_queue(queue_name, durable=True)
        await queue.bind(exchange, routing_key=routing_key)

        print("Worker connected to RabbitMQ")
        print("Waiting for messages")

        async with queue.iterator() as messages:
            async for message in messages:
                async with message.process(requeue=True):
                    body = json.loads(message.body.decode())

                    data = body.get("data", {})
                    operation = data.get("operation")
                    event_doc = data.get("doc") or {}
                    doc_id = event_doc.get("id")

                    if not doc_id:
                        print("Message missing data.doc.id")
                        continue

                    if operation != "create":
                        print("Skipping operation " + str(operation))
                        continue

                    doc, token = fetch_doc(token, doc_id)
                    token = process(token, doc)

if __name__ == "__main__":
    asyncio.run(run_worker())