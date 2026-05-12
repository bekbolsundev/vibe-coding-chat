# Speed Chat Website (FastAPI + SQLite)

## Description
A basic, testable chat website built as a speed-building project.

It includes:
- User registration
- User list and user search
- Sending and receiving messages
- Chat history per conversation
- Message search and filtering
- SQLite local storage
- Real-time chat updates using WebSocket

## Tech Stack
- Backend: Python, FastAPI
- Database: SQLite
- Frontend: HTML, CSS, Vanilla JavaScript
- Server: Uvicorn

## Project Structure
- `main.py`: FastAPI backend and API endpoints
- `static/index.html`: Frontend UI
- `requirements.txt`: Python dependencies

## Run Locally
1. Create and activate a virtual environment (recommended)

```bash
python3 -m venv .venv
source .venv/bin/activate
```

2. Install dependencies

```bash
pip install -r requirements.txt
```

3. Start the server

```bash
uvicorn main:app --reload
```

4. Open in browser

- http://127.0.0.1:8000/

## API Endpoints
### User Management
- `POST /users/register`
- `GET /users`
- `GET /users/search?q=<text>`

### Messaging
- `POST /messages/send`
- `GET /messages/receive/{user_id}`
- `GET /messages/history?user1_id=<id>&user2_id=<id>`
- `GET /messages/search?q=<text>&user_id=<id>&other_user_id=<id>`
- `GET /messages/filter?user_id=<id>`
- `GET /messages/filter?conversation_user1=<id>&conversation_user2=<id>`
- `WS /ws/{user_id}` for real-time updates

## Notes
- The SQLite file (`chat.db`) is created automatically on startup.
- Open two browser tabs and pick different users to test real-time messaging.

## 🚀 Live Project Links

* **Live Website:** [https://vibe-coding-chat.onrender.com](https://vibe-coding-chat.onrender.com)
* **Video Demo:** [Watch the Demo on YouTube](https://youtu.be/Q8mIT55fBXc)
