# City Fog Map

City Fog Map is a [Telegram Mini App](https://core.telegram.org/bots/webapps) that allows users to "unfog" a map by exploring their real-world surroundings, inspired by the "fog of war" mechanic in strategy games. It's built with FastAPI on the backend and a modular, vanilla JavaScript frontend using MapLibre GL.

This project has been fully refactored for clarity, maintainability, and is documented throughout the source code.

## Features

*   **Telegram Mini App Integration:** Authenticates users securely via their Telegram client.
*   **Interactive Map:** Uses the open-source [MapLibre GL](https://maplibre.org/) library to display a fast and interactive map.
*   **"Fog of War" Effect:** Unexplored areas are covered in a dynamic, procedurally generated "fog" that users can clear by visiting locations.
*   **Geospatial Indexing:** Uses Uber's [H3](https://h3geo.org/) library for efficient storage and retrieval of explored hexagonal areas.
*   **Multiple Debug Modes:** Includes several debug modes for easy development and testing without needing to run inside the Telegram client.

## Architecture

The application follows a classic client-server model:

1.  **Backend:** A Python backend powered by the [FastAPI](https://fastapi.tiangolo.com/) framework. It handles user authentication, API requests, and database interactions.
2.  **Frontend:** A vanilla JavaScript single-page application that runs as a Telegram Mini App. It uses MapLibre GL for rendering the map and interacts with the backend via a REST API.
3.  **Database:** A simple SQLite database, making the application self-contained and easy to set up.

For a more detailed explanation of the system's components, modules, and data flow, please see the [**ARCHITECTURE.md**](./ARCHITECTURE.md) file.

## Project Structure

The repository is organized into the following main directories:

```
.
├── app/
│   ├── __init__.py
│   ├── auth.py         # User authentication logic
│   ├── db.py           # Database interaction layer
│   ├── main.py         # FastAPI application, endpoints
│   └── utils.py        # Shared utility functions
├── webapp/
│   ├── js/             # Modular JavaScript source code
│   │   ├── api.js
│   │   ├── config.js
│   │   ├── fog.js
│   │   ├── main.js
│   │   └── map.js
│   ├── debug-auth.html # Debug page for session-based auth
│   ├── index.html      # Main HTML file for the Mini App
│   └── style.css       # Stylesheets
└── tests/
    └── test_utils.py   # Unit tests for backend utility functions
```

## Getting Started

### Prerequisites

*   Python 3.8+
*   A Telegram Bot Token (get one from [@BotFather](https://t.me/BotFather))
*   A way to expose your local server to the internet (e.g., [ngrok](https://ngrok.com/)) for testing on a real device.

### Installation

1.  **Clone the repository:**
    ```bash
    git clone https://github.com/your-username/city-fog-map.git
    cd city-fog-map
    ```

2.  **Create a virtual environment and install dependencies:**
    ```bash
    python -m venv .venv
    source .venv/bin/activate
    pip install -r requirements.txt
    ```

### Running the Application

1.  **Set the Telegram Bot Token:**
    This is only required for standard or debug-auth modes.
    ```bash
    export TELEGRAM_BOT_TOKEN="YOUR_TELEGRAM_BOT_TOKEN"
    ```

2.  **Run the backend server:**
    The `--reload` flag is useful for development, as it automatically restarts the server when code changes.
    ```bash
    uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
    ```

3.  **Expose your local server (for on-device testing):**
    ```bash
    ngrok http 8000
    ```
    Ngrok will give you a public HTTPS URL (e.g., `https://<random>.ngrok-free.app`).

4.  **Configure your Telegram Bot:**
    *   Talk to [@BotFather](https://t.me/BotFather) on Telegram.
    *   Use the `/setdomain` command to link your bot to the ngrok URL.
    *   Create a menu button for your bot with the URL pointing to your web app (e.g., `https://<random>.ngrok-free.app/webapp/`).

## Development and Debugging

To make development easier, you can run the application in one of two debug modes by setting environment variables before starting the server.

### No-Auth Mode

This is the easiest mode for frontend development. It bypasses Telegram authentication entirely and uses a fixed, local user. This allows you to open the web app directly in your desktop browser.

```bash
export NO_AUTH_MODE=1
export TELEGRAM_BOT_TOKEN="dummy" # Can be anything
uvicorn app.main:app --reload
```
You can then access the app at `http://localhost:8000/webapp/`.

### Debug-Auth Mode

This mode enables a debug authentication flow that uses a session cookie instead of the `X-Telegram-Init` header. This is useful for debugging the authentication process itself or testing API endpoints with tools like `curl` or Postman.

1.  **Start the server in debug-auth mode:**
    ```bash
    export DEBUG_AUTH_MODE=1
    export TELEGRAM_BOT_TOKEN="YOUR_REAL_TOKEN"
    uvicorn app.main:app --reload
    ```
2.  **Open the debug auth page:** Navigate to `http://localhost:8000/` (it will redirect to `/webapp/debug-auth.html`).
3.  **Authenticate:** Paste a valid `initData` string from a real Telegram client into the text area and submit. This will set an authentication cookie in your browser.
4.  **Use the app:** You can now navigate to `http://localhost:8000/webapp/` and you will be authenticated as the user from the `initData`.

When either of these modes is enabled, a debug panel will be visible in the frontend, allowing you to change the exploration radius, delete hexagons, and clear the database.

## API Endpoints

The backend exposes a REST API for the frontend. For a full, interactive list of endpoints and their schemas, run the application and visit its OpenAPI documentation at `http://localhost:8000/docs`.
