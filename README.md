# GPT Auto GUI

GPT Auto GUI is a compact Windows desktop tool for polishing and translating copied text with OpenAI-compatible GPT models. It stays on top of other windows, watches the clipboard after a hotkey trigger, sends the selected text to the configured model, then pastes the result back into the active application.

## Features

- Polish text while preserving the original language.
- Polish Chinese or other source text and translate it into natural American English.
- Trigger processing by pressing `Ctrl` twice after enabling a mode.
- Toggle modes globally with `Alt+Z` for Translate and `Alt+X` for Polish.
- Run from a tiny always-on-top PyQt6 window or the Windows system tray.
- Configure the API key, base URL, and model through environment variables or `gpt_config.ini`.

## Requirements

- Windows 10 or later
- Python 3.10+
- An OpenAI API key or an OpenAI-compatible API endpoint

## Installation

```powershell
git clone https://github.com/tachugml0421-cell/gpt-auto-gui.git
cd gpt-auto-gui
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## Configuration

The safest option is to set environment variables:

```powershell
$env:OPENAI_API_KEY="your_openai_api_key_here"
$env:OPENAI_BASE_URL="https://api.openai.com/v1"
$env:OPENAI_MODEL="gpt-5-mini"
```

Alternatively, copy the example config:

```powershell
Copy-Item gpt_config.example.ini gpt_config.ini
```

Then edit `gpt_config.ini` and fill in your own API key. Do not commit `gpt_config.ini`; it is ignored by Git because it contains secrets.

## Usage

```powershell
python .\gpt_auto_gui.pyw
```

1. Click `Translate` or press `Alt+Z` to enable translation mode.
2. Click `Polish` or press `Alt+X` to enable polishing mode.
3. Copy text in any application.
4. Press `Ctrl` twice quickly.
5. The processed result is copied to the clipboard and pasted automatically.

## Packaging A Windows Executable

You can build a standalone executable with PyInstaller:

```powershell
pip install pyinstaller
pyinstaller --noconsole --onefile --name GPTAuto .\gpt_auto_gui.pyw
```

The executable will be created under `dist\`.

## Security Notes

- Never commit a real API key.
- Rotate your API key immediately if it has ever been pasted into a public issue, commit, chat, screenshot, or shared config file.
- This tool sends copied text to the configured API endpoint. Avoid using it on confidential text unless your endpoint and data policy allow it.

## License

This project is released under the MIT License.
