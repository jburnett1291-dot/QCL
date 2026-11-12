# QCL — QCL League Central

QCL combines a Streamlit league hub with a unified Discord bot for league operations. The web app includes league, player and team views, registration and commissioner workflows, draft tools, and QTCG features. The bot provides league operations in Discord; see [discord_bot/README.md](discord_bot/README.md) for its command set and bot-specific setup.

## Repository map

- `app.py` — Streamlit application.
- `discord_bot/` — unified QCL and Federal Reserve Discord bot, launcher, and bot documentation.
- `qcl_film.py` — film-related Python utility.
- Root CSV/JSON files and folders such as `cards/`, `logos/`, `templates/`, and `backgrounds/` — league data and app assets. Review their consumers before changing or removing them.

## Run the web app locally

Use Python 3.11, matching the repository devcontainer. From the repository root:

```sh
python -m venv .venv
# Linux/macOS
source .venv/bin/activate
# Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
streamlit run app.py
```

The devcontainer installs the operating-system packages listed in `packages.txt` (currently `tesseract-ocr`). Install those with your platform's package manager when a feature or environment requires them.

### App configuration

Some authentication, registration, commissioner, and data-integration features require settings supplied through Streamlit Secrets or the deployment's secret manager. The app references these setting names; configure only those needed for the features you use:

- `DISCORD_CLIENT_ID`, `DISCORD_CLIENT_SECRET`, `DISCORD_REDIRECT_URI`, `QCL_SIGNING_SECRET`
- `QCL_REGISTRATION_WEBHOOK_URL`, `QCL_SAVE_API_URL`, `QCL_REGISTRY_URL`, `ADMIN_PIN`
- `QCL_ACTIVE_SEASON`, `QCL_HISTORY_CSV_PATH`

Never commit secret values. Keep local Streamlit secrets in `.streamlit/secrets.toml`; the root `.gitignore` excludes that file.

## Run the Discord bot

The bot has its own dependencies and environment template. From the repository root:

```sh
cd discord_bot
python -m venv .venv
# Activate the environment using the command for your platform, then:
python -m pip install -r requirements.txt
cp .env.template .env
python run_bot.py
```

Fill `.env` locally with the values required by the bot. Do not commit it. For full bot setup and operations, see [discord_bot/README.md](discord_bot/README.md).

## Checks

Run the repository's current syntax and unit checks from the root:

```sh
python -m compileall -q app.py qcl_film.py discord_bot
python -m unittest -v test_qcl_navigation.py
```

The unit test file uses Python's standard library. These checks validate Python syntax and the current navigation/session contracts; they do not start the Streamlit server, connect to Discord or Sheets, or deploy the app. GitHub Actions runs the same checks on pushes to `main` and pull requests.

## Mobile PWA

See [PWA.md](PWA.md) for the installable mobile companion, offline behavior, and publishing steps.

## Data and credentials

Tracked CSV/JSON files and image folders may be used by the app or bot; they are intentionally not ignored wholesale. Before changing them, identify the reader/writer and preserve a recoverable copy. Keep `.env`, `.streamlit/secrets.toml`, credentials, and local runtime files out of commits.
