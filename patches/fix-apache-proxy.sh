#!/usr/bin/env bash
set -euo pipefail

CONF="/etc/apache2/sites-available/wongram.shop.conf"

python3 - <<'PY'
from pathlib import Path
import re

path = Path("/etc/apache2/sites-available/wongram.shop.conf")
text = path.read_text()

# Remove any ProxyPass lines that point to tokenized webhook
text = text.replace("ProxyPass /order http://127.0.0.1:8000/webhook/warroom-secret-token", "ProxyPass /order http://127.0.0.1:8000/order")
text = text.replace("ProxyPassReverse /order http://127.0.0.1:8000/webhook/warroom-secret-token", "ProxyPassReverse /order http://127.0.0.1:8000/order")

vh_re = re.compile(r"(<VirtualHost[^>]*>.*?</VirtualHost>)", re.S)
parts = vh_re.split(text)

order_lines = [
    "    ProxyPass /order http://127.0.0.1:8000/order",
    "    ProxyPassReverse /order http://127.0.0.1:8000/order",
    "    ProxyPass /webhook http://127.0.0.1:8000/webhook",
    "    ProxyPassReverse /webhook http://127.0.0.1:8000/webhook",
]

order_re = re.compile(r"ProxyPass(Reverse)?\s+/(order|webhook)\b")


def fix_block(block: str) -> str:
    lines = block.splitlines()

    # Ensure rewrite exception for /order and /webhook on HTTP vhost
    if "RewriteEngine On" in block:
        has_exc = any("order|webhook" in l for l in lines if "RewriteCond %{REQUEST_URI}" in l)
        if not has_exc:
            new_lines = []
            for l in lines:
                new_lines.append(l)
                if "RewriteCond %{REQUEST_URI} !^/\\.well-known/acme-challenge/" in l:
                    new_lines.append("    RewriteCond %{REQUEST_URI} !^/(order|webhook)(/|$)")
            lines = new_lines

    # Drop any existing /order or /webhook ProxyPass lines to dedupe
    lines = [l for l in lines if not order_re.search(l)]

    # Insert order/webhook proxies near ProxyRequests Off or ProxyPreserveHost On
    inserted = False
    new_lines = []
    for l in lines:
        new_lines.append(l)
        if not inserted and ("ProxyRequests Off" in l or "ProxyPreserveHost On" in l):
            # Insert once, right after the first suitable line
            new_lines.extend(order_lines)
            inserted = True

    if not inserted:
        # Fallback: insert before ErrorLog or closing tag
        for i, l in enumerate(new_lines):
            if "ErrorLog" in l:
                new_lines = new_lines[:i] + order_lines + new_lines[i:]
                inserted = True
                break
    if not inserted:
        for i, l in enumerate(new_lines):
            if "</VirtualHost>" in l:
                new_lines = new_lines[:i] + order_lines + new_lines[i:]
                inserted = True
                break

    return "\n".join(new_lines)

for i, part in enumerate(parts):
    if part.startswith("<VirtualHost"):
        parts[i] = fix_block(part)

new_text = "".join(parts)
path.write_text(new_text)
PY

apache2ctl configtest
systemctl reload apache2

echo "OK"
