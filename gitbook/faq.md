# FAQ

## The app won't start — what should I do?

Most likely Python is not installed or not added to PATH. Download Python 3.11+ from [python.org](https://www.python.org/downloads/) and check the **"Add Python to PATH"** box during installation. Then re-run `pip install -r requirements.txt`.

On Windows you may also need an additional package — Microsoft Visual C++ Redistributable. Download it for free from the Microsoft website.

---

## Why do all results show ERROR?

This is almost always a proxy issue:
- Proxies not loaded — make sure the file is selected
- Proxies not working — check them first in Proxy Checker
- Datacenter proxies — Twitter and Discord require residential [proxies](https://dashboard.travchisproxies.com/billing/aff.php?aff=1488)

---

## Can I run the app without proxies?

No. Without [proxies](https://dashboard.travchisproxies.com/billing/aff.php?aff=1488) services will block your IP after just a few requests. Proxies are required for all modules.

---

## How many proxies do I need?

It depends on the volume of work. A 1-to-1 ratio is recommended. FarmerPro automatically distributes the load across them in rotation.

---

## All proxies show `low` in Proxy Checker — is that normal?

If you are using datacenter proxies (AWS, Hetzner, DigitalOcean) — yes, they almost always show `low`. For a high quality score you need residential [proxies](https://dashboard.travchisproxies.com/billing/aff.php?aff=1488) — proxies from real home IP addresses.

---

## How do I stop a module while it's running?

Click the **Stop** button. The module will finish its current task and then stop. Results already received will be saved in the table.

---

## The app is frozen — what should I do?

Do not force-close the window — this can interrupt transactions in Collector. Wait for the current task to finish, then click Stop.

If the app is truly frozen and unresponsive — close it via Task Manager. Results from the incomplete session may be lost.

---

## Collector is taking very long — is that normal?

Yes. Collector processes wallets one by one and pauses between them (1–3 minutes by default). This is intentional — real transactions take time, and the delays reduce the risk of being blocked. For 100 wallets this can take several hours.

---

## Does FarmerPro store my keys or tokens?

No. FarmerPro does not send your data anywhere. Private keys and tokens are used only locally on your computer to execute requests. They are automatically masked in logs (`***`).
