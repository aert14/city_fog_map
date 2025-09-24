# System Architecture: City Fog Map

This document provides an overview of the technical architecture for the City Fog Map application. Its purpose is to serve as a reference for future development and maintenance.

## 1. High-Level Overview

City Fog Map is a Telegram Mini App designed to let users "unfog" a map by exploring their physical surroundings. It consists of two main parts:

-   **Backend:** A Python-based API built with the FastAPI framework. It handles user authentication, data storage, and the core logic of the application.
-   **Frontend:** A single-page application (SPA) written in vanilla JavaScript. It renders the interactive map using the MapLibre GL library and communicates with the backend to fetch and update user data.

The system is designed to be lightweight and easy to deploy, using a simple SQLite database for data persistence.

## 2. Backend Architecture

The backend is responsible for all server-side operations. It's structured as a modular FastAPI application.

### 2.1. Core Components

-   **`main.py`:** The main entry point for the FastAPI application. It is responsible for initializing the application, setting up middleware (for logging and sessions), serving the frontend, and defining the API endpoints.
-   **`auth.py`:** This module centralizes all user authentication logic. It handles the verification of Telegram's `initData`, manages debug authentication flows (via session cookies), and provides a `get_current_user` dependency that API endpoints can use to require authentication.
-   **`db.py`:** This is the data access layer. It manages all interactions with the SQLite database, including connection handling, schema initialization, and all CRUD (Create, Read, Update, Delete) operations.
-   **`utils.py`:** Contains shared helper functions. Currently, this includes the logic for mapping an exploration radius to an appropriate H3 resolution.

### 2.2. Authentication Flow

Authentication is handled by the `auth.py` module and supports three modes:

1.  **Standard Mode:** In a production environment, the frontend (running inside Telegram) sends an `X-Telegram-Init` header with every API request. The `get_current_user` dependency verifies this `initData` using the bot's secret token to authenticate the user.
2.  **Debug Auth Mode:** When the `DEBUG_AUTH_MODE` environment variable is set, authentication is managed via a session cookie. A debug page (`debug-auth.html`) allows a developer to paste `initData` to create a valid session, making it easier to test API endpoints from a browser.
3.  **No Auth Mode:** When `NO_AUTH_MODE` is set, all authentication is bypassed, and a fixed, local user ID is used for all operations. This is ideal for frontend development when a connection to Telegram is not required.

### 2.3. API Endpoints (`main.py`)

The API is versioned under `/api/v1/`. The main endpoints are:

-   **`POST /api/v1/visit`:** Records that a user has "visited" a location. It takes a latitude and longitude, converts it to an H3 cell based on the user's settings, and stores it.
-   **`GET /api/v1/circles`:** Retrieves the H3 cells of all visited locations for the current user within a specified map bounding box.
-   **`POST /api/v1/radius`:** Allows the user to change their exploration radius. This may trigger a change in the H3 resolution, which results in clearing all previously visited locations.

There are also several debug and system endpoints for health checks and development utilities.

## 3. Frontend Architecture

*(This section will be detailed after the frontend code has been refactored.)*

## 4. Data Model (`db.py`)

The application uses a simple SQLite database with three main tables:

-   **`users`:** Stores basic user information, linking their internal ID to their Telegram ID.
-   **`user_settings`:** Stores individual user preferences, such as the selected exploration radius (`radius_m`) and the corresponding H3 grid resolution (`h3_resolution`).
-   **`circles`:** Stores the primary data: the `geokey` (H3 cell ID) of every location a user has visited. This table is linked to the `users` table.
