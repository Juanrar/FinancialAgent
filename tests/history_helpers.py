from financial_agent.store.pending import (
    claim_transfer,
    create_action,
    finish_transfer,
    mark_done_manually,
)


def finished_transfer(conn, dest, resolved, text, status="enviada", detail=None, amount=200000):
    """Crea una transferencia y la lleva a un estado final con resolved_at = resolved."""
    payload = {"destination_id": dest.id, "amount_cents": amount, "agent_text": text}
    action = create_action(conn, "transfer", payload, now=resolved)
    claim_transfer(conn, action.id, now=resolved)
    if status == "hecha_a_mano":
        finish_transfer(conn, action.id, "no_salio", detail, now=resolved)
        mark_done_manually(conn, action.id, now=resolved)
    else:
        finish_transfer(conn, action.id, status, detail, now=resolved)
    return action.id
