# City Fog Map

**City Fog Map** is an interactive "Fog of War" exploration game for Telegram. As you move through the real world, the fog on the map clears, revealing the city around you.

![City Fog Map Demo](https://github.com/user-attachments/assets/placeholder-image.png)

## 🎮 Demo

Try the **Demo Mode** directly in your browser (no Telegram required):
[**Launch Demo**](https://your-username.github.io/city-fog-map/webapp/)

*Note: The demo runs entirely in your browser using local storage. No backend connection is required.*

## ✨ Features

*   **Fog of War:** The world is initially hidden. Explore to uncover it.
*   **Geospatial Indexing:** Efficiently tracks visited areas using H3 hexagonal grids.
*   **Telegram Integration:** Seamlessly integrates with Telegram Mini Apps for authentication and location services.
*   **Interactive Map:** Built with MapLibre GL for smooth, vector-based mapping.
*   **Demo Mode:** A standalone static version for easy testing and showcasing.

## 🏗️ Architecture

The project consists of a microservices backend and a static frontend:

*   **Frontend:** Vanilla JavaScript + MapLibre GL (located in `webapp/`).
*   **Backend:** Python FastAPI services (Monolith, Geo, User, Visit, Stats).
*   **Database:** PostgreSQL with PostGIS for geospatial data.
*   **Infrastructure:** Docker Compose, Redis, RabbitMQ, Nginx.

## 🚀 Getting Started

### Prerequisites

*   Docker & Docker Compose
*   A Telegram Bot Token (from [@BotFather](https://t.me/BotFather))

### Local Development (Full App)

1.  **Clone the repository:**
    ```bash
    git clone https://github.com/your-username/city-fog-map.git
    cd city-fog-map
    ```

2.  **Set your Bot Token:**
    ```bash
    export TELEGRAM_BOT_TOKEN="YOUR_TOKEN_HERE"
    ```

3.  **Start Services:**
    ```bash
    make up
    ```
    This launches the full stack (DB, Backend, Frontend) via Docker Compose.

4.  **Expose to Internet:**
    To test the Telegram Web App, you need a public URL (HTTPS).
    ```bash
    make tunnel
    ```
    Use the provided URL to configure your Telegram Bot's Menu Button.

### Frontend Development (Demo Mode)

To work on the frontend without the backend:

1.  Open `webapp/index.html` in your browser.
2.  The app will automatically detect it's running locally and switch to **Demo Mode**.
3.  Changes to `webapp/` files are reflected immediately on refresh.

## 📦 Deployment

### GitHub Pages (Frontend Only)

The frontend is designed to be deployable to GitHub Pages.
A GitHub Actions workflow is included in `.github/workflows/deploy.yml` to automatically deploy the `webapp/` directory on push to `main`.

### Full Stack

For a full deployment, you will need a server with Docker support (e.g., VPS, DigitalOcean, AWS).
1.  Copy `docker-compose.yml` and `Makefile` to your server.
2.  Set environment variables (`TELEGRAM_BOT_TOKEN`, `DATABASE_URL`, etc.).
3.  Run `make up`.

## 🛠️ Tech Stack

*   **Frontend:** HTML5, CSS3, JavaScript, MapLibre GL JS
*   **Backend:** Python 3.11, FastAPI
*   **Database:** PostgreSQL 15 + PostGIS
*   **Tools:** Docker, H3 (Uber's Hexagonal Hierarchical Spatial Index)

---

*Generated for the City Fog Map Project.*
