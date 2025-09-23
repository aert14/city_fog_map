# City Fog Map

City Fog Map is a Telegram Mini App that allows users to "unfog" a map by exploring their surroundings. It's built with FastAPI on the backend and a vanilla JavaScript frontend using MapLibre GL.

## Features

*   **Telegram Mini App Integration:** Authenticates users securely via their Telegram client.
*   **Interactive Map:** Uses MapLibre GL to display an interactive map.
*   **"Fog of War" Effect:** Unexplored areas are covered in a "fog of war" that users can clear by visiting locations.
*   **Geospatial Indexing:** Uses H3 for efficient storage and retrieval of explored areas.
*   **Debug Mode:** Includes a debug mode for easy development and testing.

## Architecture

The application consists of two main components:

1.  **Backend:** A Python-based backend powered by the FastAPI framework. It handles user authentication, API requests, and database interactions.
2.  **Frontend:** A vanilla JavaScript single-page application that runs as a Telegram Mini App. It uses MapLibre GL for rendering the map and interacts with the backend via a REST API.

The database is a simple SQLite database, making the application self-contained and easy to set up.

## Getting Started

### Prerequisites

*   Python 3.8+
*   A Telegram Bot Token (get one from [@BotFather](https://t.me/BotFather))
*   A way to expose your local server to the internet (e.g., ngrok, localtunnel)

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

    ```bash
    export TELEGRAM_BOT_TOKEN="YOUR_TELEGRAM_BOT_TOKEN"
    ```

2.  **Run the backend server:**

    ```bash
    uvicorn app.main:app --host 0.0.0.0 --port 8000
    ```

3.  **Expose your local server to the internet:**

    For example, using ngrok:

    ```bash
    ngrok http 8000
    ```

    Ngrok will give you a public HTTPS URL (e.g., `https://<random>.ngrok-free.app`).

4.  **Configure your Telegram Bot:**

    *   Talk to [@BotFather](https://t.me/BotFather) on Telegram.
    *   Use the `/setdomain` command to link your bot to the ngrok URL.
    *   Create a menu button for your bot with the URL pointing to your web app (e.g., `https://<random>.ngrok-free.app/webapp/`).

## API Endpoints

The backend exposes the following API endpoints:

*   `POST /api/v1/visit`: Records a user's visit to a specific location.
    *   **Body:** `{ "lat": float, "lon": float }`
    *   **Response:** `{ "added": int, "circle": object, "stats": object }`
*   `GET /api/v1/circles`: Retrieves the explored areas (as H3 hexagons) for the current user within a given bounding box.
    *   **Query Parameter:** `bbox=minLon,minLat,maxLon,maxLat`
    *   **Response:** `{ "hexagons": [str] }`
*   `POST /api/v1/radius`: Sets the exploration radius for the current user.
    *   **Body:** `{ "radius_m": int }`
    *   **Response:** `{ "updated": int, "h3_resolution": int, "resolution_changed": bool }`
*   `DELETE /api/v1/circle`: Deletes a specific explored hexagon.
    *   **Body:** `{ "geokey": str }`
    *   **Response:** `{ "deleted": int }`

## Development

### Debug Mode

To make development easier, you can run the application in one of two debug modes:

*   **`NO_AUTH_MODE`:** Bypasses Telegram authentication and uses a fixed local user. This is useful for testing the frontend in a regular web browser. To enable it, set the `NO_AUTH_MODE` environment variable:
    ```bash
    export NO_AUTH_MODE=1
    ```
*   **`DEBUG_AUTH_MODE`:** Enables the debug authentication flow, which uses a session cookie instead of the `X-Telegram-Init` header. This is useful for debugging the authentication process itself. To enable it, set the `DEBUG_AUTH_MODE` environment variable:
    ```bash
    export DEBUG_AUTH_MODE=1
    ```

When either of these modes is enabled, a debug panel will be visible in the frontend, allowing you to change the exploration radius, delete hexagons, and clear the database.
