import asyncio

from database.engine import init_engine, check_connection, close_engine


async def main():
    try:
        init_engine()
        await check_connection()
        print("PostgreSQL ulanishi muvaffaqiyatli!")
    except Exception as exc:
        print(f"Ulanishda xato: {type(exc).__name__}: {exc}")
    finally:
        await close_engine()


if __name__ == "__main__":
    asyncio.run(main())