# Installation

## Requirements

- A computer running Windows, macOS, or Linux
- Python 3.11 or newer — download from [python.org](https://www.python.org/downloads/)

## Installation Steps

**1. Download FarmerPro**

Download the application archive and extract it to any folder.

**2. Open a terminal in the application folder**

On Windows: hold Shift and right-click inside the folder → "Open PowerShell window here".

**3. Create a virtual environment**

This is an isolated Python environment specifically for FarmerPro. It won't interfere with other programs.

```
python -m venv .venv
```

Then activate it:

- Windows: `.venv\Scripts\activate`
- macOS / Linux: `source .venv/bin/activate`

Once activated, you will see `(.venv)` at the beginning of the command line.

**4. Install dependencies**

```
pip install -r requirements.txt
```

Wait for everything to download — this takes 1–3 minutes.

**5. Launch the application**

```
python -m app.main
```

The FarmerPro window will open.

{% hint style="warning" %}
On Windows you may see a message about a missing Microsoft Visual C++ runtime. Download it for free from the Microsoft website and repeat the installation.
{% endhint %}

{% hint style="info" %}
Every time you launch the app, you need to activate the virtual environment first (step 3) before running the application (step 5).
{% endhint %}
