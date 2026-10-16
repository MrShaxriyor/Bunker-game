# Para Gifts WebApp

React, TypeScript, Vite, and Tailwind frontend for the Bunker TG Para Gifts API.
The frontend is a separate app and does not change or start the Aiogram bot.

## Configuration

Copy `.env.example` to `.env.local` and set:

```dotenv
VITE_API_BASE_URL=https://api.example.com
```

Use an HTTPS API URL when deployed. For local development only, an API URL
such as `http://127.0.0.1:8000` is allowed. Configure the API's
`PARA_GIFTS_WEBAPP_ORIGIN` to the exact browser origin. For Vite's default
development server, that is `http://localhost:5173`.

The Telegram bot token is only configured on the backend as `BOT_TOKEN`.
Never add it to any `VITE_` variable or frontend source file.

## Run

From the `webapp` directory:

```powershell
npm install
npm run dev
```

The backend API must also be running. The API authenticates requests using
`Authorization: tma <Telegram.WebApp.initData>`. When run outside Telegram in
Vite development mode, the frontend shows clearly labeled local demo data and
does not send API requests. Production requires Telegram WebApp context.

## Validate

```powershell
npm test
npm run build
```
