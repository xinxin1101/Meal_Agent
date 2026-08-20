import re
from decimal import Decimal


def extract_supported_constraints(query: str) -> dict[str, str]:
    """Deterministic M2 extractor. It exposes observations; it cannot change safety data."""
    extracted: dict[str, str] = {}
    minutes = re.search(r"(?:时间|time)\s*[：:]?\s*(\d+)\s*(?:分钟|min)", query, re.IGNORECASE)
    protein = re.search(r"(?:蛋白(?:质)?|protein)\s*[：:]?\s*(\d+(?:\.\d+)?)\s*g", query, re.IGNORECASE)
    energy = re.search(r"(\d+(?:\.\d+)?)\s*[-~至到]\s*(\d+(?:\.\d+)?)\s*kcal", query, re.IGNORECASE)
    if minutes:
        extracted["max_total_minutes"] = minutes.group(1)
    if protein:
        extracted["protein_min_g"] = str(Decimal(protein.group(1)))
    if energy:
        extracted["energy_kcal_range"] = f"{Decimal(energy.group(1))}-{Decimal(energy.group(2))}"
    return extracted
