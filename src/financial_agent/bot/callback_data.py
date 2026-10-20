def make_callback(prefix: str, action: str, item_id: int | None = None) -> str:
    parts = [prefix, action] if item_id is None else [prefix, action, str(item_id)]
    return ":".join(parts)


def parse_callback(data: str) -> tuple[str, str, int | None]:
    parts = data.split(":")
    if len(parts) == 2:
        return parts[0], parts[1], None
    if len(parts) == 3:
        return parts[0], parts[1], int(parts[2])
    raise ValueError("callback_data inválido")
