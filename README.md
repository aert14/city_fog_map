# City Fog Map

City Fog Map is a Telegram Mini App that allows users to "unfog" a map by exploring their surroundings. It's built with FastAPI on the backend and a vanilla JavaScript frontend using MapLibre GL.

## Features

*   **Telegram Mini App Integration:** Authenticates users securely via their Telegram client.
*   **Interactive Map:** Uses MapLibre GL to display an interactive map.
*   **"Fog of War" Effect:** Unexplored areas are covered in a "fog of war" that users can clear by visiting locations.
*   **Geospatial Indexing:** Uses H3 for efficient storage and retrieval of explored areas.
*   **Debug Mode:** Includes a debug mode for easy development and testing.

## Architecture

The application is built with a microservices architecture using Docker containers:

1.  **Backend Services:** Multiple Python-based services powered by FastAPI:
    - **monolith:** Main web service serving static files and API endpoints
    - **geo-service:** Handles geospatial operations and H3 indexing
    - **user-service:** Manages user authentication and sessions
    - **visit-service:** Processes visit events via RabbitMQ message queue
    - **stats-worker:** Background worker for statistics processing

2.  **Frontend:** A vanilla JavaScript single-page application that runs as a Telegram Mini App. It uses MapLibre GL for rendering the map and interacts with the backend via REST APIs.

3.  **Infrastructure:**
    - **PostgreSQL with PostGIS:** Database for geospatial data
    - **Redis:** Caching and session storage
    - **RabbitMQ:** Message queue for async processing
    - **Nginx:** Reverse proxy and load balancer

The application uses Docker Compose for easy deployment and localtunnel for exposing the local development environment to the internet.

## Getting Started

### Prerequisites

*   Docker and Docker Compose
*   A Telegram Bot Token (get one from [@BotFather](https://t.me/BotFather))
*   Node.js with npm (for localtunnel, if not using Docker)

### Installation

1.  **Clone the repository:**

    ```bash
    git clone https://github.com/your-username/city-fog-map.git
    cd city-fog-map
    ```

### Running the Application

1.  **Set the Telegram Bot Token:**

    ```bash
    export TELEGRAM_BOT_TOKEN="YOUR_TELEGRAM_BOT_TOKEN"
    ```

2.  **Start all services with Docker Compose:**

    ```bash
    make up
    ```

    This will start all services including PostgreSQL, Redis, RabbitMQ, and the application services.

3.  **Check that services are running:**

    ```bash
    make logs
    ```

4.  **Expose your local server to the internet:**

    ```bash
    make tunnel
    ```

    This uses localtunnel to expose port 80 to the internet. Check the tunnel status:

    ```bash
    make tunnel-status
    ```

    Localtunnel will give you a public HTTPS URL (e.g., `https://aert0.loca.lt`).

5.  **Configure your Telegram Bot:**

    *   Talk to [@BotFather](https://t.me/BotFather) on Telegram.
    *   Use the `/setdomain` command to link your bot to the tunnel URL.
    *   Create a menu button for your bot with the URL pointing to your web app (e.g., `https://aert0.loca.lt/webapp/`).

### Available Make Commands

*   `make up` - Start all services
*   `make down` - Stop all services
*   `make build` - Rebuild all services
*   `make logs` - Show logs from all services
*   `make tunnel` - Start localtunnel to expose port 80
*   `make tunnel-status` - Check tunnel status and get URL
*   `make kill` - Stop tunnel and clean up
*   `make clean` - Remove all containers and volumes

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
