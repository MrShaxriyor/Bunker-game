"""Idempotently seed the Para Gifts catalog without changing existing rows."""

import asyncio
import re
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert

from database.engine import close_engine, get_sessionmaker
from database.models import ShopItem


@dataclass(frozen=True)
class GiftSeed:
    code: str
    name: str
    price_coins: int


GIFTS = (
    GiftSeed("rose", "🌹 Rose", 50),
    GiftSeed("sweet-gift", "🍫 Sweet Gift", 100),
    GiftSeed("teddy-bear", "🧸 Teddy Bear", 250),
    GiftSeed("bouquet", "💐 Bouquet", 500),
    GiftSeed("gold-ring", "💍 Gold Ring", 1000),
    GiftSeed("heart-effect", "❤️‍🔥 Heart Effect", 700),
    GiftSeed("diamond-heart", "💎 Diamond Heart", 2500),
)

_CODE_PATTERN = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}\Z")


class SeedConflict(RuntimeError):
    """A code already exists with catalog values different from this seed."""


def validate_seed_data() -> None:
    codes = [gift.code for gift in GIFTS]
    prices = [gift.price_coins for gift in GIFTS]
    if len(codes) != len(set(codes)):
        raise SeedConflict("Seed gift codes must be unique.")
    if len(prices) != len(set(prices)):
        raise SeedConflict("Seed gift prices must be unique.")
    if any(not _CODE_PATTERN.fullmatch(code) for code in codes):
        raise SeedConflict("A seed gift code does not meet admin code rules.")
    if any(not gift.name.strip() or len(gift.name) > 255 for gift in GIFTS):
        raise SeedConflict("A seed gift name does not meet model limits.")
    if any(type(gift.price_coins) is not int or gift.price_coins <= 0 for gift in GIFTS):
        raise SeedConflict("Seed gift prices must be positive integers.")


async def seed_para_gifts() -> tuple[int, int]:
    """Insert absent gifts; leave exact matches alone and reject conflicts."""
    validate_seed_data()
    inserted = 0
    unchanged = 0

    async with get_sessionmaker()() as session:
        async with session.begin():
            for gift in GIFTS:
                result = await session.execute(
                    postgresql_insert(ShopItem)
                    .values(
                        code=gift.code,
                        name=gift.name,
                        description=None,
                        item_type="para_gift",
                        price_coins=gift.price_coins,
                        is_active=True,
                    )
                    .on_conflict_do_nothing(
                        constraint="uq_shop_items_code",
                    )
                )
                item = await session.scalar(
                    select(ShopItem)
                    .where(ShopItem.code == gift.code)
                    .with_for_update()
                )
                if item is None:
                    raise SeedConflict(
                        f"Could not confirm seeded gift code {gift.code}."
                    )

                mismatches = []
                if item.name != gift.name:
                    mismatches.append("name")
                if item.item_type != "para_gift":
                    mismatches.append("category")
                if item.price_coins != gift.price_coins:
                    mismatches.append("price_coins")
                if item.is_active is not True:
                    mismatches.append("is_active")
                if mismatches:
                    raise SeedConflict(
                        f"Existing gift code {gift.code} conflicts in: "
                        f"{', '.join(mismatches)}. No rows were changed."
                    )

                if result.rowcount == 1:
                    inserted += 1
                else:
                    unchanged += 1

    return inserted, unchanged


async def main() -> int:
    try:
        inserted, unchanged = await seed_para_gifts()
    except SeedConflict as error:
        print(f"Para Gifts seed stopped: {error}")
        return 1
    except Exception as error:
        print(
            "Para Gifts seed failed without exposing connection details "
            f"({type(error).__name__})."
        )
        return 1
    finally:
        await close_engine()

    print(
        "Para Gifts seed complete: "
        f"inserted={inserted}, already_matching={unchanged}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
