def parse_comma_separated(value: str) -> list[str]:
    """Split, trim, remove empty values, and preserve unique values in order."""
    return list(dict.fromkeys(filter(None, map(str.strip, value.split(",")))))
